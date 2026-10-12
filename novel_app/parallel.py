"""Bounded book workers, per-site pacing, and controls with explicit job identity."""
import threading
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from novel_get.fetch import DownloadError, SiteBlocked
from . import network
from .core import Runner
from .sites import list_books


class TaskControl(network.Control):
    def __init__(self, parent, item, url):
        self.parent = parent
        self.item, self.url = item, url
        super().__init__(lambda **event: parent.emit(**{'item': item, 'job_url': url, **event}))

    def is_cancelled(self):
        return self.cancel.is_set() or self.parent.cancel.is_set()

    def is_paused(self):
        return self.paused.is_set() or self.parent.paused.is_set()

    def checkpoint(self):
        while True:
            if self.is_cancelled():
                raise network.Cancelled('任务已停止，已保存进度')
            if not self.is_paused():
                return
            self.cancel.wait(.1)


class ChapterControl(network.Control):
    """Keep row identity and book controls, but give fallback tabs unique keys."""
    def __init__(self, parent, key, stopped=None):
        super().__init__(parent.emit)
        self.parent, self.item, self.url = parent, parent.item, parent.url
        self.stopped = stopped
        self.page_key = key
        self.cancel, self.paused = parent.cancel, parent.paused
        self.approved, self.show_browser = parent.approved, parent.show_browser

    def is_cancelled(self):
        return self.parent.is_cancelled() or bool(self.stopped and self.stopped.is_set())

    def is_paused(self):
        return self.parent.is_paused()

    def checkpoint(self):
        while True:
            if self.is_cancelled():
                raise network.Cancelled('章节队列已停止，已保存完成的章节')
            if not self.is_paused():
                return
            self.cancel.wait(.1)


class SiteGate:
    def __init__(self, site, control, *, limit=2, adaptive=True, delay=3, pause_every=30, pause_seconds=15):
        self.site, self.control = site, control
        self.maximum = max(1, min(4, int(limit)))
        self.adaptive = adaptive
        self.limit = 1 if adaptive else self.maximum
        self.delay = max(0, float(delay))
        self.pause_every, self.pause_seconds = max(1, pause_every), pause_seconds
        self.condition = threading.Condition()
        self.active = self.count = self.successes = 0
        self.inflight = set()
        self.next_start = 0
        self.latency = None
        self.verifying = False
        self.verification_count = 0
        self.blocked = None
        self.denied = None
        self.blocked_error = None
        self.restricted = False
        self.rate_recovering = False
        self.rate_generation = self.recovered_generation = 0
        self.rate_floor = 3.0
        self.effective_delay = self.delay

    def acquire(self, control):
        notice = 0
        while True:
            control.checkpoint()
            with self.condition:
                if self.blocked:
                    self.raise_blocked()
                now = time.monotonic()
                if not self.verifying and not self.rate_recovering and self.active < self.limit:
                    self.active += 1
                    return
                if now - notice >= 5:
                    control.emit(kind='wait', message=f'{self.site}：' + ('等待同站限流恢复' if self.rate_recovering else '等待同站验证完成' if self.verifying else f'等待站点限速/并发槽位（当前 {self.limit}/{self.maximum}）'))
                    notice = now
                self.condition.wait(.1)

    def _start_ready(self, control, recovery=False, verification_owner=False):
        now=time.monotonic()
        if (self.verifying and not verification_owner) or (self.rate_recovering and not recovery) or now < self.next_start or len(self.inflight) >= self.limit:
            return False
        self.inflight.add(id(control))
        self.count+=1
        interval=max(self.effective_delay,self.pause_seconds) if self.count % self.pause_every==0 else self.effective_delay
        self.next_start=now+interval
        return True

    def start(self, control):
        notice=0
        while True:
            control.checkpoint()
            with self.condition:
                if self.blocked:self.raise_blocked()
                if self._start_ready(control):
                    control.request_started=time.monotonic()
                    return
                now=time.monotonic()
                if now-notice>=5:
                    control.emit(kind='wait',message=f'{self.site}：网页请求间隔，剩余 {max(1,int(self.next_start-now))} 秒')
                    notice=now
                self.condition.wait(.1)

    async def start_async(self, control, recovery=False, verification_owner=False):
        notice=0
        while True:
            if control.is_cancelled():raise network.Cancelled('任务已停止，已保存进度')
            if not control.is_paused():
                with self.condition:
                    if self.blocked:self.raise_blocked()
                    if self._start_ready(control, recovery, verification_owner):
                        control.request_started=time.monotonic()
                        return
                    now=time.monotonic()
                    if now-notice>=5:
                        reason='同站限流冷却/恢复' if self.rate_recovering else '网页请求间隔'
                        control.emit(kind='wait',message=f'{self.site}：{reason}，剩余 {max(1,int(self.next_start-now))} 秒')
                        notice=now
            await asyncio.sleep(.1)

    def end_request(self, control):
        with self.condition:
            self.inflight.discard(id(control))
            self.condition.notify_all()

    def release(self, latency, success=True, control=None, *, page_error=False):
        with self.condition:
            if control is not None:
                self.inflight.discard(id(control))
            self.active -= 1
            old = self.limit
            if page_error:
                # Missing resources do not imply a site-wide rate restriction,
                # and are not successful samples for adaptive acceleration.
                self.successes = 0
            elif not success:
                self.successes = 0
                self.limit = 1
                self.next_start = max(self.next_start, time.monotonic() + max(self.delay, 2))
            elif self.adaptive and not self.restricted:
                slow = self.latency is not None and latency > max(5, self.latency * 2)
                self.latency = latency if self.latency is None else .8 * self.latency + .2 * latency
                if slow:
                    self.successes = 0
                    self.limit = max(1, self.limit - 1)
                else:
                    self.successes += 1
                    # Extra tabs only help when page service time exceeds pacing.
                    # Eight quick successes alone are not evidence for more load.
                    if self.successes >= 32 and self.limit < self.maximum and self.latency > self.effective_delay:
                        self.limit += 1
                        self.successes = 0
            self.condition.notify_all()
        if old != self.limit:
            self.control.emit(kind='concurrency', site=self.site, limit=self.limit, maximum=self.maximum,
                              message=f'{self.site}：站点并发调整为 {self.limit}/{self.maximum}')

    def verification(self, value):
        with self.condition:
            self.verification_count = max(0, self.verification_count + (1 if value else -1))
            self.verifying = self.verification_count > 0
            if value:
                self.limit, self.successes = 1, 0
                self.restricted = True
            self.condition.notify_all()

    def rate_limit(self, seconds):
        with self.condition:
            if not self.rate_recovering:
                self.rate_generation += 1
            self.rate_recovering = self.restricted = True
            self.limit, self.successes = 1, 0
            self.effective_delay = max(self.delay, self.rate_floor)
            self.next_start = max(self.next_start, time.monotonic() + max(0, seconds))
            generation = self.rate_generation
            self.condition.notify_all()
        return generation

    def finish_rate_recovery(self, recovered=True):
        with self.condition:
            if recovered:
                self.recovered_generation = self.rate_generation
            self.rate_recovering = False
            self.condition.notify_all()

    def block(self, message):
        with self.condition:
            self.blocked = str(message)
            self.denied = (message.status, message.url) if isinstance(message, network.AccessDenied) else None
            self.blocked_error = (type(message), getattr(message, 'status', None), getattr(message, 'url', None)) if isinstance(message, SiteBlocked) else None
            self.limit, self.successes = 1, 0
            self.condition.notify_all()

    def raise_blocked(self):
        if self.denied:
            raise network.AccessDenied(*self.denied)
        if self.blocked_error:
            kind, status, url = self.blocked_error
            if issubclass(kind, network.StopWithoutVerification):
                raise kind(self.blocked, status, url)
            raise kind(self.blocked)
        raise SiteBlocked(self.blocked)


class JobFetcher:
    def __init__(self, batch, control, site, mode, profile):
        self.batch, self.control, self.site = batch, control, site
        self.mode, self.profile = mode, profile
        self.gate = batch.gate(site)
        self.http = None
        self.lightweight = False
        self.transport_key = None
        self.chapter_workers = max(1, min(4, int(batch.opts.get('chapter_concurrency', 3))))
        if mode == 'http':
            self.http = network.Fetcher(control, proxy=batch.opts['proxy'])
            # HTTP retries and redirects still share the site's request budget.
            self.http.pace = self._http_pace

    def _http_pace(self):
        # The first request already holds a gate slot; every additional retry or
        # redirect has to wait for the same site's minimum start interval.
        if not self._http_first:
            with self.gate.condition:
                self.gate.limit,self.gate.successes=1,0
        self._http_first = False
        self.gate.end_request(self.control)
        self.gate.start(self.control)

    def get(self, url, referer=None):
        network.validate_url(url, resolve=True)
        self.gate.acquire(self.control)
        self._held, self._http_first = True, True
        started = time.monotonic()
        success = False
        page_error = False
        try:
            if self.http:
                page = self.http.get(url, referer)
            elif self.lightweight:
                page = self.batch.browser.request(self.site, self.profile, self.control, self.gate,
                                                  url, referer, self.transport_key)
            else:
                if url == self.control.url:
                    page = self.batch.browser.get(self.site, self.profile, self.control, self.gate, url, referer)
                else:
                    page = self.batch.browser.request(self.site, self.profile, self.control, self.gate,
                                                      url, referer, self.control.item, purpose='toc')
            self.control.checkpoint()
            success = True
            return page
        except network.PageHTTPError as exc:
            page_error = exc.status in {404, 410}
            raise
        except SiteBlocked as exc:
            self.gate.block(exc)
            raise
        finally:
            if self._held:
                self.gate.release(time.monotonic() - getattr(self.control,'request_started',started), success, self.control,
                                  page_error=page_error)

    def chapter_fetcher(self, slot, stopped=None):
        child = JobFetcher(self.batch, ChapterControl(self.control, f'{self.control.item}:chapter:{slot}', stopped),
                           self.site, self.mode, self.profile)
        child.lightweight = self.mode == 'browser'
        child.transport_key = self.control.item
        return child

    def close(self):
        if self.http:
            self.http.__exit__(None, None, None)
        elif self.batch._browser:
            self.batch.browser.release(self.site, getattr(self.control, 'page_key', self.control.item))


class ParallelBatch:
    def __init__(self, control, opts, profile_for, site_for, browser_factory=None):
        self.control, self.opts = control, opts
        self.profile_for, self.site_for = profile_for, site_for
        self.books = max(1, min(8, int(opts.get('books', 3))))
        self.tasks = {}
        self.gates = {}
        self.lock = threading.Lock()
        self._browser = None
        self.browser_factory = browser_factory

    @property
    def browser(self):
        with self.lock:
            if self._browser is None:
                from .browser_parallel import BrowserBroker
                factory = self.browser_factory or BrowserBroker
                self._browser = factory(max_sites=self.books, headed=True)
            return self._browser

    def gate(self, site):
        with self.lock:
            if site not in self.gates:
                self.gates[site] = SiteGate(site, self.control, limit=self.opts.get('site_concurrency', 2),
                                          adaptive=self.opts.get('adaptive', True), delay=self.opts['delay'],
                                          pause_every=self.opts['pause_every'], pause_seconds=self.opts['pause_seconds'])
            return self.gates[site]

    def command(self, items, action):
        for item in items:
            control = self.tasks.get(item)
            if control is None:
                continue
            if action == 'stop':
                control.cancel.set()
            elif action == 'pause':
                if control.paused.is_set():
                    control.paused.clear()
                    control.emit(kind='job_status', status='继续中', message='选中任务已继续')
                else:
                    control.paused.set()
                    control.emit(kind='job_status', status='已暂停', message='选中任务将在当前请求结束后暂停')
            elif action == 'show':
                control.show_browser.set()
            elif action == 'approve':
                control.approved.set()

    def _job(self, item, url, action):
        control = self.tasks[item]
        fetcher = None
        try:
            control.checkpoint()
            host = urlsplit(url).hostname
            site = self.site_for(host)
            gate = self.gate(site)
            if gate.blocked:
                gate.raise_blocked()
            control.emit(kind='active', url=url, message='开始处理')
            if action == 'verify':
                control.show_browser.set()
            profile = self.profile_for(host)
            mode = 'browser' if action == 'verify' or self.opts['mode'] == '浏览器模式' or (
                self.opts['mode'] == '自动选择' and (profile.exists() or site == '41nr.com')) else 'http'
            fetcher = JobFetcher(self, control, site, mode, profile)
            if action == 'list':
                books = list_books(fetcher.get(url))
                control.emit(kind='books', books=books, message=f'发现 {len(books)} 本候选小说')
            else:
                Runner(fetcher, self.opts['output'], control, limit=3 if action == 'sample' else 0,
                       refresh=self.opts['refresh']).run(url)
        except network.Cancelled as exc:
            control.emit(kind='job_status', status='已停止', message=str(exc))
        except SiteBlocked as exc:
            self.gate(self.site_for(urlsplit(url).hostname)).block(exc)
            control.emit(kind='job_status', status=network.blocked_status(exc), message=str(exc),
                         error_url=getattr(exc, 'url', None), error_status=getattr(exc, 'status', None),
                         error_chapter_title=getattr(exc, 'chapter_title', None))
        except Exception as exc:
            control.emit(kind='job_status', status='需处理', message=str(exc))
        finally:
            if fetcher:
                try:
                    fetcher.close()
                except Exception as exc:
                    control.emit(kind='warning', message=f'会话清理失败：{exc}')
                with fetcher.gate.condition:
                    if fetcher.gate.active == 0 and fetcher.gate.rate_recovering:
                        fetcher.gate.finish_rate_recovery(False)
            control.emit(kind='job_finished', message='当前小说处理结束，进度已保留')

    def run(self, jobs, action='download'):
        self.tasks = {item: TaskControl(self.control, item, url) for item, url in jobs}
        for item, url in jobs:
            self.tasks[item].emit(kind='job_status', status='排队中', message='进入并行队列')
        try:
            # Fill bounded slots instead of submitting hundreds of waiting futures.
            with ThreadPoolExecutor(max_workers=self.books, thread_name_prefix='novel-book') as executor:
                pending = {}
                # Give different sites an initial slot before filling more slots
                # from one site that may be waiting for a human verification.
                groups = OrderedDict()
                for job in jobs:
                    groups.setdefault(self.site_for(urlsplit(job[1]).hostname),deque()).append(job)
                active_sites = set()
                while pending or groups:
                    if self.control.cancel.is_set():
                        for group in groups.values():
                            for item, _ in group:
                                self.tasks[item].emit(kind='job_status', status='已停止', message='排队任务已停止')
                        groups.clear()
                    for site in list(groups):
                        if len(pending) >= self.books:
                            break
                        if site in active_sites:
                            continue
                        item, url = groups[site].popleft()
                        if not groups[site]:
                            del groups[site]
                        pending[executor.submit(self._job, item, url, action)] = site
                        active_sites.add(site)
                    if pending:
                        done, _ = wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
                        for future in done:
                            active_sites.discard(pending.pop(future))
                            future.result()
        finally:
            if self._browser:
                self._browser.close()

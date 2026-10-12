"""Own all async Playwright objects on one loop; workers receive plain Page data."""
import asyncio
import threading
import time
import math
import re
from contextlib import asynccontextmanager
from email.utils import parsedate_to_datetime
from collections import OrderedDict
from concurrent.futures import TimeoutError
from pathlib import Path
from urllib.parse import urlsplit

from novel_get.fetch import Page, DownloadError, SiteBlocked
from . import network
from .sites import site_config, guard_body, extract_readable
from .response import StopWithoutVerification, RateLimited, ServerUnavailable, check_destination, visible_soup


LIGHTWEIGHT_GET = r"""async (url) => {
    if (new URL(url).origin !== location.origin) return {error: 'different-origin'};
    const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 20000);
    try {
        // Local validated chapter/page files handle resume. Browser HTTP cache
        // can retain a transient denial for weeks, so every paced missing-page
        // read must contact the server and replace that old response.
        const response = await fetch(url, {credentials: 'include', redirect: 'error', cache: 'reload', signal: abort.signal});
        if (!response.body) return {url: response.url, code: response.status, html: '', type: response.headers.get('content-type') || '', challenge: response.headers.get('cf-mitigated') === 'challenge', retry_after: response.headers.get('retry-after')};
        const reader = response.body.getReader(), chunks = [];
        let size = 0;
        while (true) {
            const part = await reader.read();
            if (part.done) break;
            size += part.value.length;
            if (size > 12000000) { await reader.cancel(); return {error: 'too-large'}; }
            chunks.push(part.value);
        }
        const bytes = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
        const type = response.headers.get('content-type') || '';
        const prefix = new TextDecoder('windows-1252').decode(bytes.slice(0, 4096));
        const match = /charset\s*=\s*["']?([\w-]+)/i.exec(type) || /charset\s*=\s*["']?([\w-]+)/i.exec(prefix);
        let decoder;
        try { decoder = new TextDecoder(match ? match[1] : 'utf-8'); } catch (_) { decoder = new TextDecoder('utf-8'); }
        return {url: response.url, code: response.status, html: decoder.decode(bytes), type,
                challenge: response.headers.get('cf-mitigated') === 'challenge',
                retry_after: response.headers.get('retry-after'),
                cache_control: response.headers.get('cache-control'),
                cache_status: response.headers.get('cf-cache-status')};
    } catch (_) { return {error: 'request-failed'}; }
    finally { clearTimeout(timer); }
}"""


CACHED_DENIAL_HOSTS = frozenset({'m.41nr.com', 'www.41nr.com', '41nr.com'})


def cached_denial_delay(attempt):
    return 60 * 2 ** attempt


def recoverable_cached_denial(data, url):
    """Only the twice-observed public 41nr cache error, not arbitrary 403s."""
    if (data.get('code') != 403 or data.get('challenge')
            or urlsplit(url).hostname not in CACHED_DENIAL_HOSTS
            or (data.get('cache_status') or '').upper() != 'HIT'):
        return False
    match = re.search(r'(?:^|,)\s*max-age\s*=\s*(\d+)', data.get('cache_control') or '', re.I)
    if not match or int(match[1]) <= 0 or len(data.get('html', '')) > 2000:
        return False
    try:
        check_destination(Page(data['url'], data['html']), url)
    except DownloadError:
        return False
    text = visible_soup(data['html']).get_text(' ', strip=True)
    return bool(re.fullmatch(r'(?:403\s+Forbidden\s+){1,2}nginx', text, re.I))


def retry_after_seconds(value, fallback=30):
    """Honor both Retry-After forms; malformed values use bounded backoff."""
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError
        return seconds
    except (ValueError, TypeError):
        try:
            stamp = parsedate_to_datetime(value).timestamp()
            return max(0, stamp - time.time())
        except (ValueError, TypeError, OverflowError, AttributeError):
            return fallback


async def checkpoint(control):
    while True:
        if control.is_cancelled():
            raise network.Cancelled('任务已停止，已保存进度')
        if not control.is_paused():
            return
        await asyncio.sleep(.1)


@asynccontextmanager
async def controlled_lock(lock, control):
    while True:
        await checkpoint(control)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=.2)
            break
        except asyncio.TimeoutError:
            pass
    try:
        await checkpoint(control)
        yield
    finally:
        lock.release()


@asynccontextmanager
async def recovery_lock(lock, control, verification_owner):
    # An explicit verification owner must be able to reload its page while a
    # rate-recovery worker waits for verification. The gate still limits every
    # request, so this avoids a lock cycle without allowing a traffic burst.
    if verification_owner:
        await checkpoint(control)
        yield
    else:
        async with controlled_lock(lock, control):
            yield


class TransportControl:
    def __init__(self, control, key):
        self.control, self.page_key = control, key

    def __getattr__(self, name):
        return getattr(self.control, name)


class BrowserBroker:
    def __init__(self, max_sites=3, headed=True):
        self.max_sites, self.headed = max_sites, headed
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._serve, name='novel-browser', daemon=False)
        self.thread.start()
        if not self.ready.wait(5):
            raise DownloadError('浏览器调度线程未能启动')

    def _serve(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.sessions = OrderedDict()
        self.session_lock = asyncio.Lock()
        self.runtime = None
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    def call(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        while True:
            try:
                return future.result(timeout=1)
            except TimeoutError:
                if future.done():
                    return future.result()
                if not self.thread.is_alive():
                    raise DownloadError('浏览器调度线程意外退出')
                # Do not cancel an in-flight Playwright operation. The async
                # operation checks cancellation before/after bounded calls.

    async def _route(self, route, approved):
        try:
            request = route.request
            parts = urlsplit(request.url)
            if parts.scheme not in {'http', 'https'}:
                return await route.abort()
            if parts.hostname not in approved:
                await asyncio.to_thread(network.validate_url, request.url, resolve=True)
                approved.add(parts.hostname)
            if request.resource_type == 'media':
                return await route.abort()
            await route.continue_()
        except Exception:
            try:
                await route.abort()
            except Exception:
                pass

    async def _session(self, site, profile, control):
        async with self.session_lock:
            if site in self.sessions:
                if self.sessions[site].get('closed'):
                    raise SiteBlocked('该站专用浏览器已关闭，请重新开始该站任务')
                self.sessions.move_to_end(site)
                return self.sessions[site], True
            if len(self.sessions) >= self.max_sites:
                idle = next((name for name, session in self.sessions.items() if not session['pages']), None)
                if idle is None:
                    raise DownloadError('浏览器站点槽位已满，请降低同时下载书数')
                previous = self.sessions.pop(idle)
                await previous['context'].close()
            await checkpoint(control)
            control.emit(kind='session', site=site, reused=False, message=f'{site}：启动共享浏览器会话')
            if self.runtime is None:
                from playwright.async_api import async_playwright
                self.runtime = await async_playwright().start()
            profile = Path(profile)
            profile.mkdir(parents=True, exist_ok=True)
            context = None
            for channel in ('msedge', 'chrome'):
                await checkpoint(control)
                try:
                    context = await self.runtime.chromium.launch_persistent_context(
                        str(profile.resolve()), channel=channel, headless=not self.headed,
                        no_viewport=True, accept_downloads=False, locale='zh-CN', timeout=30000,
                        args=['--start-minimized'] if self.headed else [])
                    break
                except Exception:
                    pass
            if context is None:
                raise SiteBlocked('专用浏览器无法启动或仍被其他任务占用；请在原任务结束后使用新版')
            approved = set()
            await context.route('**/*', lambda route: self._route(route, approved))
            idle_pages = list(context.pages)
            if self.headed and idle_pages:
                await self._window(context, idle_pages[0], 'minimized')
            session = dict(context=context, pages={}, idle=idle_pages, verification=asyncio.Lock(),closed=False)
            context.on('close',lambda:session.update(closed=True))
            self.sessions[site] = session
            return session, False

    async def _window(self, context, page, state):
        session = await context.new_cdp_session(page)
        try:
            window = await session.send('Browser.getWindowForTarget')
            await session.send('Browser.setWindowBounds', {'windowId': window['windowId'], 'bounds': {'windowState': state}})
            if state == 'normal':
                await page.bring_to_front()
        finally:
            await session.detach()

    async def _readable(self, page, status, url):
        result = Page(page.url, await page.content())
        network.check_page_response(result, status.get('code'), status.get('challenge', False))
        check_destination(result, url)
        selector = site_config(url).get('content_selector')
        if selector:
            guard_body(result, selector)
            path = urlsplit(url).path.strip('/').split('/')
            chapter = path[0] == 'chapter' or (path[0] == 'novel' and len(path) >= 3)
            if chapter:
                extract_readable(result, site_config(url))
        return result

    async def _ready_content(self, page, status, url, control):
        # Wait only for content that is still being populated/decrypted by the
        # site's own script. Static pages incur no unconditional sleep.
        deadline = time.monotonic() + 2
        while True:
            await checkpoint(control)
            try:
                return await self._readable(page, status, url)
            except (SiteBlocked, DownloadError) as exc:
                pending = any(word in str(exc) for word in ('尚未恢复可读', '仍是编码数据'))
                if not pending or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(.1)

    async def _recover_rate(self, session, page, control, gate, status, url, attempts, verification_owner=False, retry=None):
        if not status.get('rate_generation'):
            return
        # One probe owns recovery. Other tabs reuse its result, with no second
        # cooldown for an old 429 response. Waiting for the lock is cancellable.
        async with recovery_lock(session.setdefault('rate_lock', asyncio.Lock()), control, verification_owner):
            recovered = False
            try:
                while (status.get('code') == 429 or status.get('cached_denial')) and not status.get('challenge'):
                    cached_denial = bool(status.get('cached_denial'))
                    maximum = 2 if cached_denial else 3
                    if attempts[0] >= maximum:
                        if cached_denial:
                            control.emit(kind='info', url=url, message=f'{gate.site}：缓存403冷却重读已达2次，仍被拒绝；结束本站本轮，断点保留')
                            raise network.AccessDenied(403, url)
                        raise RateLimited('HTTP 429 连续限流；已停止该站本轮任务并保留断点，请稍后再继续', 429, url)
                    if status.get('retry_seconds', 0) > 300:
                        if cached_denial:
                            control.emit(kind='info', url=url, message=f'{gate.site}：服务器要求等待超过自动恢复窗口；本站暂停，断点保留')
                            raise network.AccessDenied(403, url)
                        raise RateLimited(f'HTTP 429：服务器要求等待 {status["retry_seconds"]:.0f} 秒；本站暂停，断点保留，请稍后继续', 429, url)
                    if status.get('rate_generation', 0) <= gate.recovered_generation:
                        control.emit(kind='info', message=f'{gate.site}：复用同站限流恢复，重读受限页面')
                    else:
                        if not gate.rate_recovering:
                            gate.rate_limit(status.get('retry_seconds', 30))
                        control.emit(kind='job_status', status='访问冷却' if cached_denial else '限流冷却', url=url,
                                     message=(f'{gate.site}：已识别缓存403，等待 {status.get("retry_seconds", 60):.0f} 秒后重读 {attempts[0]+1}/2；同站暂停，其他站点继续'
                                              if cached_denial else f'{gate.site}：HTTP 429，自动冷却后重试；同站保持单并发，其他站点继续'))
                    await gate.start_async(control, recovery=True, verification_owner=verification_owner)
                    attempts[0] += 1
                    status.clear()
                    control.emit(kind='request', url=url, message=f'{gate.site}：' + (f'缓存403冷却重读 {attempts[0]}/2' if cached_denial else f'限流恢复重试 {attempts[0]}/3'))
                    try:
                        if retry:
                            await retry()
                        else:
                            await page.reload(wait_until='domcontentloaded', timeout=25000)
                    finally:
                        gate.end_request(control)
                    await checkpoint(control)
                recovered = True
            except SiteBlocked as exc:
                gate.block(exc)
                raise
            finally:
                gate.finish_rate_recovery(recovered)
            control.emit(kind='job_status', status='继续中', message=f'{gate.site}：限流冷却结束；本轮保持单并发')

    async def _get(self, site, profile, control, gate, url, referer):
        session, reused = await self._session(site, profile, control)
        context = session['context']
        key = getattr(control, 'page_key', control.item)
        if key not in session['pages']:
            page = session['idle'].pop() if session['idle'] else await context.new_page()
            session['pages'][key] = page
            page.on('popup', lambda popup: asyncio.create_task(popup.close()))
            control.emit(kind='session', site=site, reused=reused,
                         message=f'{site}：' + ('复用本批浏览器会话，共享登录与验证状态' if reused else '使用共享会话的任务标签页'))
        page = session['pages'][key]
        status = {}
        rate_attempt = [0]
        def navigation_started():
            # Native navigations made by the user need fresh response metadata,
            # too. Never carry a previous document's challenge into a 403 page.
            status.clear()
        def response_received(response):
            if response.request.resource_type == 'document' and response.frame == page.main_frame:
                status['code'] = response.status
                if not getattr(response, 'status_only', False):
                    status['challenge'] = response.headers.get('cf-mitigated') == 'challenge'
                if response.status == 429 and not status.get('challenge') and not status.get('rate_generation'):
                    seconds = retry_after_seconds(response.headers.get('retry-after'), 30 * 2 ** rate_attempt[0])
                    status['rate_generation'] = gate.rate_limit(seconds)
                    status['retry_seconds'] = seconds
        page.on('response', response_received)
        page.on('navigation_start', navigation_started)
        try:
            await checkpoint(control)
            await gate.start_async(control)
            control.emit(kind='request', url=url, message='开始读取网页（同站共享请求间隔）')
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=25000, referer=referer)
            finally:
                gate.end_request(control)
            await checkpoint(control)
            await self._recover_rate(session, page, control, gate, status, url, rate_attempt)
            try:
                await self._ready_content(page, status, url, control)
            except SiteBlocked as blocked:
                if isinstance(blocked, StopWithoutVerification):
                    raise
                control.emit(kind='access_blocked', url=url, status=status.get('code'),
                             challenge=status.get('challenge', False), message=str(blocked))
                if not self.headed:
                    raise
                # A second challenged tab waits for the owner tab's verification,
                # then reloads with the same browser's updated site cookies.
                gate.verification(True)
                prompted = False
                try:
                    async with controlled_lock(session['verification'], control):
                        await checkpoint(control)
                        if reused:
                            status.clear()
                            await gate.start_async(control, recovery=gate.rate_recovering, verification_owner=True)
                            try:
                                await page.reload(wait_until='domcontentloaded', timeout=25000)
                            finally:
                                gate.end_request(control)
                            await self._recover_rate(session, page, control, gate, status, url, rate_attempt, verification_owner=True)
                        try:
                            await self._ready_content(page, status, url, control)
                        except SiteBlocked as blocked:
                            if isinstance(blocked, StopWithoutVerification):
                                raise
                            prompted = True
                            control.approved.clear()
                            control.emit(kind='manual', site=site, url=url,
                                         message=f'{site}：等待登录/真人验证，同站其他任务暂停；完成后自动继续')
                            notice = time.monotonic()
                            while True:
                                await checkpoint(control)
                                if control.show_browser.is_set():
                                    control.show_browser.clear()
                                    await self._window(context, page, 'normal')
                                try:
                                    await self._readable(page, status, url)
                                    break
                                except SiteBlocked as blocked:
                                    if isinstance(blocked, StopWithoutVerification):
                                        raise
                                    control.approved.clear()
                                if time.monotonic() - notice >= 30:
                                    control.emit(kind='waiting_manual', url=url, message='仍在等待验证，会话和进度保留；其他站点继续')
                                    notice = time.monotonic()
                                await asyncio.sleep(.5)
                finally:
                    gate.verification(False)
                    if prompted:
                        control.emit(kind='manual_done', message='验证等待已结束，按当前页面状态处理任务')
            await checkpoint(control)
            selector = site_config(url).get('content_selector')
            if selector and '/novel/' in url and len(urlsplit(url).path.rstrip('/').split('/')) >= 4:
                await page.wait_for_selector(selector, state='visible', timeout=10000)
            return Page(page.url, await page.evaluate(network.RENDERED_DOM))
        except (network.Cancelled, DownloadError):
            raise
        except Exception as exc:
            raise DownloadError(f'浏览器读取失败：{type(exc).__name__}；缓存已保留') from exc
        finally:
            page.remove_listener('response', response_received)
            page.remove_listener('navigation_start', navigation_started)

    def get(self, site, profile, control, gate, url, referer=None):
        return self.call(self._get(site, profile, control, gate, url, referer))

    async def _request(self, site, profile, control, gate, url, referer, transport_key, purpose):
        session, _ = await self._session(site, profile, control)
        page = session['pages'].get(transport_key)
        if not page:
            async with controlled_lock(session.setdefault('transport_lock', asyncio.Lock()), control):
                page = session['pages'].get(transport_key)
                if not page:
                    # A complete saved directory may skip all book-page reads.
                    # Establish one origin page for fetch() in that case, shared
                    # by every chapter worker; never open each chapter to seed it.
                    seed = TransportControl(control, transport_key)
                    await self._get(site, profile, seed, gate, control.url, None)
                    page = session['pages'][transport_key]
        source, target = urlsplit(page.url if page else ''), urlsplit(url)
        if not page or (source.scheme, source.netloc) != (target.scheme, target.netloc):
            return await self._get(site, profile, control, gate, url, referer)
        if purpose == 'chapter' and session.get('raw_checks', {}).get(target.netloc) is False:
            return await self._get(site, profile, control, gate, url, referer)
        status, attempts, data = {}, [0], {}

        async def read():
            nonlocal data
            data = await page.evaluate(LIGHTWEIGHT_GET, url)
            if data.get('error') == 'too-large':
                raise DownloadError('单页超过 12 MB，已停止以避免异常下载')
            if data.get('error'):
                return
            status.update(code=data['code'], challenge=data['challenge'])
            if data['code'] == 429 and not data['challenge']:
                seconds = retry_after_seconds(data.get('retry_after'), 30 * 2 ** attempts[0])
                status.update(rate_generation=gate.rate_limit(seconds), retry_seconds=seconds)
            elif recoverable_cached_denial(data, url):
                seconds = max(cached_denial_delay(attempts[0]), retry_after_seconds(data.get('retry_after'), 0))
                status.update(cached_denial=True, rate_generation=gate.rate_limit(seconds), retry_seconds=seconds)

        await gate.start_async(control)
        control.emit(kind='request', url=url, transport='session-http', message='后台 HTTP 读取网页，不打开章节页面')
        try:
            await read()
        finally:
            gate.end_request(control)
        await checkpoint(control)
        await self._recover_rate(session, page, control, gate, status, url, attempts, retry=read)
        try:
            if data.get('error'):
                raise SiteBlocked('会话内 HTTP 请求未取得可读正文')
            network.check_page_response(Page(data['url'], data['html']), data['code'], data['challenge'])
            if data['type'] and not any(kind in data['type'].lower() for kind in ('html', 'text/plain', 'xml')):
                raise DownloadError('返回内容不是网页')
            if data['url'].split('#')[0].rstrip('/') != url.split('#')[0].rstrip('/'):
                check_destination(Page(data['url'], data['html']), url)
            result = Page(data['url'], data['html'])
            config = site_config(url)
            guard_body(result, config.get('content_selector'))
            # Validate raw extraction before selecting the lightweight path.
            # JS-only content falls back to the already established browser.
            from .sites import normalize_page
            from .core import config_without_toc
            from novel_get.parse import extract_chapter, toc_refs, identity
            if purpose == 'toc':
                if not toc_refs(result, config.get('toc_selector')):
                    raise DownloadError('后台目录未识别到章节')
            else:
                raw = extract_readable(normalize_page(result), config)
                if not config.get('content_selector'):
                    checks = session.setdefault('raw_checks', {})
                    host = target.netloc
                    async with controlled_lock(session.setdefault('raw_check_lock', asyncio.Lock()), control):
                        if host not in checks:
                            rendered = await self._get(site, profile, control, gate, url, referer)
                            parsed = extract_readable(normalize_page(rendered), config)
                            checks[host] = identity(raw.title) == identity(parsed.title) and raw.text == parsed.text
                            control.emit(kind='transport', message='未知站点首次正文已比对排版；' + ('后续使用后台 HTTP' if checks[host] else '继续使用浏览器渲染'))
                            return rendered
                        if not checks[host]:
                            return await self._get(site, profile, control, gate, url, referer)
            return result
        except (network.PageHTTPError, StopWithoutVerification):
            # No browser fallback, verification lock or repeated 404 request.
            raise
        except (SiteBlocked, DownloadError):
            control.emit(kind='transport', url=url, message='后台正文需渲染/确认，回退到专用浏览器；请求仍按同站限速')
            return await self._get(site, profile, control, gate, url, referer)

    def request(self, site, profile, control, gate, url, referer=None, transport_key=None, purpose='chapter'):
        return self.call(self._request(site, profile, control, gate, url, referer, transport_key, purpose))

    async def _release(self, site, item):
        session = self.sessions.get(site)
        if session:
            page = session['pages'].pop(item, None)
            if page and not page.is_closed():
                session['idle'].append(page)

    def release(self, site, item):
        return self.call(self._release(site, item))

    async def _close(self):
        try:
            for session in self.sessions.values():
                try:
                    await session['context'].close()
                except Exception:
                    pass
            self.sessions.clear()
        finally:
            if self.runtime:
                await self.runtime.stop()

    def close(self):
        try:
            self.call(self._close())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)

import ipaddress
import random
import re
import socket
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit, urljoin

import httpx
from novel_get.fetch import Page, DownloadError, SiteBlocked, decode_html, retry_seconds

from .sites import site_config
from .response import (PageHTTPError, AccessDenied, StopWithoutVerification, RateLimited,
                       ServerUnavailable, blocked_status, check_page_response, check_destination)


class Cancelled(Exception):
    pass


class Control:
    def __init__(self, emit=lambda **_: None):
        self.cancel = threading.Event()
        self.paused = threading.Event()
        self.approved = threading.Event()
        self.show_browser = threading.Event()
        self.emit = emit

    def checkpoint(self):
        if self.cancel.is_set():
            raise Cancelled("任务已停止，已保存进度")
        while self.paused.is_set():
            if self.cancel.wait(.15):
                raise Cancelled("任务已停止，已保存进度")

    def wait(self, seconds, reason="等待", pump=None):
        end = time.monotonic() + seconds
        last = 0
        while time.monotonic() < end:
            self.checkpoint()
            if time.monotonic() - last >= 5:
                self.emit(kind="wait", message=f"{reason}，剩余 {max(1, int(end-time.monotonic()))} 秒")
                last = time.monotonic()
            interval = min(.2, max(0, end-time.monotonic()))
            if pump:
                pump(interval)
            else:
                self.cancel.wait(interval)

    def manual(self, url, message, pump=None, ready=None):
        self.approved.clear()
        self.emit(kind="manual", url=url, message=message)
        last_notice = time.monotonic()
        while True:
            self.checkpoint()
            if pump:
                pump()
            else:
                self.approved.wait(.2)
            if ready:
                if ready():
                    break
                # An extra click must not close a still-blocked browser session.
                self.approved.clear()
            elif self.approved.is_set():
                break
            if time.monotonic() - last_notice >= 30:
                self.emit(kind="waiting_manual", url=url, message="仍在等待页面通过登录/验证；浏览器会话和下载进度已保留")
                last_notice = time.monotonic()
        self.checkpoint()
        self.emit(kind="manual_done", message="页面已恢复可读，自动继续")


def validate_url(url, resolve=False):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise DownloadError("请输入有效的 http/https 网页地址，不支持含账号密码的地址")
    if parts.port not in {None, 80, 443}:
        raise DownloadError("只支持网站的标准 HTTP/HTTPS 端口")
    host = parts.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".localhost")):
        raise DownloadError("不允许访问本机或局域网地址")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = []
    if resolve:
        try:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)]
        except OSError as exc:
            raise DownloadError(f"域名解析失败：{host}") from exc
    if any(not address.is_global for address in addresses):
        raise DownloadError("不允许访问本机、局域网或保留网络地址")
    return url


# Only render the normal site's DOM; no stealth patches, CAPTCHA automation or decryption.
RENDERED_DOM = """() => {
    const clone = document.documentElement.cloneNode(true);
    const originals = [document.documentElement, ...document.documentElement.querySelectorAll('*')];
    const copies = [clone, ...clone.querySelectorAll('*')];
    originals.forEach((el, i) => {
        const style = getComputedStyle(el), target = copies[i];
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
            target.setAttribute('hidden', '');
        }
        if (['P','DIV','BLOCKQUOTE'].includes(el.tagName) && parseFloat(style.textIndent) >= 12) {
            target.style.textIndent = '2em';
        }
    });
    return '<!doctype html>' + clone.outerHTML;
}"""


class FetcherPool:
    """Keep a few site sessions alive on the batch worker's own thread."""

    def __init__(self, control, max_sessions=3):
        self.control = control
        self.max_sessions = max(1, max_sessions)
        self.sessions = OrderedDict()

    def __enter__(self):
        return self

    def get(self, **options):
        options = dict(options)
        if options.get('profile') is not None:
            options['profile'] = Path(options['profile']).resolve()
        key = tuple(sorted(options.items()))
        if key in self.sessions:
            self.sessions.move_to_end(key)
            return self.sessions[key], True
        if len(self.sessions) >= self.max_sessions:
            _, previous = self.sessions.popitem(last=False)
            previous.__exit__(None, None, None)
        fetcher = Fetcher(self.control, **options)
        fetcher.__enter__()
        self.sessions[key] = fetcher
        return fetcher, False

    def __exit__(self, exc_type, exc, traceback):
        error = None
        while self.sessions:
            _, fetcher = self.sessions.popitem()
            try:
                fetcher.__exit__(exc_type, exc, traceback)
            except Exception as close_error:
                error = error or close_error
        if error is not None and exc_type is None:
            raise error


class Fetcher:
    def __init__(self, control, *, delay=3, mode="http", profile=None, proxy=False, manual=True, headless=False, minimized=False, pause_every=30, pause_seconds=15):
        self.control = control
        self.delay = max(1, float(delay))
        self.pause_every = max(1,int(pause_every))
        self.pause_seconds = max(0,float(pause_seconds))
        self.mode, self.profile, self.manual_enabled = mode, profile, manual
        self.headless = headless
        self.minimized = minimized
        self.client = httpx.Client(timeout=httpx.Timeout(20, connect=10), trust_env=proxy, follow_redirects=False,
                                   headers={"User-Agent": "NovelTXT/0.1 (personal offline reader)", "Accept": "text/html", "Accept-Language": "zh-CN,zh;q=0.9"})
        self.context = self.runtime = self.tab = None
        self.last = 0
        self.count = 0
        self.prepared = set()
        self.approved_domains = set()

    def __enter__(self):
        if self.mode == "browser":
            try:
                from playwright.sync_api import sync_playwright
                self.runtime = sync_playwright().start()
                profile = Path(self.profile)
                profile.mkdir(parents=True, exist_ok=True)
                # Use a separate profile. Never attach to the user's existing browser.
                errors = []
                for channel in ("msedge", "chrome"):
                    try:
                        self.context = self.runtime.chromium.launch_persistent_context(
                            str(profile.resolve()), channel=channel, headless=self.headless, no_viewport=True,
                            accept_downloads=False, locale="zh-CN",
                            args=["--start-minimized"] if self.minimized and not self.headless else [])
                        break
                    except Exception as exc:
                        errors.append(str(exc))
                if not self.context:
                    raise DownloadError("未找到可用的 Edge/Chrome，或专用浏览器仍被占用。请关闭上次打开的下载浏览器后重试。")
                self.tab = self.context.pages[0] if self.context.pages else self.context.new_page()
                if self.minimized and not self.headless:
                    session = self.context.new_cdp_session(self.tab)
                    window = session.send("Browser.getWindowForTarget")
                    session.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "minimized"}})
                    session.detach()
                self.last_status = None
                self.last_challenge = False
                self.tab.on("response", self._remember_status)
                self.context.on("page", lambda page: page.close() if page != self.tab else None)
                self.context.route("**/*", self._route)
            except Exception:
                self.__exit__(None, None, None)
                raise
        return self

    def _remember_status(self, response):
        if response.request.resource_type == "document" and response.frame == self.tab.main_frame:
            self.last_status = response.status
            self.last_challenge = response.headers.get("cf-mitigated") == "challenge"

    def show_window(self):
        session = self.context.new_cdp_session(self.tab)
        try:
            window = session.send("Browser.getWindowForTarget")
            session.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "normal"}})
            self.tab.bring_to_front()
        finally:
            session.detach()

    def manual_pump(self):
        if self.control.show_browser.is_set():
            self.control.show_browser.clear()
            self.show_window()
        self.tab.wait_for_timeout(500)

    def _route(self, route):
        request = route.request
        try:
            parts = urlsplit(request.url)
            if parts.scheme not in {"http", "https"}:
                return route.abort()
            host = parts.hostname
            if host not in self.approved_domains:
                validate_url(request.url, resolve=True)
                self.approved_domains.add(host)
            if request.resource_type in {"media"}:
                return route.abort()
            route.continue_()
        except Exception:
            route.abort()

    def __exit__(self, *_):
        try:
            self.client.close()
            if self.context:
                try:
                    self.context.close()
                except Exception:
                    pass  # A user-closed browser must not mask the original failure.
        finally:
            if self.runtime:
                try:
                    self.runtime.stop()
                except Exception:
                    pass

    def pace(self):
        self.control.checkpoint()
        if self.last:
            duration = self.delay + random.uniform(0, min(3, self.delay / 2))
            if self.count and self.count % self.pause_every == 0:
                duration = max(duration, self.pause_seconds)
            pump = (lambda seconds: self.tab.wait_for_timeout(seconds * 1000)) if self.tab else None
            self.control.wait(max(0, self.last + duration-time.monotonic()), "限速休息", pump=pump)
        self.count += 1

    def get(self, url, referer=None):
        validate_url(url, resolve=True)
        if self.context:
            return self.browser_get(url, referer)
        initial_host = urlsplit(url).hostname
        for attempt in range(3):
            current = url
            try:
                for _ in range(8):
                    validate_url(current, resolve=True)
                    if urlsplit(current).hostname != initial_host:
                        raise DownloadError("网页跳转到了其他域名，请检查后输入最终目录地址")
                    self.pace()
                    self.control.emit(kind="request", message="读取网页", url=current)
                    try:
                        with self.client.stream("GET", current, headers={"Referer": referer} if referer else {}) as response:
                            if response.status_code == 429 and response.headers.get('cf-mitigated') != 'challenge':
                                raise RateLimited(f"HTTP 429：访问频繁，本站暂停并保留断点，请稍后继续。Retry-After: {response.headers.get('retry-after', '未提供')}", 429, current)
                            if response.status_code in {204,206,304} and response.headers.get('cf-mitigated') != 'challenge':
                                raise PageHTTPError(response.status_code, current)
                            if response.is_redirect:
                                current = urljoin(current, response.headers.get("location", ""))
                                continue
                            if response.status_code not in {401, 403, 404, 410, 407, 451} and response.headers.get('cf-mitigated') != 'challenge':
                                response.raise_for_status()
                            kind = response.headers.get("content-type", "")
                            if kind and not any(x in kind for x in ("html", "text/plain", "xml")):
                                raise DownloadError("返回内容不是网页")
                            chunks, size = [], 0
                            for chunk in response.iter_bytes():
                                self.control.checkpoint()
                                size += len(chunk)
                                if size > 12_000_000:
                                    raise DownloadError("单页超过 12 MB，已停止以避免异常下载")
                                chunks.append(chunk)
                            page = Page(str(response.url), decode_html(b"".join(chunks), kind))
                            check_page_response(page, response.status_code, response.headers.get('cf-mitigated') == 'challenge')
                            return page
                    finally:
                        self.last = time.monotonic()
                raise DownloadError("网页重定向次数过多")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {404, 410}:
                    raise PageHTTPError(exc.response.status_code, str(exc.request.url)) from exc
                if exc.response.status_code not in {408, 500, 502, 503, 504} or attempt == 2:
                    raise DownloadError(f"HTTP {exc.response.status_code}：页面读取失败") from exc
                wait = max(20 * 2**attempt, retry_seconds(exc.response.headers.get("retry-after", "")))
                if wait > 300:
                    raise ServerUnavailable(f"服务器要求等待 {wait:.0f} 秒，已暂停本站并保留断点，请稍后继续", exc.response.status_code, url)
                self.control.wait(wait, "服务器暂不可用，退避重试")
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise DownloadError(f"网络连接失败：{type(exc).__name__}") from exc
                self.control.wait(10 * 2**attempt, "网络失败，退避重试")

    def browser_get(self, url, referer):
        self.pace()
        self.control.emit(kind="request", message="通过专用浏览器读取网页", url=url)
        try:
            self.last_status = None
            self.last_challenge = False
            self.tab.goto(url, wait_until="domcontentloaded", timeout=25000, referer=referer)
            self.tab.wait_for_timeout(1000)
            selector = site_config(url).get("content_selector")
            from .sites import guard_body
            def readable():
                page = Page(self.tab.url, self.tab.content())
                check_page_response(page, self.last_status, self.last_challenge)
                check_destination(page, url)
                if selector:
                    guard_body(page, selector)
                    path = urlsplit(url).path.strip('/').split('/')
                    chapter_url = (path[0] == 'chapter' or (path[0] == 'novel' and len(path) >= 3))
                    if chapter_url:
                        from .sites import extract_readable
                        extract_readable(page, site_config(url))
                return page
            try:
                page = readable()
            except SiteBlocked as blocked:
                if isinstance(blocked, StopWithoutVerification):
                    raise
                self.control.emit(kind="access_blocked", url=url, status=self.last_status,
                                  challenge=self.last_challenge, message=str(blocked))
                if not self.manual_enabled or self.headless:
                    raise
                def ready():
                    try:
                        readable()
                        return True
                    except StopWithoutVerification:
                        # A challenge can change into a permanent denial while
                        # the user is looking at it; leave the manual wait then.
                        raise
                    except SiteBlocked:
                        return False
                self.control.manual(url, "网站要求登录或真人验证。请在浏览器完成并返回原任务页；恢复可读后会自动继续，无需重复点击。",
                                    pump=self.manual_pump, ready=ready)
                page = readable()
            if selector and "/novel/" in url and len(urlsplit(url).path.rstrip('/').split('/')) >= 4:
                self.tab.wait_for_selector(selector, state="visible", timeout=10000)
            return Page(self.tab.url, self.tab.evaluate(RENDERED_DOM))
        except (Cancelled, DownloadError):
            raise
        except Exception as exc:
            raise DownloadError(f"浏览器读取失败：{type(exc).__name__}；检查页面后可继续断点任务") from exc
        finally:
            self.last = time.monotonic()

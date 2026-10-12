from __future__ import annotations

import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urldefrag, urlsplit

import httpx
from bs4 import BeautifulSoup
from charset_normalizer import from_bytes


class DownloadError(Exception):
    pass


class SiteBlocked(DownloadError):
    """Stop the run instead of probing more chapters after an access restriction."""


@dataclass
class Page:
    url: str
    html: str


DEFAULT_DELAY = 15.0
DEFAULT_JITTER = 15.0
DEFAULT_PAUSE_EVERY = 20
DEFAULT_PAUSE = 300.0


def check_access(page: Page, soup=None):
    soup = soup if soup is not None else BeautifulSoup(page.html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if (
        re.search(r"just a moment|access denied|attention required|安全验证|人机验证|身份验证|访问被拒绝|^请稍候[.…。]*$", title, re.I)
        or re.search(r"/WAF/VERIFY/|/captcha(?:/|\?)", page.url, re.I)
        or soup.select_one("form#captcha-form")
    ):
        raise SiteBlocked(f"站点返回验证/拒绝访问页面，已停止本次任务，请勿立即重试: {page.url}")


def retry_seconds(value):
    try:
        return max(0, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}


def decode_html(data: bytes, content_type: str = "") -> str:
    # HTML's own charset is often more accurate than a server's default header.
    meta = re.search(rb"charset\s*=\s*[\"\']?([\w-]+)", data[:8192], re.I)
    header = re.search(r"charset\s*=\s*[\"\']?([\w-]+)", content_type, re.I)
    encodings = ["utf-8-sig"] if data.startswith(b"\xef\xbb\xbf") else []
    encodings += [meta[1].decode("ascii")] if meta else []
    encodings += [header[1]] if header else []
    encodings += ["utf-8"]
    for encoding in encodings:
        if encoding.lower() in {"gb2312", "gbk"}:
            encoding = "gb18030"
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeError):
            pass
    match = from_bytes(data).best()
    if match is None:
        raise DownloadError("无法确定页面编码")
    return str(match)


class Fetcher:
    def __init__(
        self,
        *,
        delay=DEFAULT_DELAY,
        jitter=DEFAULT_JITTER,
        pause_every=DEFAULT_PAUSE_EVERY,
        pause=DEFAULT_PAUSE,
        timeout=30.0,
        retries=0,
        headers=None,
        browser=False,
        browser_channel=None,
        wait=1.0,
        wait_selector=None,
        progress=lambda _: None,
        interactive=False,
        browser_profile=None,
    ):
        self.delay, self.timeout, self.retries = delay, timeout, retries
        self.jitter, self.pause_every, self.pause = jitter, pause_every, pause
        self.headers = {**DEFAULT_HEADERS, **(headers or {})}
        self.browser_headers = {k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"}
        self.interactive = interactive or browser_profile is not None
        self.browser, self.browser_channel = browser or self.interactive, browser_channel
        self.browser_profile = (
            Path(browser_profile) if browser_profile is not None else (Path(".novel-get/browser") / (browser_channel or "chromium"))
        )
        self.tab = None
        self.prepared_sites = set()
        self.wait, self.wait_selector = wait, wait_selector
        self.last_request = None
        self.request_count = 0
        self.blocked = None
        self.progress = progress
        self.client = httpx.Client(headers=self.headers, timeout=timeout, follow_redirects=False)
        self.runtime = self.browser_instance = self.context = None

    def __enter__(self):
        if self.browser:
            try:
                from playwright.sync_api import sync_playwright

                self.runtime = sync_playwright().start()
                # Let Chromium supply internally consistent UA and client hints.
                options = {"locale": "zh-CN", "extra_http_headers": self.browser_headers}
                if self.headers["User-Agent"] != DEFAULT_UA:
                    options["user_agent"] = self.headers["User-Agent"]
                if self.interactive:
                    self.browser_profile.mkdir(parents=True, exist_ok=True)
                    self.context = self.runtime.chromium.launch_persistent_context(
                        str(self.browser_profile.resolve()),
                        headless=False,
                        channel=self.browser_channel,
                        no_viewport=True,
                        **options,
                    )
                    self.tab = self.context.pages[0] if self.context.pages else self.context.new_page()
                    self.progress(f"交互浏览器会话：{self.browser_profile.resolve()}")
                else:
                    self.browser_instance = self.runtime.chromium.launch(
                        headless=True,
                        channel=self.browser_channel,
                    )
                    self.context = self.browser_instance.new_context(**options)
                    self.context.route(
                        "**/*",
                        lambda route: route.abort() if route.request.resource_type in {"image", "media", "font"} else route.continue_(),
                    )
            except Exception as exc:
                self.__exit__(None, None, None)
                raise DownloadError(
                    "浏览器启动失败；运行 uv sync --extra browser 和 "
                    "uv run --extra browser playwright install chromium，"
                    "或使用 --browser-channel chrome/msedge。专用会话目录不能被其他任务同时使用。" + str(exc)
                ) from exc
        return self

    def __exit__(self, *_):
        self.client.close()
        try:
            if self.context:
                self.context.close()
        finally:
            try:
                if self.browser_instance:
                    self.browser_instance.close()
            finally:
                if self.runtime:
                    self.runtime.stop()

    def _pace(self):
        if self.blocked:
            raise SiteBlocked(self.blocked)
        if self.last_request is not None:
            interval = self.delay + random.uniform(0, self.jitter)
            if self.pause_every and self.request_count % self.pause_every == 0:
                interval = max(interval, self.pause + random.uniform(0, self.pause))
            seconds = max(0, interval - (time.monotonic() - self.last_request))
            if seconds and self.pause_every and self.request_count % self.pause_every == 0:
                self.progress(f"已请求 {self.request_count} 次页面，休息 {seconds:.0f} 秒；Ctrl+C 可中断并保留缓存")
            time.sleep(seconds)
        self.request_count += 1

    @staticmethod
    def _check_status(status, url, retry_after=""):
        if status in {401, 403, 429}:
            hint = f"；服务器 Retry-After: {retry_after}" if retry_after else ""
            raise SiteBlocked(f"HTTP {status}: {url}{hint}；已停止本次任务，请勿立即重试")

    def _http_get(self, url, referer):
        request = self.client.build_request("GET", url, headers={"Referer": referer} if referer else {})
        # Count redirects too: automatic redirects would bypass the request pacing.
        for _ in range(21):
            self._pace()
            try:
                response = self.client.send(request, follow_redirects=False)
            finally:
                self.last_request = time.monotonic()
            self._check_status(response.status_code, str(response.url), response.headers.get("retry-after", ""))
            if response.next_request is None:
                return response
            check_access(Page(str(response.next_request.url), ""))
            request = response.next_request
        raise DownloadError(f"重定向次数过多: {url}")

    def get(self, url: str, referer: str | None = None) -> Page:
        if self.blocked:
            raise SiteBlocked(self.blocked)
        for attempt in range(self.retries + 1):
            try:
                if self.context:
                    self._pace()
                    try:
                        page = self._browser_get(url, referer)
                    finally:
                        self.last_request = time.monotonic()
                else:
                    response = self._http_get(url, referer)
                    response.raise_for_status()
                    kind = response.headers.get("content-type", "")
                    if kind and not any(t in kind for t in ("html", "text/plain", "xml")):
                        raise DownloadError(f"响应不是 HTML ({kind}): {url}")
                    page = Page(str(response.url), decode_html(response.content, kind))
                check_access(page)
                return page
            except SiteBlocked as exc:
                self.blocked = str(exc)
                if self.interactive and self.tab is not None and not self.tab.is_closed():
                    self._confirm_browser(
                        f"{exc}\n自动下载已停止。浏览器窗口暂时保留，你可以手动查看或处理页面。"
                        "\n完成后回到终端按回车，保存浏览器会话并退出本次下载；不会自动重试。"
                    )
                raise
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code not in {408, 500, 502, 503, 504} or attempt == self.retries:
                    raise DownloadError(f"HTTP {code}: {url}") from exc
                retry_after = exc.response.headers.get("retry-after", "")
                time.sleep(max(60 * 2**attempt, retry_seconds(retry_after)))
            except httpx.RequestError as exc:
                if attempt == self.retries:
                    raise DownloadError(f"网络请求失败: {url}: {exc}") from exc
                time.sleep(60 * 2**attempt)
        raise DownloadError(f"请求失败: {url}")

    def _browser_get(self, url, referer):
        if self.tab is None:
            self.tab = self.context.new_page()
        tab = self.tab
        if tab.is_closed():
            raise SiteBlocked("浏览器标签页已关闭，已停止本次任务")
        try:
            response = self._navigate_browser(tab, url, referer)
            if response:
                self._check_status(response.status, tab.url, response.headers.get("retry-after", ""))
            if response and response.status >= 400:
                raise DownloadError(f"浏览器 HTTP {response.status}: {url}")
            tab.wait_for_timeout(self.wait * 1000)
            page = Page(tab.url, tab.content())
            check_access(page)
            if self.wait_selector:
                try:
                    tab.wait_for_selector(self.wait_selector, timeout=self.timeout * 1000)
                except Exception:
                    check_access(Page(tab.url, tab.content()))
                    raise
                page = Page(tab.url, tab.content())
                check_access(page)
            site = urlsplit(page.url).netloc
            if self.interactive and site not in self.prepared_sites:
                self._confirm_browser(
                    f"浏览器已打开 {page.url}\n如需登录，可在窗口手动完成，然后回到上述页面。"
                    "\n确认内容可正常阅读后，在终端按回车开始缓慢下载；Ctrl+C 退出。"
                )
                if tab.is_closed():
                    raise SiteBlocked("浏览器标签页已关闭，已停止本次任务")
                if urldefrag(tab.url)[0] != urldefrag(page.url)[0]:
                    raise SiteBlocked("手动浏览后未回到原页面，已停止本次任务以避免下载错误内容")
                page = Page(tab.url, tab.content())
                check_access(page)
                self.prepared_sites.add(site)
            return page
        except DownloadError:
            raise
        except Exception as exc:
            raise DownloadError(f"浏览器读取失败: {url}: {exc}") from exc

    @staticmethod
    def _confirm_browser(message):
        print(message, file=sys.stderr, flush=True)
        try:
            input()
        except EOFError as exc:
            raise SiteBlocked("交互输入已关闭，已停止本次任务；请从可输入的终端运行") from exc

    def _navigate_browser(self, tab, url, referer):
        # Follow an existing visible link when possible, preserving normal navigation.
        # No simulated mouse trails, fingerprint spoofing, or automatic challenge solving.
        if self.interactive and tab.url.startswith(("http://", "https://")):
            links = tab.locator("a[href]")
            index = links.evaluate_all(
                """(anchors, target) => anchors.findIndex(a =>
                a.href === target && !a.hasAttribute('download') &&
                (!a.target || a.target === '_self') && a.getClientRects().length > 0 &&
                getComputedStyle(a).visibility !== 'hidden')""",
                url,
            )
            if index >= 0:
                with tab.expect_navigation(wait_until="domcontentloaded", timeout=self.timeout * 1000) as navigation:
                    links.nth(index).click(timeout=self.timeout * 1000)
                return navigation.value
        return tab.goto(url, referer=referer, wait_until="domcontentloaded", timeout=self.timeout * 1000)

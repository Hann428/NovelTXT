import sys
from types import ModuleType, SimpleNamespace

import pytest

from novel_get.cli import main, parser
from novel_get.fetch import Fetcher, SiteBlocked

ROOT = "https://example.test/book/1/"


class Tab:
    def __init__(self):
        self.url = "about:blank"
        self.closed = False
        self.actions = []
        self.routes = {}
        self.available_link = None
        self.response = None
        self.html = "<h1>测试页面</h1>"
        self.content_reads = 0

    def is_closed(self):
        return self.closed

    def load(self, url):
        self.url = url
        status, self.html = self.routes.get(url, (200, "<h1>测试页面</h1>"))
        self.response = SimpleNamespace(status=status, headers={})
        return self.response

    def goto(self, url, **kwargs):
        self.actions.append(("goto", url))
        return self.load(url)

    def content(self):
        self.content_reads += 1
        return self.html

    def wait_for_timeout(self, timeout):
        pass

    def wait_for_selector(self, selector, **kwargs):
        self.actions.append(("selector", selector))

    def locator(self, selector):
        assert selector == "a[href]"
        tab = self

        class Links:
            def evaluate_all(self, expression, target):
                self.target = target
                return 0 if target == tab.available_link else -1

            def nth(self, index):
                assert index == 0
                return self

            def click(self, **kwargs):
                tab.actions.append(("click", self.target))
                tab.load(self.target)

        return Links()

    def expect_navigation(self, **kwargs):
        tab = self

        class Navigation:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.value = tab.response

        return Navigation()


@pytest.fixture
def browser(monkeypatch):
    state = SimpleNamespace(launches=[], stops=0, confirms=[])
    state.tab = Tab()

    class Context:
        def __init__(self):
            self.pages = [state.tab]
            self.routes = []
            self.new_pages = 0
            self.closed = False

        def new_page(self):
            self.new_pages += 1
            return state.tab

        def route(self, pattern, handler):
            self.routes.append((pattern, handler))

        def close(self):
            self.closed = True

    state.context = Context()

    class Browser:
        def new_context(self, **kwargs):
            state.options = kwargs
            return state.context

        def close(self):
            pass

    class Chromium:
        def launch(self, **kwargs):
            state.launches.append(("temporary", kwargs))
            return Browser()

        def launch_persistent_context(self, path, **kwargs):
            state.launches.append(("persistent", path, kwargs))
            state.options = kwargs
            return state.context

    def stop():
        state.stops += 1

    runtime = SimpleNamespace(chromium=Chromium(), stop=stop)
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: runtime)
    monkeypatch.setitem(sys.modules, "playwright", ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(Fetcher, "_confirm_browser", lambda self, message: state.confirms.append(message))
    return state


def interactive_fetcher(tmp_path, **options):
    return Fetcher(interactive=True, browser_profile=tmp_path / "browser-profile", delay=0, jitter=0, pause=0, **options)


def test_persistent_visible_profile_and_native_browser_headers(browser, tmp_path):
    with interactive_fetcher(tmp_path, browser_channel="chrome") as fetcher:
        assert fetcher.browser and fetcher.interactive
        fetcher.get(ROOT)
    kind, path, options = browser.launches[0]
    assert kind == "persistent" and path == str((tmp_path / "browser-profile").resolve())
    assert options["headless"] is False and options["channel"] == "chrome"
    assert options["no_viewport"] is True
    assert options["extra_http_headers"] == {} and "user_agent" not in options
    assert browser.context.routes == []  # Verification images and normal page resources remain visible.
    assert browser.context.closed and browser.stops == 1


def test_reuses_tab_clicks_existing_link_and_confirms_only_once_per_site(browser, tmp_path):
    with interactive_fetcher(tmp_path) as fetcher:
        fetcher.get(ROOT)
        browser.tab.available_link = ROOT + "1.html"
        fetcher.get(ROOT + "1.html", referer=ROOT)
        fetcher.get(ROOT + "2.html", referer=ROOT + "1.html")
    assert browser.context.new_pages == 0
    assert browser.tab.actions == [("goto", ROOT), ("click", ROOT + "1.html"), ("goto", ROOT + "2.html")]
    assert len(browser.confirms) == 1


@pytest.mark.parametrize("status,html", [(403, "<h1>禁止访问</h1>"), (429, ""), (200, "<title>身份验证</title>")])
def test_manual_handling_does_not_retry_after_block(browser, tmp_path, monkeypatch, status, html):
    browser.tab.routes[ROOT] = (status, html)
    prompts = []

    def handle(self, message):
        assert not browser.context.closed
        prompts.append(message)
        browser.tab.html = "<h1>用户已手动处理</h1>"

    monkeypatch.setattr(Fetcher, "_confirm_browser", handle)
    with interactive_fetcher(tmp_path) as fetcher:
        with pytest.raises(SiteBlocked):
            fetcher.get(ROOT)
        with pytest.raises(SiteBlocked):
            fetcher.get(ROOT + "1.html")
    assert browser.tab.actions == [("goto", ROOT)]
    assert len(prompts) == 1 and "不会自动重试" in prompts[0]
    assert browser.context.closed


def test_verification_is_detected_before_waiting_for_missing_selector(browser, tmp_path):
    browser.tab.routes[ROOT] = (200, "<title>身份验证</title>")
    with interactive_fetcher(tmp_path, wait_selector="#content") as fetcher:
        with pytest.raises(SiteBlocked):
            fetcher.get(ROOT)
    assert all(action[0] != "selector" for action in browser.tab.actions)


def test_user_must_return_to_requested_page_before_download(browser, tmp_path, monkeypatch):
    monkeypatch.setattr(Fetcher, "_confirm_browser", lambda self, _: setattr(browser.tab, "url", ROOT + "wrong.html"))
    with interactive_fetcher(tmp_path) as fetcher:
        with pytest.raises(SiteBlocked, match="未回到原页面"):
            fetcher.get(ROOT)
    assert browser.tab.actions == [("goto", ROOT)]


def test_closing_browser_stops_and_does_not_reopen_tab(browser, tmp_path):
    with interactive_fetcher(tmp_path) as fetcher:
        browser.tab.closed = True
        with pytest.raises(SiteBlocked, match="已关闭"):
            fetcher.get(ROOT)
    assert browser.tab.actions == [] and browser.context.new_pages == 0
    assert browser.confirms == []


def test_interrupt_at_manual_checkpoint_flushes_context(browser, tmp_path, monkeypatch):
    def interrupt(self, _):
        raise KeyboardInterrupt

    monkeypatch.setattr(Fetcher, "_confirm_browser", interrupt)
    with pytest.raises(KeyboardInterrupt):
        with interactive_fetcher(tmp_path) as fetcher:
            fetcher.get(ROOT)
    assert browser.context.closed and browser.stops == 1


def test_headless_mode_remains_temporary_and_reuses_one_tab(browser):
    with Fetcher(browser=True, delay=0, jitter=0) as fetcher:
        fetcher.get(ROOT)
        fetcher.get(ROOT + "1.html")
    assert browser.launches == [("temporary", {"headless": True, "channel": None})]
    assert browser.context.new_pages == 1 and len(browser.context.routes) == 1
    assert browser.confirms == [] and browser.context.closed


def test_explicit_profile_enables_interactive_and_requires_terminal(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert main(["inspect", ROOT, "--browser-profile", str(tmp_path / "profile")]) == 2
    assert "需要可输入的终端" in capsys.readouterr().err
    args = parser().parse_args(["download", ROOT, "--interactive"])
    assert args.interactive and args.delay == 15 and args.pause == 300

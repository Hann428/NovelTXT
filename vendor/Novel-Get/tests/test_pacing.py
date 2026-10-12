from types import SimpleNamespace

import httpx
import pytest

from novel_get.cli import main, parser
from novel_get.fetch import Fetcher, SiteBlocked, retry_seconds

ROOT = "https://example.test/book/1/"


@pytest.fixture
def clock(monkeypatch):
    import novel_get.fetch as fetch

    state = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(seconds):
        state.sleeps.append(seconds)
        state.now += seconds

    monkeypatch.setattr(fetch.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(fetch.time, "sleep", sleep)
    monkeypatch.setattr(fetch.random, "uniform", lambda low, high: high)
    return state


def mock_fetcher(handler, **options):
    fetcher = Fetcher(**options)
    fetcher.client.close()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    return fetcher


def test_defaults_pause_after_completed_response_and_long_break(clock):
    starts = []
    messages = []

    def handler(request):
        starts.append(clock.now)
        clock.now += 2  # The response itself takes time.
        return httpx.Response(200, text="<h1>测试</h1>")

    with mock_fetcher(handler, progress=messages.append) as fetcher:
        for _ in range(21):
            fetcher.get(ROOT)
    assert starts[0] == 0
    assert all(b - a == 32 for a, b in zip(starts[:19], starts[1:20]))
    assert starts[20] - starts[19] == 602
    assert clock.sleeps == [30] * 19 + [600]
    assert len(messages) == 1 and "600 秒" in messages[0]


def test_redirects_obey_pacing(clock):
    calls = []

    def handler(request):
        calls.append((str(request.url), clock.now))
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": ROOT + "toc"})
        return httpx.Response(200, text="<h1>目录</h1>")

    with mock_fetcher(handler) as fetcher:
        assert fetcher.get(ROOT).url == ROOT + "toc"
    assert len(calls) == 2 and calls[1][1] - calls[0][1] == 30


@pytest.mark.parametrize("status", [401, 403, 429])
def test_access_restrictions_never_retry_or_allow_later_requests(clock, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"retry-after": "7200"})

    with mock_fetcher(handler, retries=3) as fetcher:
        with pytest.raises(SiteBlocked, match=f"HTTP {status}"):
            fetcher.get(ROOT)
        with pytest.raises(SiteBlocked):
            fetcher.get("https://another.example/next")
    assert len(calls) == 1
    assert clock.sleeps == []


def test_captcha_redirect_stops_before_requesting_captcha(clock):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "/WAF/VERIFY/CAPTCHA?info=test"})

    with mock_fetcher(handler) as fetcher:
        with pytest.raises(SiteBlocked):
            fetcher.get(ROOT)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403),
        httpx.Response(429),
        httpx.Response(200, text="<title>身份验证</title>"),
    ],
)
def test_cli_stops_remaining_chapters_and_books_on_block(tmp_path, monkeypatch, clock, response):
    import novel_get.cli as cli

    calls = []
    toc = '<meta property="og:title" content="测试书"><div class="chapter-list">'
    toc += '<a href="1.html">第1章 测试</a><a href="2.html">第2章 测试</a></div>'

    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == ROOT:
            return httpx.Response(200, text=toc)
        return response

    fetcher = mock_fetcher(handler)
    monkeypatch.setattr(cli, "Fetcher", lambda **_: fetcher)
    assert main(["download", ROOT, "https://another.example/", "-o", str(tmp_path), "--quiet"]) == 2
    assert calls == [ROOT, ROOT + "1.html"]
    import json

    report = json.loads((tmp_path / "run-report.json").read_text(encoding="utf-8"))
    assert [row["status"] for row in report] == ["incomplete", "skipped"]
    manifest = json.loads(next(tmp_path.glob("*/manifest.json")).read_text(encoding="utf-8"))
    assert manifest["blocked"] and len(manifest["failures"]) == 1


def test_block_during_directory_discovery_stops_before_chapters(tmp_path, monkeypatch, clock):
    import novel_get.cli as cli

    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(200, text='<div class="chapter-list"><a href="1.html">第1章 测试</a></div><a href="?page=2">下一页</a>')
        return httpx.Response(429)

    monkeypatch.setattr(cli, "Fetcher", lambda **_: mock_fetcher(handler))
    assert main(["download", ROOT, "https://another.example/", "-o", str(tmp_path), "--quiet"]) == 2
    assert calls == [ROOT, ROOT + "?page=2"]


def test_browser_status_stops_without_more_navigation(clock):
    calls = []

    class Tab:
        url = ROOT

        def is_closed(self):
            return False

        def goto(self, url, **kwargs):
            calls.append(url)
            return SimpleNamespace(status=429, headers={})

        def close(self):
            pass

    with mock_fetcher(lambda _: pytest.fail("Unexpected HTTP request")) as fetcher:
        fetcher.context = SimpleNamespace(new_page=lambda: Tab(), close=lambda: None)
        with pytest.raises(SiteBlocked):
            fetcher.get(ROOT)
        with pytest.raises(SiteBlocked):
            fetcher.get(ROOT)
    assert calls == [ROOT]


def test_default_no_retry_and_explicit_retry_honors_retry_after(clock):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, headers={"retry-after": "7200"})
        return httpx.Response(200, text="<h1>恢复</h1>")

    from novel_get.fetch import DownloadError

    with mock_fetcher(handler) as fetcher:
        with pytest.raises(DownloadError):
            fetcher.get(ROOT)
    assert len(calls) == 1
    calls.clear()
    with mock_fetcher(handler, retries=1) as fetcher:
        fetcher.get(ROOT)
    assert len(calls) == 2 and 7200 in clock.sleeps
    assert retry_seconds("invalid") == 0
    assert retry_seconds("Wed, 01 Jan 2098 00:00:00 GMT") > 7200


def test_cli_conservative_defaults():
    for command in ("download", "inspect"):
        args = parser().parse_args([command, ROOT])
        assert (args.delay, args.jitter, args.pause_every, args.pause, args.retries) == (15, 15, 20, 300, 0)

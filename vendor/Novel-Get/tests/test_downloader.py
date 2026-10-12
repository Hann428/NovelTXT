import json
import zipfile
from xml.etree import ElementTree

import httpx
import pytest

from novel_get.cli import main
from novel_get.engine import discover, download, read_chapter
from novel_get.export import export_epub
from novel_get.fetch import DownloadError, Fetcher, Page, decode_html
from novel_get.parse import Book, Ref, extract_chapter, number, toc_refs

ROOT = "https://books.example/book/1/"


def chapter(n, body=None, links="", fraction="", title=None):
    title = title or f"第{n}章 测试{n}"
    body = body or f"这是第{n}章的独立测试内容。" * 12
    return f'<h1>{title}{fraction}</h1><div id="content"><p>{body}</p></div>{links}'


def toc(links, latest="", extra=""):
    return (
        f'<meta property="og:title" content="测试小说">'
        f'<meta property="og:novel:latest_chapter_name" content="{latest}">'
        f'<div class="chapter-list">{links}</div>{extra}'
    )


def link(n, url=None):
    return f'<a href="{url or str(n) + ".html"}">第{n}章 测试{n}</a>'


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, referer=None):
        self.calls.append(url)
        if url not in self.pages:
            raise DownloadError("unavailable: " + url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return Page(url, value)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("第十二章 开始", 12),
        ("第四百一十九章 结束", 419),
        ("第两千零三章", 2003),
        ("1.开始", 1),
        ("第〇一章", 1),
        ("第１２章 开始", 12),
        ("番外一", None),
    ],
)
def test_numbers(title, expected):
    assert number(title) == expected


def test_encoding():
    original = '<meta charset="gb2312"><p>测试汉字与生僻字喆</p>'
    assert decode_html(original.encode("gb18030"), "text/html; charset=ISO-8859-1") == original


def test_directory_pagination_excludes_latest_and_retains_conflicts():
    fake = FakeFetcher(
        {
            ROOT: '<h2>最新章节</h2><div id="chapterlist">'
            + link(4)
            + "</div><h2>正文</h2>"
            + toc(
                link(1) + link(2, "1.html"),
                extra='<div class="pagination"><select>'
                '<option value="./">1-2章</option><option value="?page=2">3-4章</option>'
                '</select><a href="?page=2">下一页</a></div>',
            ),
            ROOT + "?page=2": toc(link(3) + link(4), extra='<a href="./">目录</a>'),
        }
    )
    book = discover(fake, ROOT)
    assert [number(r.title) for r in book.chapters] == [1, 2, 3, 4]
    assert book.chapters[0].url == book.chapters[1].url
    assert len(book.toc_pages) == 2


def test_descending_directory():
    refs = toc_refs(Page(ROOT, toc(link(3) + link(2) + link(1))))
    assert [number(r.title) for r in refs] == [1, 2, 3]


def test_navigation_list_does_not_beat_short_directory():
    menu = "<ul>" + "".join(f'<li><a href="/genre/{i}/">分类{i}</a></li>' for i in range(20)) + "</ul>"
    refs = toc_refs(Page(ROOT, menu + toc(link(1))))
    assert len(refs) == 1 and number(refs[0].title) == 1


def test_stale_unfinished_notice_on_numbered_final_page():
    html = chapter(1, fraction="（2 / 2）", links='<a href="2.html">下一章</a>')
    html = html.replace("</div>", '<p class="chapter-page-info">本章未完，点击下一页继续阅读。</p></div>')
    result = extract_chapter(Page(ROOT + "1_2.html", html))
    assert result.next_chapter == ROOT + "2.html"
    assert result.page_number == result.page_total == 2


def test_inline_markup_and_noise_preserve_paragraphs():
    html = '<h1>第1章 标题</h1><article><p>你<span>好</span>，<a href="/word/">世界</a>。</p><p>下一段。</p>'
    html += '<script>坏内容</script><div style="display: none"><span style="color:red">隐藏内容</span></div>'
    html += '<p class="chapter-page-info">本章已完</p></article>'
    assert extract_chapter(Page(ROOT, html), min_chars=1).text == "你好，世界。\n下一段。"


def test_paginated_chapter_and_next_boundary():
    fake = FakeFetcher(
        {
            ROOT + "1.html": chapter(1, "首页正文。" * 20, '<a href="1_2.html">下一页</a>', "（1 / 2）"),
            ROOT + "1_2.html": chapter(1, "末页正文。" * 20, '<a href="2.html">下一章</a>', "（2 / 2）"),
        }
    )
    result = read_chapter(fake, Ref("第1章 测试1", ROOT + "1.html"), Book("测试", ROOT), {}, 10, 80)
    assert len(result["pages"]) == 2
    assert "首页正文" in result["text"] and "末页正文" in result["text"]
    assert result["next_chapter"] == ROOT + "2.html"
    assert ROOT + "2.html" not in fake.calls


@pytest.mark.parametrize(
    "second",
    [
        chapter(2),
        chapter(1, "不同内容。" * 20, fraction="（3 / 3）"),
    ],
)
def test_bad_pagination_rejected(second):
    fake = FakeFetcher(
        {
            ROOT + "1.html": chapter(1, "相同内容。" * 20, '<a href="1_2.html">下一页</a>', "（1 / 3）"),
            ROOT + "1_2.html": second,
        }
    )
    with pytest.raises(DownloadError):
        read_chapter(fake, Ref("第1章 测试1", ROOT + "1.html"), Book("测试", ROOT), {}, 10, 80)


def test_duplicate_pagination_merged():
    fake = FakeFetcher(
        {
            ROOT + "1.html": chapter(1, "相同内容。" * 20, '<a href="1_2.html">下一页</a>', "（1 / 3）"),
            ROOT + "1_2.html": chapter(1, "相同内容。" * 20, '<a href="1_3.html">下一页</a>', "（2 / 3）"),
            ROOT + "1_3.html": chapter(1, "末页内容。" * 20, '<a href="2.html">下一章</a>', "（3 / 3）"),
        }
    )
    result = read_chapter(fake, Ref("第1章 测试1", ROOT + "1.html"), Book("测试", ROOT), {}, 10, 80)
    assert len(result["pages"]) == 2
    assert "相同内容" in result["text"] and "末页内容" in result["text"]
    assert len(result["duplicate_pages"]) == 1
    assert ROOT + "1_2.html" in result["duplicate_pages"]


def test_next_page_actually_points_to_next_chapter():
    fake = FakeFetcher({ROOT + "1.html": chapter(1, links='<a href="2.html">下一页</a>'), ROOT + "2.html": chapter(2)})
    result = read_chapter(fake, Ref("第1章 测试1", ROOT + "1.html"), Book("测试", ROOT), {}, 10, 80)
    assert len(result["pages"]) == 1
    assert result["next_chapter"] == ROOT + "2.html"
    assert "第2章" not in result["text"]


def test_chain_repairs_directory_and_resumes_without_chapter_requests(tmp_path):
    pages = {
        ROOT: toc(link(1) + link(2, "1.html"), latest="第3章 测试3"),
        ROOT + "1.html": chapter(1, links='<a href="2.html">下一章</a>'),
        ROOT + "2.html": chapter(2, links='<a href="3.html">下一章</a>'),
        ROOT + "3.html": chapter(3),
    }
    fake = FakeFetcher(pages)
    book = discover(fake, ROOT)
    folder, records, state = download(fake, book, tmp_path)
    assert state["status"] == "complete"
    assert len(records) == 3 and len(state["repairs"]) == 1
    assert (folder / "manifest.json").is_file()
    fake.calls.clear()
    _, records, state = download(fake, book, tmp_path)
    assert not fake.calls
    assert len(records) == 3 and state["status"] == "complete"
    cache = next((folder / "chapters").glob("*.json"))
    cached = json.loads(cache.read_text(encoding="utf-8"))
    cached["text"] = "损坏正文"
    cache.write_text(json.dumps(cached), encoding="utf-8")
    download(fake, book, tmp_path)
    assert fake.calls == [cached["url"]]


def test_missing_latest_and_limits_cannot_claim_complete(tmp_path):
    fake = FakeFetcher({ROOT: toc(link(1), latest="第10章 测试10"), ROOT + "1.html": chapter(1)})
    _, _, state = download(fake, discover(fake, ROOT), tmp_path)
    assert state["status"] == "incomplete" and not state["latest_reached"]
    fake.pages[ROOT] = toc(link(1))
    book = discover(fake, ROOT)
    _, _, state = download(fake, book, tmp_path, limit=1)
    # Even a one-chapter test run must not accidentally advertise a full download.
    assert state["status"] == "incomplete"


def test_failed_chapter_does_not_prevent_later_chapters(tmp_path):
    fake = FakeFetcher({ROOT: toc(link(1) + link(2) + link(3)), ROOT + "1.html": chapter(1), ROOT + "3.html": chapter(3)})
    _, chapters, state = download(fake, discover(fake, ROOT), tmp_path)
    assert len(chapters) == 2 and state["status"] == "incomplete"
    assert len(state["failures"]) == 1 and len(state["missing"]) == 1
    assert any("缺口" in issue for issue in state["issues"])


def test_duplicate_title_in_chain_is_not_silently_discarded(tmp_path):
    fake = FakeFetcher(
        {
            ROOT: toc(link(1)),
            ROOT + "1.html": chapter(1, links='<a href="other.html">下一章</a>'),
            ROOT + "other.html": chapter(1, body="另一份不同的正文。" * 20),
        }
    )
    _, _, state = download(fake, discover(fake, ROOT), tmp_path)
    assert state["status"] == "incomplete"
    assert any("相同章节标题" in issue for issue in state["issues"])


def test_challenge_not_saved_as_body(tmp_path):
    fake = FakeFetcher({ROOT: toc(link(1)), ROOT + "1.html": "<title>Just a moment...</title>" + chapter(1)})
    folder, chapters, state = download(fake, discover(fake, ROOT), tmp_path)
    assert not chapters and state["status"] == "incomplete"
    assert not list((folder / "chapters").glob("*.json"))


def test_http_headers_referer_cookie_and_retry():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text="<h1>测试</h1>", headers={"set-cookie": "session=abc; Path=/"})

    with Fetcher(delay=0, jitter=0, headers={"User-Agent": "test-browser"}) as fetcher:
        fetcher.client.close()
        fetcher.client = httpx.Client(transport=httpx.MockTransport(handler), headers=fetcher.headers)
        fetcher.get(ROOT)
        fetcher.get(ROOT + "1.html", referer=ROOT)
    assert requests[1].headers["user-agent"] == "test-browser"
    assert requests[1].headers["referer"] == ROOT
    assert requests[1].headers["cookie"] == "session=abc"
    assert "zh-CN" in requests[1].headers["accept-language"]


def test_http_retry_on_transient_status_only(monkeypatch):
    import novel_get.fetch as transport

    monkeypatch.setattr(transport.time, "sleep", lambda _: None)
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503)
        return httpx.Response(200, text="<h1>恢复</h1>")

    with Fetcher(delay=0, retries=1) as fetcher:
        fetcher.client.close()
        fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
        assert "恢复" in fetcher.get(ROOT).html
    assert len(attempts) == 2
    attempts.clear()

    def forbidden(request):
        attempts.append(request)
        return httpx.Response(403)

    with Fetcher(delay=0, retries=3) as fetcher:
        fetcher.client.close()
        fetcher.client = httpx.Client(transport=httpx.MockTransport(forbidden))
        with pytest.raises(DownloadError, match="HTTP 403"):
            fetcher.get(ROOT)
    assert len(attempts) == 1


def test_captcha_redirect_is_reported_as_verification():
    from novel_get.parse import soup_for

    with pytest.raises(DownloadError, match="验证"):
        soup_for(Page(ROOT + "WAF/VERIFY/CAPTCHA?info=test", "<title>身份验证</title>"))


def test_cli_multiple_sources_failure_and_epub(tmp_path, monkeypatch):
    import novel_get.cli as cli

    pages = {ROOT: toc(link(1)), ROOT + "1.html": chapter(1)}
    fake = FakeFetcher(pages)

    class Context:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return fake

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(cli, "Fetcher", Context)
    code = main(["download", ROOT, "https://unavailable.example/", "-o", str(tmp_path), "--merge", "合集", "--quiet"])
    assert code == 2
    report = json.loads((tmp_path / "run-report.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in report] == ["complete", "failed"]
    assert "合集不完整" in (tmp_path / "合集.txt").read_text(encoding="utf-8")
    with zipfile.ZipFile(tmp_path / "合集.epub") as archive:
        assert archive.infolist()[0].filename == "mimetype"
        assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
        for name in archive.namelist():
            if name.endswith((".xml", ".opf", ".xhtml")):
                ElementTree.fromstring(archive.read(name))


def test_epub_escapes_text(tmp_path):
    path = tmp_path / "test.epub"
    export_epub(path, "标题 & <内容>", [{"title": "章节", "text": "内容 <>&"}])
    with zipfile.ZipFile(path) as archive:
        assert "&lt;&gt;&amp;" in archive.read("OEBPS/chapter-00001.xhtml").decode()


def test_invalid_config_is_clear_error(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text('[sites.test]\ndomains = ["books.example"]\ncontent_selector = "["', encoding="utf-8")
    assert main(["inspect", ROOT, "--config", str(config)]) == 2
    assert "错误" in capsys.readouterr().err

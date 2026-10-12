import json

import pytest

from novel_get.cli import main
from novel_get.engine import discover, download
from novel_get.fetch import Page
from novel_get.settings import load_site_configs


def write_config(tmp_path, text, name="sites.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8-sig")
    return path


@pytest.mark.parametrize("host", ["wodushu.com", "WWW.WODUSHU.COM", "m.wodushu.com:8443", "www.wodushu.com."])
def test_builtin_domain_matching(tmp_path, host):
    assert load_site_configs(directory=tmp_path).resolve(f"https://{host}/book/") == {"title_selector": ".title"}


@pytest.mark.parametrize("host", ["fakewodushu.com", "wodushu.com.evil.org", "unrelated.example"])
def test_builtin_does_not_match_unrelated_domains(tmp_path, host):
    assert load_site_configs(directory=tmp_path).resolve(f"https://{host}/wodushu.com") == {}


def test_layers_specificity_and_partial_overrides(tmp_path):
    auto = tmp_path / "auto"
    auto.mkdir()
    write_config(auto, '''
[sites.mobile]
domains = ["m.wodushu.com"]
content_selector = "#mobile"
[sites.narrow]
domains = ["*.sub.wodushu.com"]
content_selector = "#narrow"
[sites.general]
domains = ["*.wodushu.com"]
content_selector = "#general"
remove_selector = ".ad"
''', "10-first.toml")
    write_config(auto, '''
[sites.override]
domains = ["m.wodushu.com"]
title_selector = "h2"
''', "20-second.toml")
    registry = load_site_configs(directory=auto)
    assert registry.resolve("https://m.wodushu.com/") == {
        "content_selector": "#mobile", "title_selector": "h2", "remove_selector": ".ad",
    }
    assert registry.resolve("https://a.sub.wodushu.com/")["content_selector"] == "#narrow"
    assert registry.resolve("https://wodushu.com/") == {"title_selector": ".title"}
    explicit = write_config(tmp_path, '''
[sites.manual]
domains = ["*.wodushu.com"]
title_selector = ""
''')
    registry = load_site_configs([explicit], directory=auto)
    assert registry.resolve("https://m.wodushu.com/") == {
        "content_selector": "#mobile", "title_selector": "", "remove_selector": ".ad",
    }
    assert registry.resolve("https://unrelated.example/") == {}


def test_json_site_config_rejected_before_network(tmp_path, monkeypatch, capsys):
    import novel_get.cli as cli

    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path, '{"content_selector": "article"}', "site.json")

    def no_fetch(**kwargs):
        pytest.fail("unsupported configuration must fail before creating a fetcher")

    monkeypatch.setattr(cli, "Fetcher", no_fetch)
    assert main(["inspect", "https://example.com/", "--config", str(path)]) == 2
    error = capsys.readouterr().err
    assert str(path) in error
    assert "仅支持 .toml" in error


def test_equal_specificity_and_explicit_file_order(tmp_path):
    first = write_config(tmp_path, '''
[sites.first]
domains = ["books.example"]
title_selector = "h1"
[sites.second]
domains = ["books.example"]
title_selector = "h2"
''', "first.toml")
    second = write_config(tmp_path, '''
[sites.third]
domains = ["books.example"]
title_selector = "h3"
''', "second.toml")
    assert load_site_configs([first], directory=tmp_path / "missing").resolve("https://books.example/")["title_selector"] == "h2"
    assert load_site_configs([second, first], directory=tmp_path / "missing").resolve("https://books.example/")["title_selector"] == "h2"
    assert load_site_configs([first, second], directory=tmp_path / "missing").resolve("https://books.example/")["title_selector"] == "h3"


def test_unicode_domains(tmp_path):
    path = write_config(tmp_path, '[sites.cn]\ndomains = ["例子.测试"]\ntitle_selector = "h2"')
    registry = load_site_configs([path], directory=tmp_path / "missing")
    for domain in ("例子.测试", "xn--fsqu00a.xn--0zwm56d"):
        assert registry.resolve(f"https://{domain}/") == {"title_selector": "h2"}


@pytest.mark.parametrize("text", [
    '[sites.bad',
    '[wrong]\nvalue = "x"',
    'sites = []',
    '[sites.bad]\ntitle_selector = "h1"',
    '[sites.bad]\ndomains = []',
    '[sites.bad]\ndomains = "example.com"',
    '[sites.bad]\ndomains = [1]',
    '[sites.bad]\ndomains = ["https://example.com"]',
    '[sites.bad]\ndomains = ["example.com/path"]',
    '[sites.bad]\ndomains = ["example.com:80"]',
    '[sites.bad]\ndomains = ["*example.com"]',
    '[sites.bad]\ndomains = ["example.com"]\nunknown_selector = "h1"',
    '[sites.bad]\ndomains = ["example.com"]\ntitle_selector = 1',
    '[sites.bad]\ndomains = ["example.com"]\ntitle_selector = "["',
])
def test_invalid_config_rejected_before_network(tmp_path, monkeypatch, capsys, text):
    import novel_get.cli as cli

    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path, text)

    def no_fetch(**kwargs):
        pytest.fail("invalid configuration must fail before creating a fetcher")

    monkeypatch.setattr(cli, "Fetcher", no_fetch)
    assert main(["inspect", "https://example.com/", "--config", str(path)]) == 2
    assert str(path) in capsys.readouterr().err


class PageFetcher:
    def __init__(self, pages, **kwargs):
        self.pages = pages
        self.calls = []

    def get(self, url, referer=None):
        self.calls.append(url)
        return self.pages[url]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def test_cli_auto_config_batch_redirect_and_pagination(tmp_path, monkeypatch, capsys):
    import novel_get.cli as cli

    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "config" / "sites"
    directory.mkdir(parents=True)
    write_config(directory, '''
[sites.first]
domains = ["first.example"]
toc_selector = ".catalog a"
toc_next_selector = "a.more"
title_selector = ".heading"
content_selector = ".body"
next_page_selector = "a.continue"
remove_selector = ".noise"
[sites.second]
domains = ["second.example"]
toc_selector = ".entries a"
title_selector = "h2"
content_selector = "main"
''')
    start = "https://redirect.example/book/"
    first = "https://first.example/book/"
    second = "https://second.example/book/"
    fake = PageFetcher({
        start: Page(first, '<h1>甲</h1><section class="catalog"><a href="1">第1章 开始</a></section><a class="more" href="?p=2">更多</a>'),
        first + "?p=2": Page(first + "?p=2", '<section class="catalog"><a href="1">第1章 开始</a></section>'),
        first + "1": Page(first + "1", '<b class="heading">第1章 开始</b><section class="body">首页正文<span class="noise">广告</span></section>'
                          '<a class="continue" href="1_2">继续</a>'),
        first + "1_2": Page(first + "1_2", '<b class="heading">第1章 开始</b><section class="body">末页正文</section>'),
        second: Page(second, '<h1>乙</h1><section class="entries"><a href="1">第1章 开始</a></section>'),
        second + "1": Page(second + "1", '<h2>第1章 开始</h2><main>另一本正文</main>'),
    })
    monkeypatch.setattr(cli, "Fetcher", lambda **kwargs: fake)
    output = tmp_path / "out"
    assert main(["download", start, second, "-o", str(output), "--format", "txt", "--min-chars", "1", "--quiet"]) == 0
    report = json.loads((output / "run-report.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in report] == ["complete", "complete"]
    texts = [path.read_text(encoding="utf-8") for path in output.glob("*/book.txt")]
    assert any("首页正文\n末页正文" in text and "广告" not in text for text in texts)
    assert any("另一本正文" in text for text in texts)
    capsys.readouterr()
    assert main(["inspect", start, "--quiet"]) == 0
    inspection = json.loads(capsys.readouterr().out)[0]
    assert inspection["url"] == first
    assert inspection["site_config"]["title_selector"] == ".heading"


def test_builtin_and_changed_config_invalidates_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = "https://www.wodushu.com/book/"
    fake = PageFetcher({
        root: Page(root, '<h1>测试</h1><div id="list"><a href="1">第1章 开始</a></div>'),
        root + "1": Page(root + "1", '<b class="title">第1章 开始</b><div id="content">正确正文<span class="noise">广告</span></div>'),
    })
    book = discover(fake, root)
    _, records, _ = download(fake, book, tmp_path / "out", min_chars=1)
    assert records[0]["title"] == "第1章 开始"
    fake.calls.clear()
    download(fake, book, tmp_path / "out", min_chars=1)
    assert fake.calls == []
    path = write_config(tmp_path, '[sites.clean]\ndomains = ["*.wodushu.com"]\nremove_selector = ".noise"')
    _, records, _ = download(fake, book, tmp_path / "out", config=load_site_configs([path]), min_chars=1)
    assert fake.calls == [root + "1"]
    assert records[0]["text"] == "正确正文"

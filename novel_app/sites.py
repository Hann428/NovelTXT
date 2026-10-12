"""Small, evidence-based site adapters; no CAPTCHA solving or fingerprint spoofing."""
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from novel_get.fetch import DownloadError, SiteBlocked, Page
from novel_get.parse import book_info
from .response import inspect_body


def site_config(url):
    host = urlsplit(url).hostname
    if host in {"www.banshanren.com", "banshanren.com"}:
        parts = urlsplit(url).path.strip("/").split("/")
        slug = parts[1] if len(parts) > 1 and parts[0] == "novel" else ""
        return {
            "toc_selector": f'a[href^="/novel/{slug}/"][title]',
            "content_selector": ".chapter_content_box",
            "title_selector": ".chapter_content_box h2",
            "remove_selector": ".z, .nz, .comment_count, #captcha_box",
        }
    if host in {"m.41nr.com", "www.41nr.com", "41nr.com"}:
        return {"content_selector": "#nr1", "title_selector": "#nr_title",
                "remove_selector": "[data-novel-navigation]", "parser_revision": "41nr-2"}
    return {}


def get_book(page):
    book = book_info(page)
    soup = BeautifulSoup(page.html, "html.parser")
    if "banshanren.com" in (urlsplit(page.url).hostname or ""):
        heading = soup.find("h1")
        if heading:
            book.title = heading.get_text(strip=True)
        elif soup.title:
            match = re.search(r"《(.*?)》", soup.title.get_text())
            if match:
                book.title = match[1]
    book.title, book.author = clean_book_identity(book.title, book.author, page.url)
    return book


def clean_book_identity(title, author='', url=''):
    """Remove verified 41nr SEO wrappers, including in older saved metadata."""
    title, author = str(title or '未命名小说').strip(), str(author or '').strip()
    if urlsplit(url).hostname in {'m.41nr.com', 'www.41nr.com', '41nr.com'}:
        online = re.search(r'(?:^|_)([^_]+?)[（(]([^()（）]+)[）)]在线阅读(?:\s*[-_|].*)?$', title)
        if online:
            title = online[1].strip()
            author = author or online[2].strip()
        else:
            title = re.split(r'(?:最新章节目录|最新章节|章节目录|在线阅读)', title, maxsplit=1)[0].strip(' _-')
        # The site's indexed title appends this source label to the book name.
        title = re.sub(r'书包网$', '', title).strip()
    author = re.sub(r'^作者\s*[:：]\s*', '', author).strip()
    return title or '未命名小说', author


def book_label(title, author=''):
    return f'{title}（{author}）' if author else title


def guard_body(page, selector):
    soup = BeautifulSoup(page.html, "html.parser")
    body = soup.select_one(selector) if selector else None
    if body is None:
        return
    text = body.get_text(" ", strip=True)
    inspect_body(page, body)
    if body.select_one("[data-encrypted], .encrypted") or (len(text) > 150 and re.fullmatch(r"[A-Za-z0-9+/=\s]+", text)):
        raise DownloadError("正文仍是编码数据；请使用浏览器模式，等待网页自身显示正文")
    private = re.findall(r'[\ue000-\uf8ff]', text)
    # Three real 41nr failures contain this original source marker at paragraph
    # starts, alongside ordinary Chinese prose. Preserve it exactly; do not
    # guess a font mapping or weaken guards for unknown encoded characters.
    source_marker = (urlsplit(page.url).hostname in {'m.41nr.com','www.41nr.com','41nr.com'}
                     and set(private) == {'\ue4c6'} and len(text) >= 200
                     and len(re.findall(r'[\u4e00-\u9fff]', text)) >= 100
                     and len(private) <= max(4, len(text)//100)
                     and all(re.fullmatch(r'\s*\ue4c6+[ \t]*[^\ue4c6]*', line)
                             for node in body.find_all(string=True) for line in str(node).splitlines() if '\ue4c6' in line))
    if "\ufffd" in text or (private and not source_marker):
        raise DownloadError("正文存在替换字符或私用区字符，无法确认文字正确；已拒绝导出")
    if len(body.select("img")) > 0 and len(text) < 80:
        raise DownloadError("正文可能是图片，目前不能无损转换为 TXT")


def extract_readable(page, config):
    """Short tails need explicit numbering; Runner also checks prior-page continuity."""
    from novel_get.parse import extract_chapter
    options = {key: value for key, value in config.items()
               if not key.startswith('toc_') and key != 'parser_revision'}
    parsed = extract_chapter(page, min_chars=1, **options)
    final_tail = parsed.page_number is not None and parsed.page_number > 1 and parsed.page_number == parsed.page_total
    if len(parsed.text) < 40 and not final_tail:
        raise DownloadError(f'正文尚未恢复可读，正文过短 ({len(parsed.text)} < 40)，没有连续尾页证据：{page.url}')
    return parsed


def normalize_page(page):
    if urlsplit(page.url).hostname in {"m.41nr.com", "www.41nr.com", "41nr.com"}:
        soup = BeautifulSoup(page.html, "html.parser")
        for panel in soup.select("#nr1 > div"):
            label = re.sub(r"\s+", "", panel.get_text())
            if label in {"本章未完，请点击下方下一页继续阅读", "本章已完，请点击下方下一章翻阅新篇"}:
                panel["data-novel-navigation"] = "true"
        return Page(page.url, str(soup))
    # Verified against public chapter.css: .chapter_content_box p { text-indent: 2em; }
    if urlsplit(page.url).hostname in {"www.banshanren.com", "banshanren.com"}:
        soup = BeautifulSoup(page.html, "html.parser")
        for p in soup.select(".chapter_content_box p"):
            if "text-indent" not in p.get("style", ""):
                p["style"] = p.get("style", "") + ";text-indent:2em"
        return Page(page.url, str(soup))
    return page


def list_books(page):
    """List book links on the supplied page only. Never crawl an entire site."""
    from novel_get.parse import absolute, same_site
    soup = BeautifulSoup(page.html, "html.parser")
    found = {}
    for a in soup.select("a[href]"):
        url = absolute(page.url, a.get("href"))
        title = a.get("title") or a.get_text(" ", strip=True)
        if not url or not same_site(page.url, url) or not title:
            continue
        if re.fullmatch(r"/(?:novel/[^/]+|book/\d+)/?", urlsplit(url).path):
            found.setdefault(url, {"url": url, "title": title[:120]})
    return list(found.values())

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup

from .fetch import DownloadError, Page, check_access
from .settings import builtin_config

NUMBER = r"[0-9零〇一二两三四五六七八九十百千万]+"
CHAPTER = re.compile(rf"^(?:第\s*({NUMBER})\s*[章节回集]|({NUMBER})\s*[.、：:．]|chapter\s+(\d+))", re.I)
SPECIAL = re.compile(r"^(?:番外|序章|序言|楔子|引子|前言|后记|尾声|终章|大结局)")
PAGE_SUFFIX = re.compile(r"\s*[（(]\s*\d+\s*[/／]\s*\d+\s*[）)]\s*$")
TOC_SELECTOR = builtin_config()["parser"]["toc_containers"]
BODY_SELECTOR = builtin_config()["parser"]["content_containers"]
TITLE_SELECTOR = builtin_config()["parser"]["title_candidates"]
NOISE = builtin_config()["parser"]["remove_nodes"]


def absolute(base: str, href: str | None) -> str | None:
    if not href or href.strip().startswith(("#", "javascript:", "data:", "mailto:")):
        return None
    url = urldefrag(urljoin(base, href.strip()))[0]
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        return None
    return url


def same_site(a, b):
    return urlsplit(a).netloc.lower() == urlsplit(b).netloc.lower()


def number(title: str) -> int | None:
    match = CHAPTER.match(unicodedata.normalize("NFKC", title).strip())
    if not match:
        return None
    value = next(group for group in match.groups() if group is not None)
    if value.isdigit():
        return int(value)
    digits = dict(zip("零〇一二两三四五六七八九", (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    total = section = current = 0
    if not any(c in value for c in "十百千万"):
        return int("".join(str(digits[c]) for c in value))
    for char in value:
        if char in digits:
            current = digits[char]
        elif char == "万":
            total += (section + current) * 10000
            section = current = 0
        else:
            section += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            current = 0
    return total + section + current


def identity(title: str) -> str:
    return re.sub(r"\W+", "", unicodedata.normalize("NFKC", PAGE_SUFFIX.sub("", title))).lower()


@dataclass
class Ref:
    title: str
    url: str


@dataclass
class Book:
    title: str
    url: str
    author: str = ""
    latest_title: str = ""
    latest_url: str = ""
    chapters: list[Ref] = field(default_factory=list)
    toc_pages: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class ChapterPage:
    title: str
    text: str
    next_page: str | None
    next_chapter: str | None
    page_number: int | None = None
    page_total: int | None = None


def soup_for(page):
    soup = BeautifulSoup(page.html, "html.parser")
    from novel_app.response import check_page_response
    check_page_response(page)
    return soup


def meta(soup, key):
    element = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    return element.get("content", "").strip() if element else ""


def book_info(page: Page) -> Book:
    soup = soup_for(page)
    heading = soup.find("h1") or soup.title
    title = meta(soup, "og:novel:book_name") or meta(soup, "og:title")
    title = title or (heading.get_text(" ", strip=True) if heading else "未命名小说")
    return Book(
        title,
        page.url,
        meta(soup, "og:novel:author"),
        meta(soup, "og:novel:latest_chapter_name"),
        absolute(page.url, meta(soup, "og:novel:latest_chapter_url")) or "",
    )


def toc_refs(page: Page, selector: str | None = None) -> list[Ref]:
    soup = soup_for(page)

    def refs(nodes, strict=False):
        found = []
        for a in nodes:
            title = a.get_text(" ", strip=True)
            url = absolute(page.url, a.get("href"))
            if not url or not same_site(page.url, url) or url == page.url:
                continue
            if not CHAPTER.match(title) and re.fullmatch(r".*(?:下一页|上一页|下一章|上一章|目录|首页|尾页|开始阅读|加入书架|倒序|正序).*", title):
                continue
            if title and (not strict or CHAPTER.match(title) or SPECIAL.match(title)):
                found.append(Ref(title, url))
        return found

    if selector:
        result = refs(soup.select(selector))
        if not result:
            raise DownloadError(f"目录选择器未匹配任何章节链接: {selector}")
        return result
    candidates = []
    for container in soup.select(TOC_SELECTOR):
        items = refs(container.select("a[href]"))
        if not items:
            continue
        known = sum(bool(CHAPTER.match(r.title) or SPECIAL.match(r.title)) for r in items)
        marker = " ".join([container.get("id", ""), *container.get("class", [])])
        if not known and not re.search(r"chapter|directory|listmain|mulu|js_cover|^list$", marker, re.I):
            continue
        heading = container.find_previous(["h2", "h3", "dt"])
        recent = heading and "最新章节" in heading.get_text()
        score = known * 5 + len(items) - (1000 if recent else 0)
        candidates.append((score, items))
    if candidates:
        result = max(candidates, key=lambda x: x[0])[1]
    else:
        groups = defaultdict(list)
        for ref in refs(soup.select("a[href]"), strict=True):
            groups[urlsplit(ref.url).path.rsplit("/", 1)[0]].append(ref)
        result = max(groups.values(), key=len, default=[])
    nums = [number(r.title) for r in result]
    if len(nums) > 1 and all(n is not None for n in nums) and all(a > b for a, b in zip(nums, nums[1:])):
        result.reverse()
    return result


def toc_links(page: Page, chapter_urls: set[str], selector=None) -> list[str]:
    soup = soup_for(page)
    nodes = soup.select(selector) if selector else soup.select("a[href], select option[value]")
    result = []
    for node in nodes:
        label = re.sub(r"\s+", "", node.get_text())
        url = absolute(page.url, node.get("href") or node.get("value"))
        if not url or not same_site(page.url, url) or url in chapter_urls or url == page.url:
            continue
        pagination = node.find_parent(class_=re.compile(r"page|pager|index-container|pagination", re.I))
        is_next = label.strip("<>›»→") in {"下一页", "下页", "下页目录", "next", "Next", "下一页目录"}
        is_index = label in {"全部章节", "查看全部章节", "完整目录", "章节目录", "目录", "全部目录"}
        is_option = node.name == "option" and (pagination or re.search(r"\d+\s*[-~至]\s*\d+.*章", label))
        if selector or is_next or is_index or is_option or (pagination and label.isdigit()):
            result.append(url)
    return list(dict.fromkeys(result))


def extract_chapter(
    page: Page,
    *,
    content_selector=None,
    title_selector=None,
    next_page_selector=None,
    next_chapter_selector=None,
    remove_selector=None,
    min_chars=80,
) -> ChapterPage:
    soup = soup_for(page)
    title_node = soup.select_one(title_selector or TITLE_SELECTOR)
    title = title_node.get_text(" ", strip=True) if title_node else ""
    if not title and soup.title:
        title = re.split(r"[_|]", soup.title.get_text(" ", strip=True))[0]
    title = PAGE_SUFFIX.sub("", title).strip()
    if not title:
        raise DownloadError(f"无法识别章节标题: {page.url}")

    def cleaned(node):
        # Local fork: do not strip indentation, blank paragraphs, or title-like prose.
        from novel_app.text import preserve_text
        return preserve_text(node, NOISE + (", " + remove_selector if remove_selector else ""))

    candidates = soup.select(content_selector or BODY_SELECTOR)
    fallback = not candidates and not content_selector
    if fallback:
        candidates = soup.select("div, main, section")
    if not candidates:
        raise DownloadError(f"无法识别正文容器: {page.url}；可配置 content_selector")

    def score(node):
        length = len(node.get_text(strip=True))
        link_text = sum(len(a.get_text(strip=True)) for a in node.select("a"))
        return length - 3 * link_text - (len(node.select("div, section")) * 10 if fallback else 0)

    body = max(candidates, key=score)
    from novel_app.response import inspect_body
    inspect_body(page, body)
    text = cleaned(body)
    if len(text) < min_chars:
        raise DownloadError(f"正文过短 ({len(text)} < {min_chars})，可能是空页或加载失败: {page.url}")
    info = body.select_one(".chapter-page-info")
    pagination_text = (title_node.get_text() if title_node else "") + (info.get_text() if info else "")
    fraction = re.search(r"[（(]\s*(\d+)\s*[/／]\s*(\d+)\s*[）)]", pagination_text)
    current, total = (int(fraction[1]), int(fraction[2])) if fraction else (None, None)
    next_page = next_chapter = None
    for a in soup.select("a[href]"):
        label = re.sub(r"\s+", "", a.get_text()).strip("<>›»→")
        url = absolute(page.url, a.get("href"))
        if not url or not same_site(url, page.url):
            continue
        if label in {"下一页", "下页", "继续阅读", "下一頁"}:
            next_page = url
        elif label in {"下一章", "下章", "下一篇", "下一节", "下一章→"}:
            next_chapter = url
        elif "next" in a.get("rel", []) and not next_chapter and not next_page:
            next_page = url  # Ambiguous rel=next is checked against the next page's title.
    for selector, kind in ((next_page_selector, "page"), (next_chapter_selector, "chapter")):
        if selector:
            element = soup.select_one(selector)
            url = absolute(page.url, element.get("href")) if element else None
            if url and not same_site(url, page.url):
                raise DownloadError(f"分页链接跳向其他站点: {url}")
            if kind == "page":
                next_page = url
            else:
                next_chapter = url
    if current is not None and current < total and not next_page:
        raise DownloadError(f"章节声明 {total} 页，但第 {current} 页没有下一页链接: {page.url}")
    if "本章未完" in body.get_text() and not next_page and not (current and current == total):
        raise DownloadError(f"章节未完却找不到下一页: {page.url}")
    return ChapterPage(title, text, next_page, next_chapter, current, total)

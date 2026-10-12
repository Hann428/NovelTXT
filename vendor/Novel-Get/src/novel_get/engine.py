from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from .fetch import DownloadError, SiteBlocked
from .parse import Book, Ref, book_info, extract_chapter, identity, number, same_site, toc_links, toc_refs
from .settings import builtin_config, resolve_config


def atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:100] or "novel"
    if value.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        value = "_" + value
    return value


def discover(fetcher, url, config=None, max_pages=200, progress=lambda _: None):
    page = fetcher.get(url)
    config = resolve_config(config, page.url)
    book = book_info(page)
    queue = [page.url]
    seen, entries = set(), set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        if len(seen) >= max_pages:
            book.issues.append(f"目录页数达到上限 {max_pages}")
            break
        try:
            if current != page.url:
                page = fetcher.get(current, referer=book.url)
            if not same_site(book.url, page.url):
                raise DownloadError(f"目录重定向到其他站点: {page.url}")
            refs = toc_refs(page, config.get("toc_selector"))
            seen.add(current)
            seen.add(page.url)
            book.toc_pages.append(page.url)
            for ref in refs:
                key = (identity(ref.title), ref.url)
                if key not in entries:
                    book.chapters.append(ref)
                    entries.add(key)
            progress(f"目录 {len(book.toc_pages)} 页，已发现 {len(book.chapters)} 条章节记录")
            links = toc_links(page, {r.url for r in book.chapters}, config.get("toc_next_selector"))
            queue.extend(u for u in links if u not in seen and u not in queue)
        except SiteBlocked:
            raise
        except DownloadError as exc:
            book.issues.append(str(exc))
            seen.add(current)
    if not book.chapters:
        raise DownloadError("没有找到章节目录；可尝试 --browser 或配置 toc_selector（应选中 a 链接）")
    return book


def read_chapter(fetcher, ref, book, config, max_pages, min_chars):
    pages, parts, seen = [], [], set()
    current, title, next_chapter = ref.url, None, None
    previous_number = None
    expected_total = None
    duplicate_pages = []
    while current:
        if current in seen:
            raise DownloadError(f"章节分页出现循环: {current}")
        if len(pages) >= max_pages:
            raise DownloadError(f"章节分页超过上限 {max_pages}: {ref.url}")
        seen.add(current)
        page = fetcher.get(current, referer=pages[-1] if pages else book.url)
        if not same_site(book.url, page.url):
            raise DownloadError(f"章节重定向到其他站点: {page.url}")
        if page.url in pages:
            raise DownloadError(f"章节分页重定向回已读取页面: {page.url}")
        parsed = extract_chapter(
            page,
            min_chars=min_chars,
            **{
                key: config[key]
                for key in (
                    "content_selector",
                    "title_selector",
                    "next_page_selector",
                    "next_chapter_selector",
                    "remove_selector",
                )
                if key in config
            },
        )
        if title and identity(title) != identity(parsed.title):
            if expected_total and len(pages) < expected_total - len(duplicate_pages):
                raise DownloadError(f"分页未读完却跳到其他章节: {current}")
            next_chapter = current
            break
        title = parsed.title
        if not pages and parsed.page_number not in {None, 1}:
            raise DownloadError(f"章节起始页是第 {parsed.page_number} 页: {ref.url}")
        if parsed.page_number is not None:
            if previous_number is not None and parsed.page_number != previous_number + 1:
                raise DownloadError(f"分页编号不连续: {current}")
            previous_number = parsed.page_number
            if expected_total is not None and expected_total != parsed.page_total:
                raise DownloadError(f"章节总页数发生变化: {current}")
            expected_total = parsed.page_total
        if parsed.text in parts:
            duplicate_pages.append(current)
            next_chapter = parsed.next_chapter
            current = parsed.next_page
            continue
        pages.append(page.url)
        parts.append(parsed.text)
        next_chapter = parsed.next_chapter
        current = parsed.next_page
        if not current and expected_total and len(pages) != expected_total - len(duplicate_pages):
            raise DownloadError(f"章节只读到 {len(pages)}/{expected_total} 页: {ref.url}")
    text = "\n".join(parts)
    return {
        "url": ref.url,
        "title": title,
        "text": text,
        "pages": pages,
        "next_chapter": next_chapter,
        "sha256": digest(text),
        "duplicate_pages": duplicate_pages,
    }


def download(
    fetcher,
    book: Book,
    output: Path,
    *,
    config=None,
    max_chapters=10000,
    max_pages=100,
    limit=None,
    min_chars=80,
    refresh=False,
    progress=lambda _: None,
):
    config = resolve_config(config, book.url)
    folder = output / f"{safe_name(book.title)}-{digest(book.url)[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    cache_dir = folder / "chapters"
    cache_dir.mkdir(exist_ok=True)
    fingerprint = digest(json.dumps(
        {"parser": 1, "parser_config": builtin_config()["parser"], "config": config, "min_chars": min_chars}, sort_keys=True,
    ))
    records, failures, repairs = [], [], []
    attempted, covered = set(), set()
    issues = list(book.issues)
    if limit is not None:
        issues.append(f"本次使用 --limit {limit}，仅为试下载")
    pending = list(book.chapters)
    paths = [urlsplit(ref.url).path.rsplit("/", 1)[0] + "/" for ref in pending]
    scope = os.path.commonpath(paths).replace("\\", "/")
    scope = scope.rstrip("/") + "/" if scope != "/" else scope
    state = {
        "schema": 1,
        "book": asdict(book),
        "status": "in_progress",
        "chapters": [],
        "failures": [],
        "issues": [],
        "repairs": repairs,
        "scope": "available_source_snapshot",
    }

    def save_state(status):
        state.update(
            status=status,
            chapters=[{k: v for k, v in r.items() if k != "text"} for r in records],
            failures=failures,
            issues=list(dict.fromkeys(issues)),
            repairs=repairs,
        )
        write_json(folder / "manifest.json", state)

    save_state("in_progress")
    try:
        while pending:
            if limit is not None and len(records) >= limit:
                break
            if len(records) >= max_chapters or len(attempted) >= max_chapters:
                issues.append(f"章节数达到上限 {max_chapters}")
                break
            ref = pending.pop(0)
            if identity(ref.title) in covered or ref.url in attempted:
                continue
            attempted.add(ref.url)
            try:
                cache_file = cache_dir / f"{digest(ref.url)}.json"
                record = None
                if cache_file.exists() and not refresh:
                    try:
                        cached = json.loads(cache_file.read_text(encoding="utf-8"))
                        if (
                            isinstance(cached, dict)
                            and cached.get("fingerprint") == fingerprint
                            and cached.get("url") == ref.url
                            and isinstance(cached.get("text"), str)
                            and cached.get("sha256") == digest(cached["text"])
                            and isinstance(cached.get("title"), str)
                            and cached["title"]
                            and isinstance(cached.get("pages"), list)
                            and cached["pages"]
                            and all(isinstance(page, str) for page in cached["pages"])
                            and "next_chapter" in cached
                            and (cached["next_chapter"] is None or isinstance(cached["next_chapter"], str))
                        ):
                            record = cached
                    except (ValueError, OSError):
                        pass
                resumed = record is not None
                if record is None:
                    record = read_chapter(fetcher, ref, book, config, max_pages, min_chars)
                    record["fingerprint"] = fingerprint
                    write_json(cache_file, record)
                key = identity(record["title"])
                if key not in covered:
                    records.append(record)
                    covered.add(key)
                    duplicate_pages = record.get("duplicate_pages", [])
                    if duplicate_pages:
                        issues.append(f"章节 {record['title']} 跳过 {len(duplicate_pages)} 个重复分页: {', '.join(duplicate_pages)}")
                    progress(f"[{len(records)}] {'缓存' if resumed else '下载'} {record['title']} ({len(record['pages'])} 页)")
                else:
                    issues.append(f"不同 URL 返回相同章节标题，需核对分页或重复内容: {record['title']} ({ref.url})")
                for declared in book.chapters:
                    if identity(declared.title) == key and declared.url != record["url"]:
                        repairs.append({"title": declared.title, "toc_url": declared.url, "actual_url": record["url"]})
                next_url = record.get("next_chapter")
                if next_url and next_url not in book.toc_pages:
                    if not same_site(book.url, next_url) or not urlsplit(next_url).path.startswith(scope):
                        issues.append(f"下一章离开当前目录范围，已停止跟随: {next_url}")
                    elif next_url in attempted:
                        issues.append(f"下一章链接形成循环: {next_url}")
                    else:
                        pending.insert(0, Ref("", next_url))
                save_state("in_progress")
            except DownloadError as exc:
                failures.append({"title": ref.title, "url": ref.url, "error": str(exc)})
                progress(f"失败: {ref.title or ref.url}: {exc}")
                save_state("in_progress")
                if isinstance(exc, SiteBlocked):
                    state["blocked"] = True
                    issues.append("检测到访问限制，已停止本次任务；保留缓存，请勿立即重试")
                    break
    except KeyboardInterrupt:
        issues.append("用户中断；重新运行相同命令可续传")
        save_state("interrupted")
        raise

    missing = [asdict(r) for r in book.chapters if identity(r.title) not in covered]
    if missing:
        issues.append(f"目录中仍有 {len(missing)} 条章节记录未核实")
    latest_reached = not book.latest_title and not book.latest_url
    if book.latest_title and identity(book.latest_title) in covered:
        latest_reached = True
    if book.latest_url and any(book.latest_url in r["pages"] for r in records):
        latest_reached = True
    if not latest_reached:
        issues.append(f"未到达站点声明的最新章节: {book.latest_title or book.latest_url}")
    nums = [number(r["title"]) for r in records]
    numbered = [n for n in nums if n is not None]
    if numbered and len(numbered) == len(set(numbered)):
        ordered = sorted(numbered)
        if ordered[0] > 1:
            issues.append(f"已下载章节从第 {ordered[0]} 章开始，无法确认开头完整")
        gaps = [(a + 1, b - 1) for a, b in zip(ordered, ordered[1:]) if b - a > 1]
        if gaps:
            issues.append("章号有缺口: " + ", ".join(f"{a}-{b}" for a, b in gaps))
        if len(numbered) == len(records):
            records.sort(key=lambda r: number(r["title"]))
    elif len(numbered) != len(set(numbered)):
        issues.append("章号重复或分卷重置，需核对章节顺序")
    texts = {}
    for record in records:
        if record["sha256"] in texts:
            issues.append(f"重复正文: {texts[record['sha256']]} / {record['title']}")
        texts[record["sha256"]] = record["title"]
    state["missing"] = missing
    state["latest_reached"] = latest_reached
    state["downloaded"] = len(records)
    save_state("complete" if not issues and not failures else "incomplete")
    return folder, records, state

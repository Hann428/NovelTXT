from dataclasses import asdict
from contextlib import contextmanager
import hashlib
from datetime import datetime
from pathlib import Path
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from urllib.parse import urlsplit

from novel_get.fetch import DownloadError, SiteBlocked, Page
from novel_get.parse import Book, Ref, toc_refs, toc_links, extract_chapter, identity, number, same_site
from novel_get.engine import safe_name

from .network import Cancelled, PageHTTPError, AccessDenied, StopWithoutVerification, check_page_response, validate_url
from .sites import get_book, site_config, guard_body, normalize_page, clean_book_identity, book_label, extract_readable
from .storage import digest, save, load, write_text, JobLock

PARSER_VERSION = "han-txt-0.1.0-3"
RAW_VERSION = "http-html-1"


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


@contextmanager
def export_target(output, name, url, control):
    """Keep the basename clean, serialize same-name writers, preserve other books."""
    output = Path(output)
    lock = JobLock(output / '.novel-cache' / '.export-locks' / digest(name.casefold()))
    deadline = time.monotonic() + 10
    while True:
        control.checkpoint()
        try:
            lock.__enter__()
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise DownloadError('同名 TXT 正在写入，请稍后继续；已有文件和缓存保留')
            control.cancel.wait(.1)
    try:
        target = output / name
        def available(path):
            report_path = path.with_suffix('.report.json')
            if path.is_symlink() or report_path.is_symlink():
                return False
            if not path.exists() and not report_path.exists():
                return True
            report = load(report_path, {}) or {}
            try:
                return (report.get('input_url') == url and bool(report.get('output_sha256'))
                        and hashlib.sha256(path.read_bytes()).hexdigest() == report['output_sha256'])
            except OSError:
                return False
        if not available(target):
            target = output / '同名书' / digest(url)[:16] / name
            if not available(target):
                target = target.parent / datetime.now().strftime('%Y%m%d-%H%M%S-%f') / name
                if not available(target):
                    raise DownloadError('同名输出目标已被占用，已有文件保留')
        yield target
    finally:
        lock.__exit__(None, None, None)


class Runner:
    def __init__(self, fetcher, output, control, *, limit=0, refresh=False):
        self.fetcher, self.output, self.control = fetcher, Path(output), control
        self.limit, self.refresh = limit, refresh
        self.folder = None
        self.state = {}

    def emit(self, kind, message, **data):
        self.control.emit(kind=kind, message=message, **data)

    def checkpoint(self, **data):
        self.state.update(data, updated_at=now())
        save(self.folder / "manifest.json", self.state)

    def discover(self, url):
        checkpoint = self.folder / "toc.json"
        state = load(checkpoint) if not self.refresh else None
        if not state or state.get("version") != PARSER_VERSION or state.get("input_url") != url:
            page = self.fetcher.get(url)
            book = get_book(page)
            config = site_config(page.url)
            state = {"version": PARSER_VERSION, "input_url": url, "book": asdict(book), "config": config,
                     "queue": [page.url], "seen": [], "chapters": [], "complete": False, "created_at": now()}
            save(checkpoint, state)
        else:
            page = None
            # Apply current adapters to a saved directory without fetching it again.
            config = {**state["config"], **site_config(url)}
            book = Book(**{k: v for k, v in state["book"].items() if k != "chapters"})
            book.title, book.author = clean_book_identity(book.title, book.author, url)
            if (state['book']['title'], state['book'].get('author', '')) != (book.title, book.author):
                state['book'].update(title=book.title, author=book.author)
                save(checkpoint, state)
            self.emit("info", f"继续目录快照（{state['created_at']}）；需要追更可勾选“刷新目录”")
        while state["queue"]:
            self.control.checkpoint()
            current = state["queue"][0]
            if current in state["seen"]:
                state["queue"].pop(0)
                continue
            if len(state["seen"]) >= 300:
                raise DownloadError("目录超过 300 页，已保留进度，需要人工确认范围")
            if page is None or page.url != current:
                page = self.fetcher.get(current, referer=book.url)
            if not same_site(book.url, page.url):
                raise DownloadError("目录跳转到其他站点，已停止")
            refs = toc_refs(page, config.get("toc_selector"))
            if not refs:
                raise DownloadError("该页未识别到章节目录，请提供目录页，或使用“解析列表”选择书籍")
            entries = {ref["url"] for ref in state["chapters"]}
            for ref in refs:
                if ref.url not in entries:
                    state["chapters"].append(asdict(ref))
                    entries.add(ref.url)
            state["seen"].append(current)
            state["queue"].pop(0)
            for link in toc_links(page, entries, config.get("toc_next_selector")):
                # Prevent a broken directory link from crawling recommendations or other books.
                base_path = urlsplit(book.url).path.rstrip("/")
                link_path = urlsplit(link).path
                in_scope = link_path == base_path or link_path.startswith(base_path + "/")
                if not in_scope:
                    raise DownloadError("目录分页离开当前书籍范围，需人工核对")
                if link not in state["seen"] and link not in state["queue"]:
                    state["queue"].append(link)
            state["complete"] = not state["queue"]
            save(checkpoint, state)
            self.checkpoint(status="discovering", title=book.title, toc_pages=len(state["seen"]), total=len(state["chapters"]))
            self.emit("toc", f"目录 {len(state['seen'])} 页，已发现 {len(state['chapters'])} 章",
                      total=len(state["chapters"]), toc_pages=len(state['seen']), title=book.title)
        book.chapters = [Ref(**item) for item in state["chapters"]]
        book.toc_pages = state["seen"]
        if not book.chapters:
            raise DownloadError("目录为空，无法开始下载")
        return book, config

    def get_page(self, url, referer, config):
        path = self.folder / "pages" / (digest(url) + ".json")
        record = load(path)
        if record and record.get("version") in {RAW_VERSION, "han-txt-0.1.0-2"} and record.get("requested_url", record.get("url")) == url:
            html = record.get("html", "")
            if record.get("sha256") == digest(html):
                page = normalize_page(Page(record.get("url", url), html))
                check_page_response(page)
                guard_body(page, config.get("content_selector"))
                return page
        page = self.fetcher.get(url, referer=referer)
        page = normalize_page(page)
        try:
            check_page_response(page)
            guard_body(page, config.get("content_selector"))
        except SiteBlocked:
            save(self.folder / "last-blocked-page.json", {"url": url, "at": now(), "html": page.html})
            raise
        # Cache only pages which pass text extraction, not challenges or empty skeletons.
        extract_readable(page, config)
        save(path, {"version": RAW_VERSION, "requested_url": url, "url": page.url, "html": page.html, "sha256": digest(page.html)})
        return page

    def chapter(self, ref, book, config):
        url = ref.url
        pages, parts, seen = [], [], set()
        title, expected, previous = None, None, None
        next_chapter = None
        known = {r.url for r in book.chapters}
        issues = []
        while url:
            self.control.checkpoint()
            if url in seen or len(seen) >= 100:
                raise DownloadError("正文分页循环或超过 100 页，已停止该章")
            seen.add(url)
            try:
                page = self.get_page(url, pages[-1] if pages else book.url, config)
            except SiteBlocked as exc:
                if getattr(exc, 'url', None) == url:
                    exc.chapter_title, exc.chapter_url = ref.title, ref.url
                raise
            if not same_site(book.url, page.url):
                raise DownloadError("正文跳转到其他站点")
            parsed = extract_readable(page, config)
            if '\ue4c6' in parsed.text:
                issue = '源页面含段首私用区字符 U+E4C6，已原样保留；请核对显示效果'
                if issue not in issues:
                    issues.append(issue)
            if title is not None and identity(title) != identity(parsed.title):
                if expected and len(pages) < expected:
                    raise DownloadError("分页未读完却跳到其他章节")
                next_chapter = page.url
                break
            if title is None:
                title = parsed.title
                if identity(ref.title) != identity(title):
                    raise DownloadError("目录标题与正文标题不一致，已停止该章以避免串书或错章")
                if parsed.page_number not in {None, 1}:
                    raise DownloadError("章节不是从第一页开始")
            if parsed.page_number is not None:
                if previous is not None and parsed.page_number != previous + 1:
                    raise DownloadError("章节分页编号不连续")
                if expected is not None and expected != parsed.page_total:
                    raise DownloadError("章节总页数发生变化")
                previous, expected = parsed.page_number, parsed.page_total
            if parsed.text in parts:
                raise DownloadError("多个分页返回相同正文，需人工核对，未静默去重")
            pages.append(page.url)
            parts.append(parsed.text)
            self.control.emit(kind='page', url=page.url, chapter_title=ref.title,
                              page_current=len(pages), page_total=expected,
                              message=f'正文分页已保存 · {ref.title} · 第 {len(pages)}/{expected or "?"} 页（整章读完后章节数加 1）')
            next_chapter = parsed.next_chapter
            url = parsed.next_page
            if url in known and url != ref.url:
                next_chapter = url
                url = None
            if not url and expected and len(pages) != expected:
                raise DownloadError(f"章节分页不完整：{len(pages)}/{expected}")
        text = "\n".join(parts)
        if next_chapter and next_chapter not in known and next_chapter not in book.toc_pages:
            issues.append("正文指向目录外的下一章，可能存在目录遗漏")
        return {"version": PARSER_VERSION, "adapter": config.get("parser_revision"), "url": ref.url, "title": title, "text": text, "sha256": digest(text),
                "pages": pages, "next_chapter": next_chapter, "issues": issues}

    def run(self, url, inspect=False):
        validate_url(url)
        self.folder = self.output / ".novel-cache" / digest(url)[:16]
        self.folder.mkdir(parents=True, exist_ok=True)
        with JobLock(self.folder):
            self.state = {"version": PARSER_VERSION, "input_url": url, "started_at": now(), "status": "discovering",
                          "downloaded": 0, "total": 0, "failures": [], "issues": [], "output": None,
                          "scope": "下载时站点公开目录快照；不代表作品已完结"}
            self.checkpoint()
            try:
                book, config = self.discover(url)
                self.checkpoint(title=book.title, author=book.author, total=len(book.chapters), toc_complete=True)
                if inspect:
                    self.checkpoint(status="inspected")
                    return self.state
                return self.download(book, config)
            except Cancelled:
                self.checkpoint(status="stopped")
                raise
            except SiteBlocked as exc:
                details = dict(error_status=exc.status, error_url=exc.url) if isinstance(exc, StopWithoutVerification) else {}
                if getattr(exc, 'chapter_title', None):
                    details['error_chapter_title'] = exc.chapter_title
                self.checkpoint(status="blocked", error=str(exc), **details)
                raise
            except Exception as exc:
                self.checkpoint(status="failed", error=str(exc))
                raise

    def download(self, book, config):
        records, failures, issues = [], [], []
        indexed_records = {}
        failure_signatures = {}
        last_cache_report = 0
        cache_pending = False
        refs = book.chapters[:self.limit] if self.limit else book.chapters
        self.checkpoint(status="downloading")
        results = self.chapter_results(refs, book, config)
        try:
            for attempted, (index, ref, record, cached, error) in enumerate(results, 1):
                self.control.checkpoint()
                try:
                    if error:
                        raise error
                    records.append(record)
                    indexed_records[index] = record
                    failure_signatures.pop(index, None)
                    issues.extend(record.get("issues", []))
                    report = not cached or index == len(refs)-1 or time.monotonic()-last_cache_report >= .5
                    if report:
                        self.emit("chapter", f"{'缓存已校验' if cached else '已下载'} {len(records)}/{len(refs)} · {ref.title}",
                                  current=index+1, downloaded=len(records), total=len(refs), title=book.title)
                        last_cache_report = time.monotonic()
                except SiteBlocked:
                    raise
                except DownloadError as exc:
                    report = True
                    failure = {"title": ref.title, "url": ref.url, "reason": str(exc)}
                    if isinstance(exc, PageHTTPError):
                        failure.update(page_url=exc.url, http_status=exc.status)
                    failures.append(failure)
                    # The cache pass is yielded first and workers finish out of
                    # order. A sequence of missing cache entries is not a run
                    # of adjacent source chapters. Use original TOC indices.
                    signature = ((type(exc), exc.status) if isinstance(exc, PageHTTPError)
                                 else (type(exc), str(exc)))
                    if isinstance(exc, PageHTTPError) and exc.status in {404, 410}:
                        failure_signatures.pop(index, None)
                        repeated = False
                    else:
                        failure_signatures[index] = signature
                        repeated = any(all(failure_signatures.get(position) == signature
                                           for position in range(start, start + 3))
                                       for start in range(max(0, index - 2), index + 1))
                    self.emit("warning", f"该章待重试：{ref.title} · {exc}")
                    if repeated:
                        self.checkpoint(failures=failures)
                        raise DownloadError(f"连续三次出现相同错误（目录相邻三章），已暂停本书；已下载章节保留：{exc}")
                self.state.update(downloaded=len(records), attempted=attempted, failures=failures, issues=list(dict.fromkeys(issues)))
                if report:
                    self.checkpoint(downloaded=len(records), attempted=attempted, failures=failures, issues=list(dict.fromkeys(issues)))
                    cache_pending = False
                else:
                    cache_pending = True
        finally:
            results.close()
        records = [indexed_records[index] for index in sorted(indexed_records)]
        titles, texts = {}, {}
        for record in records:
            title_key = identity(record["title"])
            if title_key in titles:
                issues.append(f"同名章节均已保留，需要核对：{record['title']}")
            titles[title_key] = record["url"]
            if record["sha256"] in texts:
                issues.append(f"不同章节正文相同，需要核对：{record['title']}")
            texts[record["sha256"]] = record["url"]
        if not self.limit:
            reached_urls = {p for record in records for p in record['pages']}
            if book.latest_url and book.latest_url not in reached_urls:
                issues.append("未到达网站声明的最新章节地址")
            elif book.latest_title and identity(book.latest_title) not in titles:
                issues.append("未到达网站声明的最新章节标题")
        nums = [number(r["title"]) for r in records]
        numbered = [n for n in nums if n is not None]
        if numbered and len(set(numbered)) == len(numbered):
            if numbered[0] != 1:
                issues.append("开头章号不是 1，不能确认开头完整")
            if any(b != a+1 for a,b in zip(numbered, numbered[1:])):
                issues.append("章号存在缺口或顺序异常，请核对报告")
        if self.limit:
            status, suffix = "sample", "试下载"
        elif failures or issues or len(records) != len(book.chapters):
            status, suffix = "incomplete", "待核对"
        else:
            status, suffix = "complete", "已发布章节"
        export = None
        result = dict(status=status, title=book.title, author=book.author, downloaded=len(records), failures=failures,
                      issues=list(dict.fromkeys(issues)), chapters=[{k:v for k,v in r.items() if k != 'text'} for r in records])
        if records:
            text = book.title + "\n\n" + "\n\n".join(r["title"] + "\n\n" + r["text"] for r in records) + "\n"
            text = text.replace('\r\n', '\n').replace('\n', '\r\n')
            folder = self.output if status == 'complete' else self.output / ('试下载' if status == 'sample' else '待核对')
            name = safe_name(book_label(book.title, book.author)) + '.txt'
            with export_target(folder, name, self.state['input_url'], self.control) as export:
                write_text(export, text)
                self.checkpoint(**result, output=str(export), output_sha256=digest(text))
                save(export.with_suffix('.report.json'), self.state)
        else:
            self.checkpoint(**result, output=None)
        self.emit("done", f"{book.title}：{len(records)}/{len(book.chapters)} 章 · {suffix}", **{"result": self.state})
        return self.state

    def chapter_results(self, refs, book, config):
        """Validate all saved chapters, then keep only a bounded set in flight."""
        pending_refs = []
        cached_count = 0
        last_cached = None
        for index, ref in enumerate(refs):
            self.control.checkpoint()
            record = load(self.folder / 'chapters' / (digest(ref.url) + '.json'))
            cached = bool(record and record.get('version') == PARSER_VERSION and record.get('url') == ref.url
                          and record.get('adapter') == config.get('parser_revision')
                          and record.get('sha256') == digest(record.get('text', ''))
                          and identity(record.get('title', '')) == identity(ref.title))
            if cached:
                cached_count += 1
                last_cached = (index, ref)
                yield index, ref, record, True, None
            else:
                pending_refs.append((index, ref))
        if cached_count and pending_refs:
            index, ref = last_cached
            self.checkpoint(downloaded=cached_count)
            self.emit('chapter', f'缓存校验完成 {cached_count}/{len(refs)} · 继续缺失章节',
                      current=index+1, downloaded=cached_count, total=len(refs), title=book.title)
        workers = getattr(self.fetcher, 'chapter_workers', 1)
        child_factory = getattr(self.fetcher, 'chapter_fetcher', None)
        if not child_factory:
            workers = 1
        local = threading.local()
        children, child_lock = [], threading.Lock()
        slots = iter(range(workers))
        stopped = threading.Event()

        def read(entry):
            index, ref = entry
            try:
                self.control.checkpoint()
                if stopped.is_set():
                    raise Cancelled('章节队列已停止')
                if child_factory:
                    if not hasattr(local, 'fetcher'):
                        with child_lock:
                            slot = next(slots)
                        local.fetcher = child_factory(slot, stopped)
                        with child_lock:
                            children.append(local.fetcher)
                    fetcher = local.fetcher
                else:
                    fetcher = self.fetcher
                # Workers only write their own raw pages/chapter files. The
                # coordinator alone owns the manifest and ordered TXT export.
                task = Runner(fetcher, self.output, getattr(fetcher, 'control', self.control))
                task.folder = self.folder
                record = task.chapter(ref, book, config)
                save(self.folder / 'chapters' / (digest(ref.url) + '.json'), record)
                return index, ref, record, False, None
            except Exception as exc:
                return index, ref, None, False, exc

        try:
            if workers == 1:
                for entry in pending_refs:
                    yield read(entry)
                return
            self.emit('info', f'章节批量队列：每书最多 {workers} 章；分页顺序读取；最终按目录顺序合并')
            executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='novel-chapter')
            pending, entries = set(), iter(pending_refs)
            exhausted = False
            try:
                while pending or not exhausted:
                    self.control.checkpoint()
                    while len(pending) < workers and not exhausted:
                        entry = next(entries, None)
                        if entry is None:
                            exhausted = True
                        else:
                            pending.add(executor.submit(read, entry))
                    if pending:
                        done, pending = wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
                        for future in done:
                            yield future.result()
            finally:
                stopped.set()
                for future in pending:
                    future.cancel()
                executor.shutdown(wait=True, cancel_futures=True)
        finally:
            for child in children:
                try:
                    child.close()
                except Exception as exc:
                    self.emit('warning', f'章节会话清理失败：{exc}')


def config_without_toc(config):
    return {key: value for key, value in config.items() if not key.startswith("toc_") and key != "parser_revision"}

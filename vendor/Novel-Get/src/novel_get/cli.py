from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import soupsieve

from .engine import discover, download, safe_name, write_json
from .export import export_epub, export_txt
from .fetch import (
    DEFAULT_DELAY,
    DEFAULT_JITTER,
    DEFAULT_PAUSE,
    DEFAULT_PAUSE_EVERY,
    DownloadError,
    Fetcher,
    SiteBlocked,
)
from .parse import absolute, identity, number
from .settings import load_site_configs


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("必须大于 0")
    return result


def nonnegative(value):
    result = float(value)
    if not 0 <= result < float("inf"):
        raise argparse.ArgumentTypeError("必须是非负有限数")
    return result


def parser():
    root = argparse.ArgumentParser(
        prog="novel-get",
        description="通用小说下载器：目录发现、分页合并、阅读链补全和断点续传",
    )
    sub = root.add_subparsers(dest="command", required=True)
    for command, description in (("inspect", "检查目录，不下载正文"), ("download", "下载并导出小说")):
        p = sub.add_parser(command, help=description)
        p.add_argument("urls", nargs="*", help="小说目录页 URL，可传多个，按参数顺序处理")
        p.add_argument("--urls-file", type=Path, help="每行一个 URL，支持 # 注释")
        p.add_argument("--config", type=Path, action="append", default=[], help="自定义站点 TOML 配置，可重复，后者优先")
        p.add_argument("--user-agent", help="自定义 User-Agent")
        p.add_argument("--header", action="append", default=[], metavar="NAME:VALUE", help="额外请求头，可重复")
        p.add_argument("--headers-file", type=Path, help="请求头 JSON 文件（可存 Cookie，勿提交版本库）")
        p.add_argument("--delay", type=nonnegative, default=DEFAULT_DELAY, help="响应结束后的最少等待秒数，默认 15")
        p.add_argument("--jitter", type=nonnegative, default=DEFAULT_JITTER, help="额外随机等待 0～N 秒，默认 15")
        p.add_argument("--pause-every", type=positive, default=DEFAULT_PAUSE_EVERY, help="每 N 次页面请求长休息一次，默认 20")
        p.add_argument("--pause", type=nonnegative, default=DEFAULT_PAUSE, help="长休息 N～2N 秒，默认 300（5～10 分钟）")
        p.add_argument("--timeout", type=positive, default=30, help="每次请求超时秒数，默认 30")
        p.add_argument("--retries", type=int, choices=range(0, 11), default=0, metavar="0..10", help="临时网络错误重试次数，默认 0")
        p.add_argument("--browser", action="store_true", help="用真实 Chromium 渲染 JavaScript")
        p.add_argument("--browser-channel", choices=["chrome", "msedge"], help="使用已安装浏览器（自动启用 --browser）")
        p.add_argument("--interactive", action="store_true", help="打开可见浏览器，保留会话并允许手动登录；自动启用浏览器模式")
        p.add_argument("--browser-profile", type=Path, help="交互浏览器专用会话目录；默认 .novel-get/browser/<浏览器>，自动启用交互模式")
        p.add_argument("--wait", type=nonnegative, default=1, help="浏览器页面加载后等待秒数")
        p.add_argument("--wait-selector", help="浏览器等待指定 CSS 元素出现")
        p.add_argument("--max-toc-pages", type=positive, default=200)
        p.add_argument("--quiet", action="store_true", help="隐藏进度，保留结果和错误")
        if command == "download":
            p.add_argument("-o", "--output", type=Path, default=Path("downloads"))
            p.add_argument("--format", choices=["txt", "epub", "both"], default="both")
            p.add_argument("--merge", metavar="TITLE", help="按 URL 顺序另生成一份合集")
            p.add_argument("--limit", type=positive, help="每本仅试下载 N 章，结果标记为不完整")
            p.add_argument("--max-chapters", type=positive, default=10000)
            p.add_argument("--max-chapter-pages", type=positive, default=100)
            p.add_argument("--min-chars", type=positive, default=80, help="单页最少正文字数，默认 80")
            p.add_argument("--refresh", action="store_true", help="忽略已有章节缓存并重新下载")
    return root


def load_object(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError(f"{path} 必须为字符串键值的 JSON 对象")
    return value


def run(args):
    interactive = args.interactive or args.browser_profile is not None
    if interactive and not sys.stdin.isatty():
        raise ValueError("--interactive 需要可输入的终端，以便确认开始或保存会话退出")
    urls = list(args.urls)
    if args.urls_file:
        urls += [
            line.strip() for line in args.urls_file.read_text(encoding="utf-8-sig").splitlines() if line.strip() and not line.lstrip().startswith("#")
        ]
    urls = list(dict.fromkeys(urls))
    if not urls:
        raise ValueError("请提供至少一个目录 URL 或 --urls-file")
    for url in urls:
        if absolute(url, url) != url:
            raise ValueError(f"无效 URL（需要完整 http/https 地址且无片段）: {url}")
    config = load_site_configs(args.config)
    if args.wait_selector:
        soupsieve.compile(args.wait_selector)
    headers = load_object(args.headers_file) if args.headers_file else {}
    for header in args.header:
        name, separator, value = header.partition(":")
        if not separator or not name.strip():
            raise ValueError("--header 必须为 NAME:VALUE")
        headers[name.strip()] = value.strip()
    # HTTP header names are case insensitive; keep a single spelling for overrides.
    headers = {"-".join(part.capitalize() for part in k.split("-")): v for k, v in headers.items()}
    if args.user_agent:
        headers["User-Agent"] = args.user_agent
    progress = (lambda _: None) if args.quiet else (lambda message: print(message, file=sys.stderr, flush=True))
    results, inspection, merged = [], [], []
    stopped = False
    progress(
        f"串行请求：每页等待 {args.delay:g}～{args.delay + args.jitter:g} 秒，"
        f"每 {args.pause_every} 次请求休息 {args.pause:g}～{args.pause * 2:g} 秒；遇访问限制停止整次任务"
    )
    with Fetcher(
        delay=args.delay,
        jitter=args.jitter,
        pause_every=args.pause_every,
        pause=args.pause,
        timeout=args.timeout,
        retries=args.retries,
        headers=headers,
        browser=args.browser or bool(args.browser_channel),
        browser_channel=args.browser_channel,
        wait=args.wait,
        wait_selector=args.wait_selector,
        progress=progress,
        interactive=interactive,
        browser_profile=args.browser_profile,
    ) as fetcher:
        for index, url in enumerate(urls):
            progress(f"读取 {url}")
            try:
                book = discover(fetcher, url, config, args.max_toc_pages, progress)
                if args.command == "inspect":
                    data = asdict(book)
                    data["site_config"] = config.resolve(book.url)
                    by_url = {}
                    conflicts = []
                    for ref in book.chapters:
                        if ref.url in by_url and identity(by_url[ref.url]) != identity(ref.title):
                            conflicts.append({"url": ref.url, "titles": [by_url[ref.url], ref.title]})
                        by_url[ref.url] = ref.title
                    data["conflicting_links"] = conflicts
                    latest = number(book.latest_title)
                    discovered = [number(ref.title) for ref in book.chapters]
                    highest = max((n for n in discovered if n is not None), default=0)
                    data["reading_chain_needed"] = bool(conflicts or (latest and latest > highest))
                    data["note"] = "仅检查可发现目录；download 会沿正文下一章继续发现和核对。"
                    inspection.append(data)
                    continue
                folder, chapters, state = download(
                    fetcher,
                    book,
                    args.output,
                    config=config,
                    max_chapters=args.max_chapters,
                    max_pages=args.max_chapter_pages,
                    limit=args.limit,
                    min_chars=args.min_chars,
                    refresh=args.refresh,
                    progress=progress,
                )
                note = f"来源：{url}\n校验状态：{state['status']}\n范围：当前资源站可获取内容；不代表原作已完结。"
                if state["status"] != "complete":
                    note += "\n此文件不完整，详见 manifest.json。"
                export_files(folder / "book", book.title, chapters, args.format, book.author, note)
                results.append(
                    {
                        "url": url,
                        "title": book.title,
                        "status": state["status"],
                        "downloaded": len(chapters),
                        "directory": str(folder.resolve()),
                        "issues": state["issues"],
                        "failures": state["failures"],
                    }
                )
                merged.append({"title": book.title, "text": note})
                merged.extend({**chapter, "title": f"{book.title} · {chapter['title']}"} for chapter in chapters)
                print(f"{book.title}: {state['status']}，{len(chapters)} 章 → {folder.resolve()}")
                if state.get("blocked"):
                    stopped = True
                    break
            except DownloadError as exc:
                failure = {"url": url, "status": "failed", "error": str(exc)}
                results.append(failure)
                inspection.append(failure)
                merged.append({"title": url, "text": "本部分下载失败：" + str(exc)})
                print(str(exc), file=sys.stderr)
                if isinstance(exc, SiteBlocked):
                    stopped = True
                    break
    if stopped:
        for url in urls[index + 1 :]:
            skipped = {"url": url, "status": "skipped", "error": "检测到访问限制，本次任务已停止"}
            results.append(skipped)
            inspection.append(skipped)
            merged.append({"title": url, "text": skipped["error"]})
    if args.command == "inspect":
        print(json.dumps(inspection, ensure_ascii=False, indent=2))
        return 2 if results else 0
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "run-report.json", results)
    incomplete = any(r["status"] != "complete" for r in results)
    if args.merge:
        note = "合集不完整，请查看 run-report.json。" if incomplete else "已获取各资源站当前可获取内容。"
        export_files(args.output / safe_name(args.merge), args.merge, merged, args.format, note=note)
    return 2 if incomplete else 0


def export_files(stem, title, chapters, format, author="", note=""):
    # Append suffix: a title may itself contain a dot.
    if format in {"txt", "both"}:
        export_txt(Path(str(stem) + ".txt"), title, chapters, note)
    if format in {"epub", "both"}:
        export_epub(Path(str(stem) + ".epub"), title, chapters, author, note)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (DownloadError, ValueError, OSError, soupsieve.SelectorSyntaxError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断，已完成章节保存在缓存中；重跑相同命令即可续传。", file=sys.stderr)
        return 130

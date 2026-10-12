import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from .network import Control, Fetcher, Cancelled
from .core import Runner


def main(argv=None):
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="本地小说 TXT 下载器；浏览器模式请使用桌面界面")
    parser.add_argument("url")
    parser.add_argument("--output", type=Path, default=Path("下载"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=3)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--browser-headless", action="store_true", help="使用已登录的独立浏览器会话在后台下载")
    parser.add_argument("--browser-minimized", action="store_true", help="保留正常浏览器模式并最小化；验证时保留窗口等待用户")
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit 不能为负数")
    if args.browser_headless and args.browser_minimized:
        parser.error("两种浏览器模式不能同时选择")
    browser = args.browser_headless or args.browser_minimized
    if browser and not args.profile:
        parser.error("浏览器模式需要 --profile 指定本软件的独立会话目录")
    control = Control(lambda **data: print(json.dumps(data, ensure_ascii=False), flush=True))
    if args.stop_file:
        def watch_stop():
            while not control.cancel.wait(.5):
                if args.stop_file.exists():
                    control.cancel.set()
        threading.Thread(target=watch_stop, daemon=True).start()
    signal.signal(signal.SIGINT, lambda *_: control.cancel.set())
    try:
        with Fetcher(control, delay=args.delay, manual=args.browser_minimized,
                     mode="browser" if browser else "http", profile=args.profile,
                     headless=args.browser_headless, minimized=args.browser_minimized) as fetcher:
            result = Runner(fetcher, args.output, control, limit=args.limit, refresh=args.refresh).run(args.url, inspect=args.inspect)
        return 0 if result["status"] in {"complete", "inspected", "sample"} else 2
    except Cancelled:
        return 130
    except Exception as exc:
        print(json.dumps({"kind": "error", "message": str(exc)}, ensure_ascii=False), flush=True)
        return 1

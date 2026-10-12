import sys
from pathlib import Path

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(ROOT / "vendor" / "Novel-Get" / "src"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-control":
        from novel_app.test_control import main
        main(ROOT, sys.argv[2] if len(sys.argv)>2 else 'status')
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from novel_app.cli import main
        raise SystemExit(main(sys.argv[2:]))
    from novel_app.gui import main
    initial_urls = []
    if "--url" in sys.argv:
        initial_urls = [sys.argv[sys.argv.index("--url") + 1]]
    home = Path(sys.argv[sys.argv.index("--home") + 1]).resolve() if "--home" in sys.argv else ROOT
    output = Path(sys.argv[sys.argv.index("--output") + 1]).resolve() if "--output" in sys.argv else None
    main(home, smoke="--smoke" in sys.argv, initial_urls=initial_urls, autorun="--run" in sys.argv,
         output=output, force_browser="--browser" in sys.argv)

"""Bounded integration test for the application's own browser transport."""
import sys
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'vendor/Novel-Get/src'))
from novel_app.network import Control, Fetcher
from novel_app.sites import get_book, site_config
from novel_get.parse import toc_refs, toc_links

sys.stdout.reconfigure(encoding='utf-8')
control=Control(lambda **data: print(json.dumps(data,ensure_ascii=False),flush=True))
try:
    with Fetcher(control,mode='browser',profile=ROOT/'data/browser-probe',manual=False) as fetcher:
        page=fetcher.get('https://m.41nr.com/book/28425/')
        (ROOT/'evidence/41nr-directory.html').write_text(page.html,encoding='utf-8')
        config=site_config(page.url)
        refs=toc_refs(page,config.get('toc_selector'))
        print(json.dumps({'kind':'result','url':page.url,'title':get_book(page).title,'chapters_on_page':len(refs),
                          'first_links':[r.url for r in refs[:2]],'toc_links':toc_links(page,{r.url for r in refs})},ensure_ascii=False),flush=True)
except Exception as exc:
    print(json.dumps({'kind':'error','type':type(exc).__name__,'message':str(exc)},ensure_ascii=False),flush=True)
    raise SystemExit(1)

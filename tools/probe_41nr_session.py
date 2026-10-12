"""One bounded check of the app-owned saved browser session; no credential logging."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'vendor/Novel-Get/src')]
from novel_app.network import Control, Fetcher
from novel_app.sites import site_config
from novel_app.core import config_without_toc
from novel_get.fetch import Page, check_access
from novel_get.parse import extract_chapter

sys.stdout.reconfigure(encoding='utf-8')
def emit(**data):
    print(json.dumps(data, ensure_ascii=False), flush=True)

url = 'https://m.41nr.com/chapter/28425/5821509_2.html'
with Fetcher(Control(emit), mode='browser', profile=ROOT/'data/browser-profile', manual=False, minimized=True) as f:
    started = time.monotonic()
    response = f.tab.goto(url, wait_until='domcontentloaded', timeout=25000)
    emit(kind='response', status=response.status, challenge=response.headers.get('cf-mitigated'),
         browser_mode='headed-minimized', requested=url)
    for attempt in range(5):
        f.tab.wait_for_timeout(3000)
        page = Page(f.tab.url, f.tab.content())
        try:
            check_access(page)
            parsed = extract_chapter(page, min_chars=40, **config_without_toc(site_config(url)))
            emit(kind='readable', elapsed=round(time.monotonic()-started,1), status=f.last_status,
                 title=parsed.title, chars=len(parsed.text), next_page=parsed.next_page)
            break
        except Exception as exc:
            emit(kind='waiting', elapsed=round(time.monotonic()-started,1), status=f.last_status,
                 page_title=f.tab.title(), reason=str(exc), challenge_frame=bool(f.tab.locator('iframe[src*="challenges.cloudflare.com"]').count()))
    else:
        emit(kind='blocked', message='Bounded check ended without readable chapter; no challenge interaction performed.')

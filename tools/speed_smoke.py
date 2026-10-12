"""Bounded live speed check: three new chapters per test site, then exit."""
import json
import sys
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.core import Runner
from novel_app.network import Control,Fetcher
from novel_app.sites import site_config
from novel_app.storage import load,save,digest
from novel_app.test_control import jobs
from novel_get.parse import Book,Ref

sys.stdout.reconfigure(encoding='utf-8')
def emit(**event):
    print(json.dumps(event,ensure_ascii=False),flush=True)

results=[]
for job in jobs(ROOT):
    control=Control(emit)
    deadline=threading.Timer(180,control.cancel.set)
    deadline.daemon=True
    deadline.start()
    result={'site':job['name'],'status':'running','chapters':0,'pages':0}
    start=time.monotonic()
    try:
        toc=load(job['folder']/'toc.json')
        book=Book(**{k:v for k,v in toc['book'].items() if k!='chapters'})
        book.chapters=[Ref(**r) for r in toc['chapters']]
        book.toc_pages=toc['seen']
        refs=[r for r in book.chapters if not (job['folder']/'chapters'/(digest(r.url)+'.json')).exists()][:3]
        headless=job['name']!='41nr'
        with Fetcher(control,mode='browser',profile=job['profile'],delay=3,manual=False,
                     headless=headless,minimized=not headless) as fetcher:
            runner=Runner(fetcher,ROOT/'evidence/speed-smoke',control)
            runner.folder=ROOT/'evidence/speed-smoke'/digest(job['url'])[:16]
            runner.folder.mkdir(parents=True,exist_ok=True)
            for ref in refs:
                record=runner.chapter(ref,book,site_config(job['url']))
                save(runner.folder/'chapters'/(digest(ref.url)+'.json'),record)
                result['chapters']+=1
                result['pages']+=len(record['pages'])
                emit(kind='speed_progress',**result)
        result['status']='passed' if result['chapters']==3 else 'insufficient_sample'
    except Exception as exc:
        result.update(status='blocked_or_failed',error=str(exc))
    finally:
        deadline.cancel()
    result['elapsed_seconds']=round(time.monotonic()-start,1)
    results.append(result)
    save(ROOT/'evidence/speed-smoke-results.json',results)
    emit(kind='speed_result',**result)

"""Bounded real-site compatibility check using user-verified WebView2 profiles."""
import hashlib
import json
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.browser_panel import BrowserPanel
from novel_app.parallel import SiteGate, TaskControl
from novel_app.network import Control
from novel_app.sites import site_config, normalize_page
from novel_app.core import config_without_toc
from novel_get.parse import toc_refs, extract_chapter
from novel_app.storage import save

home=ROOT/'evidence'/('webview2-sites-'+time.strftime('%Y%m%d-%H%M%S'))
home.mkdir(parents=True)
progress=home/'progress.json'; events=[]; finished=threading.Event()
root=tk.Tk(); root.title('逐页 · 两站内置浏览器首章测试'); root.geometry('1080x760')
panel=BrowserPanel(root,ROOT,tk.StringVar(value='内置浏览器')); panel.show()
parent=Control(lambda **event:events.append(event))
broker=panel.broker(max_sites=2)
result={'stage':'running','whole_book_test':False,'sites':[]}


def run():
    try:
        for site,url in [('41nr.com','https://m.41nr.com/book/28425/'),('banshanren.com','https://www.banshanren.com/novel/cunsemanyuan')]:
            task=TaskControl(parent,site,url); gate=SiteGate(site,parent,delay=2,limit=1,adaptive=False,pause_every=1000)
            def fetch(address, toc=False):
                gate.acquire(task); started=time.monotonic()
                try:
                    return broker.get(site,None,task,gate,address) if toc else broker.request(site,None,task,gate,address,transport_key=site)
                finally: gate.release(time.monotonic()-started,control=task)
            result.update(current_site=site,updated=time.time()); save(progress,result)
            config=site_config(url); page=fetch(url,True)
            refs=toc_refs(page,config.get('toc_selector')); assert refs,'No chapter links'
            parsed=[]; address=refs[0].url
            for _ in range(3):
                document=fetch(address)
                chapter=extract_chapter(normalize_page(document),min_chars=40,**config_without_toc(config))
                parsed.append(chapter.text)
                if not chapter.next_page: break
                address=chapter.next_page
            else: raise RuntimeError('First chapter exceeds three pages; stopped at safety bound')
            body='\n\n'.join(parsed)
            assert len(body)>100
            result['sites'].append({'site':site,'toc_links_on_open_page':len(refs),'chapter_pages':len(parsed),'characters':len(body),
                                    'lines':body.count('\n')+1,'text_sha256':hashlib.sha256(body.encode()).hexdigest(),
                                    'same_session_background_requests':sum(e.get('item')==site and e.get('transport')=='session-http' for e in events),
                                    'manual_requests':sum(e.get('item')==site and e.get('kind')=='manual' for e in events),'status':'passed'})
            save(progress,result)
        result['stage']='passed'
    except Exception as exc:
        result.update(stage='failed',error=repr(exc))
    finally:
        broker.close(); result['updated']=time.time(); save(progress,result); finished.set()


threading.Thread(target=run,daemon=True).start()
def poll():
    panel.status.set(f'正在检查 {result.get("current_site", "初始化")} 的首章与分页；不启动整书下载。')
    if finished.is_set():
        panel.close()
        def done():
            if panel.closed.is_set(): root.destroy()
            else: root.after(100,done)
        done(); return
    result['last_event']={k:v for k,v in (events[-1] if events else {}).items() if k in ('kind','message','site')}
    result['updated']=time.time(); save(progress,result); root.after(500,poll)
root.after(500,poll); root.after(90000,parent.cancel.set)
root.protocol('WM_DELETE_WINDOW',parent.cancel.set)
root.mainloop(); print(json.dumps(result,ensure_ascii=False),flush=True)
sys.exit(0 if result['stage']=='passed' else 1)

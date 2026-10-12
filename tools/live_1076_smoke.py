"""Bounded live check of one source chapter; original queue/cache stay untouched."""
import json
import hashlib
import argparse
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_get.parse import Book,Ref
from novel_app.core import Runner
from novel_app.parallel import ParallelBatch,TaskControl,JobFetcher
from novel_app.browser_panel import BrowserPanel
from novel_app.network import Control
from novel_app.sites import site_config,book_label
from novel_app.storage import load,save,digest,write_text

parser=argparse.ArgumentParser()
parser.add_argument('--chapter-url',default='https://m.41nr.com/chapter/28425/5828743.html')
parser.add_argument('--home',type=Path,default=ROOT/'evidence/live-chapter-1076-fix7')
args=parser.parse_args()
home=args.home
url='https://m.41nr.com/book/28425/'
target=args.chapter_url
toc=load(ROOT/'下载/.novel-cache'/digest(url)[:16]/'toc.json')
book=Book(**{k:v for k,v in toc['book'].items() if k!='chapters'})
book.chapters=[Ref(**r) for r in toc['chapters']]
ref=next(r for r in book.chapters if r.url==target)
root=tk.Tk();root.title('逐页 · 单章测试');root.geometry('1000x720')
panel=BrowserPanel(root,ROOT,tk.StringVar(value='内置浏览器'));panel.show()
state=dict(stage='starting',chapter_title=ref.title,requested_pages=[],source_cache_written=False)
finished=threading.Event()
def emit(**event):
    if event.get('kind')=='request':state['requested_pages'].append(event['url'])
    state.update(latest=event.get('message',''),updated=time.strftime('%Y-%m-%d %H:%M:%S'))
    save(home/'progress.json',state)
control=Control(emit)
def run():
    batch=child=None
    try:
        opts=dict(delay=5,pause_every=1000,pause_seconds=0,mode='浏览器模式',proxy=False,refresh=False,
                  output=home/'downloads',books=1,site_concurrency=1,chapter_concurrency=1,adaptive=False)
        task=TaskControl(control,'single',url)
        batch=ParallelBatch(control,opts,lambda _:panel.profile('41nr.com'),lambda _:'41nr.com',browser_factory=panel.broker)
        batch.browser.headed=False  # A challenge stops this diagnostic; never waits invisibly.
        fetcher=JobFetcher(batch,task,'41nr.com','browser',panel.profile('41nr.com'))
        child=fetcher.chapter_fetcher(0)
        runner=Runner(child,opts['output'],control)
        runner.folder=home/'downloads/.novel-cache'/digest(url)[:16]
        result=runner.chapter(ref,book,site_config(url))
        output=home/'样本'/f'{book_label("都市奇缘","易天下")}.txt'
        write_text(output,'都市奇缘（易天下）\n\n'+result['title']+'\n\n'+result['text']+'\n')
        state.update(stage='passed',title=result['title'],pages=result['pages'],body_chars=len(result['text']),
                     chapter_sha256=result['sha256'],output=str(output),output_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    except Exception as ex:state.update(stage='failed',error=repr(ex))
    finally:
        try:
            if child:child.close()
            if batch and batch._browser:batch.browser.close()
        finally:save(home/'progress.json',state);finished.set()
threading.Thread(target=run,daemon=True).start()
def poll():
    if finished.is_set():
        panel.close()
        def done():
            if panel.closed.is_set():
                state['browser_exited']=True;save(home/'progress.json',state);root.destroy()
            else:root.after(100,done)
        done()
    else:root.after(100,poll)
root.after(100,poll);root.after(80000,control.cancel.set);root.mainloop()
print(json.dumps(state,ensure_ascii=True),flush=True)
sys.exit(0 if state['stage']=='passed' else 1)

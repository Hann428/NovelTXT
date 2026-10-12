"""One bounded, non-secret diagnostic using the downloader's normal profile."""
import json
import argparse
import queue
import sys
import time
import tkinter as tk
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.webview2 import NativeSession
from novel_app.storage import save
from bs4 import BeautifulSoup

parser=argparse.ArgumentParser();parser.add_argument('--cache-check',action='store_true')
parser.add_argument('--chapter-url',default='https://m.41nr.com/chapter/28425/5828743.html')
parser.add_argument('--neighbor-url',default='https://m.41nr.com/chapter/28425/5828747.html')
parser.add_argument('--home',type=Path)
args=parser.parse_args()

home=args.home or ROOT/('evidence/webview-access-cache-20261010' if args.cache_check else 'evidence/webview-access-20261010')
profile=ROOT/'evidence/webview2-prototype/profiles/m.41nr.com'
if not profile.is_dir():profile=ROOT/'data/webview2-profiles/41nr.com'
urls=['https://m.41nr.com/book/28425/',
      args.chapter_url,
      args.neighbor_url]
root=tk.Tk();root.title('逐页 · 单次访问诊断');root.geometry('1000x720')
frame=tk.Frame(root);frame.pack(fill='both',expand=True);root.update_idletasks()
events=queue.Queue();pool=ThreadPoolExecutor(max_workers=1)
session=NativeSession(frame.winfo_id(),profile,emit=events.put)
state=dict(status='running',total=3,processed=0,profile=str(profile),results=[])
def record(**values):
    state.update(values,updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
    save(home/'progress.json',state)
    print(json.dumps({k:v for k,v in state.items() if k!='results'},ensure_ascii=True),flush=True)
def call(op,**args):
    future=pool.submit(session.request,op,**args)
    deadline=time.monotonic()+32
    while not future.done():
        if time.monotonic()>deadline:raise TimeoutError('diagnostic request timeout')
        root.update();time.sleep(.02)
    root.update();return future.result()
def drain():
    result=[]
    while not events.empty():result.append(events.get_nowait())
    return result
record(pid=session.process.pid)
try:
    call('new_page',page='probe');call('show',page='probe')
    if args.cache_check:
        call('navigate',page='probe',url=urls[0])
        for index,mode in enumerate(('default','reload','default')):
            if index==1:
                until=time.monotonic()+5
                while time.monotonic()<until:root.update();time.sleep(.02)
            record(current_url=urls[1],cache_mode=mode)
            expression="""(async () => {const r=await fetch(URL,{credentials:'include',redirect:'error',cache:MODE,signal:AbortSignal.timeout(15000)});
                return {url:r.url,code:r.status,html:await r.text(),headers:Object.fromEntries(['cache-control','date','age','etag','content-type','cf-cache-status'].map(k=>[k,r.headers.get(k)]))};})()""".replace('URL',json.dumps(urls[1])).replace('MODE',json.dumps(mode))
            response=call('evaluate',page='probe',expression=expression)
            soup=BeautifulSoup(response.pop('html'),'html.parser');body=soup.select_one('#nr1');heading=soup.select_one('#nr_title')
            response.update(cache_mode=mode,title=soup.title.get_text(strip=True) if soup.title else '',heading=heading.get_text(strip=True) if heading else '',body_chars=len(body.get_text()) if body else 0)
            state['results'].append(response);record(processed=index+1)
        record(status='complete')
    else:
        for index,url in enumerate(urls):
            if index:
                until=time.monotonic()+5
                while time.monotonic()<until:root.update();time.sleep(.02)
            drain();record(current_url=url)
            started=time.monotonic();call('navigate',page='probe',url=url)
            metadata=call('evaluate',page='probe',expression="""({url:location.href,title:document.title,
                heading:document.querySelector('#nr_title')?.textContent?.trim() || '',
                body_chars:document.querySelector('#nr1')?.textContent?.length || 0,
                user_agent:navigator.userAgent, referrer:document.referrer,
                navigation:performance.getEntriesByType('navigation').map(e=>({transfer_size:e.transferSize,encoded_size:e.encodedBodySize,decoded_size:e.decodedBodySize})),
                challenge:!!document.querySelector('#challenge-running, #challenge-stage, .cf-turnstile, #captcha-form')})""")
            replies=[e for e in drain() if e.get('kind') in ('navigation','response') and e.get('page')=='probe']
            metadata.update(requested_url=url,elapsed_seconds=round(time.monotonic()-started,2),responses=replies)
            state['results'].append(metadata);record(processed=index+1)
            if metadata.get('challenge'):
                record(status='needs_manual_verification');break
        else:record(status='complete')
except Exception as ex:
    record(status='failed',error=repr(ex));raise
finally:
    pool.shutdown(wait=True,cancel_futures=True)
    session.close();root.destroy();record(helper_closed=session.closed)
print(json.dumps(state,ensure_ascii=True),flush=True)

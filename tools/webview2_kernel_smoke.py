"""Download a complete fixture through the embedded UI and production queue."""
import json
import argparse
import sys
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--missing-page', action='store_true')
parser.add_argument('--forbidden-page', action='store_true')
parser.add_argument('--cached-denial', action='store_true')
parser.add_argument('--temporary-cache-denial', action='store_true')
parser.add_argument('--semantic-pages', action='store_true')
parser.add_argument('--request-cap', type=int, choices=(1,2,3,4), default=2)
parser.add_argument('--bundle', type=Path)
args=parser.parse_args()
if args.forbidden_page and args.missing_page: parser.error('choose only one failed-page scenario')
if args.cached_denial and not args.forbidden_page: parser.error('cached denial requires forbidden-page')
if args.temporary_cache_denial and (args.forbidden_page or args.missing_page or args.semantic_pages): parser.error('temporary cache scenario must run separately')
if args.semantic_pages and (args.forbidden_page or args.missing_page): parser.error('semantic scenario must run separately')
if args.bundle: sys._MEIPASS=str(args.bundle.resolve())
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app import core, network, browser_parallel, parallel
from novel_app.browser_panel import BrowserPanel
from novel_app.storage import save, load

home=ROOT/'evidence'/('webview2-kernel-'+time.strftime('%Y%m%d-%H%M%S'))
home.mkdir(parents=True)
progress=home/'progress.json'
events=[]; arrivals=[]; active=peak=cookie_failures=0; lock=threading.Lock()
missing=args.missing_page or args.forbidden_page or args.semantic_pages or args.temporary_cache_denial
reload_failed=True
prose='　　清晨的院子里桂花落在石桌上小林推开门沿着小路走到桥边看见远处有人正在收拾钓具。'
pages={'/book/a/':'<h1>流水线测试书</h1><div id="list">'+''.join(f'<a href="{i}.html">第{i}章 测试</a>' for i in range(1,7))+'</div>'}
for i in range(1,7):
    suffix=' (1/2)' if i==1 else ''; nextpage='<a href="1_2.html">下一页</a>' if i==1 else ''
    pages[f'/book/a/{i}.html']=f'<h1>第{i}章 测试{suffix}</h1><div id="content"><p>{prose}章节{i}。</p><p></p><p>第二段落，继续保留文字。</p></div>{nextpage}'
pages['/book/a/1_2.html']=f'<h1>第1章 测试 (2/2)</h1><div id="content"><p>{prose}第二页内容。</p></div>'
if args.cached_denial:
    pages['/book/a/reload.html']=pages['/book/a/2.html']
if args.semantic_pages:
    pages['/book/a/']=pages['/book/a/'].replace('第2章 测试','第2章 安全验证')
    pages['/book/a/2.html']=pages['/book/a/2.html'].replace('第2章 测试','第2章 安全验证')
    pages['/book/a/3.html']=pages['/book/a/3.html'].replace('章节3。','章节3。他看到提示：“访问过于频繁，请稍后再试。”')
    pages['/book/a/1_2.html']='<h1>第1章 测试 (2/2)</h1><div id="content"><p>　　他转身离开。</p></div>'

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_): pass
    def do_GET(self):
        global active,peak,cookie_failures,missing
        path=urlsplit(self.path).path
        if path not in pages:
            self.send_response(404); self.end_headers(); return
        with lock:
            active+=1; peak=max(peak,active); arrivals.append(path)
        try:
            time.sleep(.35)
            if missing and args.semantic_pages and path=='/book/a/4.html':
                html=('<h1>第4章 测试</h1><div id="content">系统维护中，当前页面内容暂时无法提供，请您稍后再次访问。给您的阅读带来不便，我们深表歉意，感谢理解与支持。</div>').encode()
                self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(html))); self.end_headers(); self.wfile.write(html); return
            if (missing and not args.semantic_pages and path=='/book/a/1_2.html') or (args.cached_denial and reload_failed and path=='/book/a/reload.html'):
                code=403 if args.forbidden_page or args.temporary_cache_denial else 404
                label='403 Forbidden' if code==403 else '404 Not Found'
                html=f'<title>{label}</title><h1>{label}</h1><hr>nginx'.encode()
                self.send_response(code); self.send_header('Content-Type', 'text/html')
                if args.cached_denial or args.temporary_cache_denial: self.send_header('Cache-Control','max-age=2678400')
                if args.temporary_cache_denial:
                    self.send_header('CF-Cache-Status','HIT')
                    missing=False
                self.send_header('Content-Length', str(len(html))); self.end_headers(); self.wfile.write(html); return
            if path!='/book/a/' and 'fixture_login=1' not in self.headers.get('Cookie',''):
                cookie_failures+=1; self.send_response(401); self.end_headers(); return
            html=pages[path].encode(); self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Set-Cookie','fixture_login=1; Max-Age=86400; Path=/; HttpOnly; SameSite=Lax')
            self.send_header('Content-Length',str(len(html))); self.end_headers(); self.wfile.write(html)
        finally:
            with lock: active-=1

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'; url=base+'/book/a/'
original=network.validate_url
def validate(address,resolve=False):
    return address if urlsplit(address).netloc==urlsplit(base).netloc else original(address,resolve=resolve)
network.validate_url=core.validate_url=validate
config=lambda url:dict(content_selector='#content',title_selector='h1')
core.site_config=browser_parallel.site_config=config
if args.temporary_cache_denial:
    browser_parallel.CACHED_DENIAL_HOSTS={'127.0.0.1'}
    browser_parallel.cached_denial_delay=lambda attempt:0  # Fixture only; production remains 60/120 seconds.
root=tk.Tk(); root.title('逐页 · 内置浏览器下载内核测试'); root.geometry('960x680')
panel=BrowserPanel(root,home,tk.StringVar(value='内置浏览器'),fixture_port=server.server_port)
panel.show()
control=network.Control(lambda **event:events.append(event))
result={'fixture_only':True,'stage':'starting'}
finished=threading.Event()
def run():
    global missing, reload_failed
    try:
        opts=dict(delay=.1,pause_every=1000,pause_seconds=0,mode='浏览器模式',proxy=False,refresh=False,
                  output=home/'downloads',books=1,site_concurrency=args.request_cap,chapter_concurrency=3,adaptive=False)
        batch=parallel.ParallelBatch(control,opts,lambda _:home/'legacy-unused',lambda _:'fixture',browser_factory=panel.broker)
        started=time.monotonic(); batch.run([('book',url)]); elapsed=time.monotonic()-started
        done=[event for event in events if event['kind']=='done']
        if args.temporary_cache_denial:
            assert arrivals.count('/book/a/1_2.html')==2,arrivals
            assert any(e.get('status')=='访问冷却' for e in events)
            assert not any(e['kind']=='manual' for e in events)
            assert batch.gates['fixture'].restricted and not batch.gates['fixture'].blocked
            result.update(temporary_cached_denial_recovered=True,cache_denial_requests=2,no_false_verification=True)
        if args.semantic_pages:
            partial=done[0]['result']
            assert partial['status']=='incomplete' and partial['downloaded']==5,partial
            assert any('维护提示' in failure['reason'] for failure in partial['failures'])
            assert not any(e['kind']=='manual' for e in events) and not batch.gates['fixture'].blocked
            partial_txt=Path(partial['output']).read_text('utf-8')
            assert '系统维护' not in partial_txt and '第2章 安全验证' in partial_txt and '他转身离开。' in partial_txt
            cached=[load(p)['url'] for p in (home/'downloads/.novel-cache').glob('*/pages/*.json')]
            assert base+'/book/a/4.html' not in cached
            missing=False;count=len(arrivals)
            repair=parallel.ParallelBatch(control,opts,lambda _:home/'legacy-unused',lambda _:'fixture',browser_factory=panel.broker)
            repair.run([('book',url)])
            assert arrivals[count:]==['/book/a/','/book/a/4.html'],arrivals[count:]
            result.update(maintenance_not_cached_or_exported=True, title_and_quoted_warning_preserved=True,
                          contextual_short_tail_preserved=True, repair_requests=arrivals[count:])
            done=[e for e in events if e['kind']=='done'][-1:]
        if args.forbidden_page:
            state=load(next((home/'downloads/.novel-cache').glob('*/manifest.json')))
            assert state['status']=='blocked' and state['error_status']==403, state
            assert any(e.get('status')=='访问被拒绝' for e in events) and not any(e['kind']=='manual' for e in events)
            assert batch.gates['fixture'].blocked and not batch.gates['fixture'].verifying
            assert arrivals.count('/book/a/1_2.html')==1, arrivals
            result.update(plain_403_paused_site=True, no_false_verification=True, saved_chapters=state['downloaded'])
            probe=panel.broker(max_sites=1, headed=True)
            task=parallel.TaskControl(control,'rendered-403',url)
            gate=parallel.SiteGate('fixture',control,delay=0)
            try:
                try:
                    probe.get('fixture',home/'legacy-unused',task,gate,base+'/book/a/1_2.html')
                    raise AssertionError('Rendered 403 was treated as readable')
                except network.AccessDenied as error:
                    assert error.status==403 and not gate.verifying
                    result['rendered_403_classified']=True
            finally:probe.close()
            assert not any(e['kind']=='manual' for e in events)
            missing=False;count=len(arrivals)
            if args.cached_denial:
                native=panel.sessions['fixture']
                target=json.dumps(base+'/book/a/1_2.html')
                stale=native.evaluate(f'(async()=>{{const r=await fetch({target},{{credentials:"include"}});return r.status;}})()',page='main')
                assert stale==403 and len(arrivals)==count,(stale,arrivals[count:])
                result['cached_403_reproduced_after_server_recovery']=True
            repair=parallel.ParallelBatch(control,opts,lambda _:home/'legacy-unused',lambda _:'fixture',browser_factory=panel.broker)
            repair.run([('book',url)])
            assert '/book/a/1_2.html' in arrivals[count:] and '/book/a/1.html' not in arrivals[count:]
            result['repair_requests']=arrivals[count:]
            done=[e for e in events if e['kind']=='done'][-1:]
        if args.missing_page:
            partial=done[0]['result']
            assert partial['status']=='incomplete' and partial['downloaded']==5, partial
            assert partial['failures'][0]['http_status']==404 and partial['failures'][0]['page_url'].endswith('1_2.html')
            assert not any(e['kind']=='manual' for e in events)
            assert not batch.gates['fixture'].blocked and batch.gates['fixture'].limit==args.request_cap
            assert arrivals.count('/book/a/1_2.html')==1 and arrivals.count('/book/a/1.html')==1
            result.update(missing_page_did_not_block_site=True, partial_chapters=5, missing_page_http=404)
            probe=panel.broker(max_sites=1, headed=True)
            task=parallel.TaskControl(control, 'rendered-404', url)
            gate=parallel.SiteGate('fixture', control, delay=0)
            try:
                try:
                    probe.get('fixture', home/'legacy-unused', task, gate, base+'/book/a/1_2.html')
                    raise AssertionError('Rendered 404 was treated as readable')
                except network.PageHTTPError as error:
                    assert error.status==404 and not gate.verifying
                    result['rendered_404_classified']=True
            finally: probe.close()
            assert not any(e['kind']=='manual' for e in events)
            missing=False; count=len(arrivals)
            repair=parallel.ParallelBatch(control,opts,lambda _:home/'legacy-unused',lambda _:'fixture',browser_factory=panel.broker)
            repair.run([('book',url)])
            assert arrivals[count:]==['/book/a/', '/book/a/1_2.html'], arrivals[count:]
            result['repair_requests']=arrivals[count:]
            done=[event for event in events if event['kind']=='done'][-1:]
        assert len(done)==1 and done[0]['result']['status']=='complete' and done[0]['result']['downloaded']==6, events[-8:]
        raw=Path(done[0]['result']['output']).read_bytes()
        if args.semantic_pages:
            expected=[]
            for i in range(1,7):
                title=f'第{i}章 '+('安全验证' if i==2 else '测试')
                paragraph=prose+f'章节{i}。'
                if i==3: paragraph+='他看到提示：“访问过于频繁，请稍后再试。”'
                text=paragraph+'\n\n第二段落，继续保留文字。'
                if i==1:text+='\n　　他转身离开。'
                expected.append(title+'\n\n'+text)
            expected=('流水线测试书\n\n'+'\n\n'.join(expected)+'\n').replace('\n','\r\n').encode()
            assert raw==expected,'Semantic fixture changed text or formatting'
        else:
            baseline=load(ROOT/'evidence/chapter-pipeline-v10-result.json')
            assert raw==Path(baseline['output']).read_bytes(), 'TXT changed from v10'
            assert raw.count('第二页内容'.encode())==1
        assert b'\r\n\r\n' in raw and '　　'.encode() in raw
        assert peak==min(3,args.request_cap) and cookie_failures==0, (peak,cookie_failures)
        assert not batch.browser.thread.is_alive()
        native=panel.sessions['fixture']; pid=native.process.pid
        assert not native.closed and native.process.poll() is None, 'Batch closed native browser'
        count=len(arrivals)
        resumed=parallel.ParallelBatch(control,opts,lambda _:home/'legacy-unused',lambda _:'fixture',browser_factory=panel.broker)
        resumed.run([('book',url)])
        assert len(arrivals)==count and resumed._browser is None
        if args.cached_denial:
            target=base+'/book/a/reload.html'
            native.request('navigate',page='cache-refresh',url=target)
            assert native.evaluate('document.title',page='cache-refresh')=='403 Forbidden'
            reload_failed=False
            native.request('navigate',page='cache-refresh',url=target)
            assert native.evaluate('document.title',page='cache-refresh')=='403 Forbidden'
            before=arrivals.count('/book/a/reload.html')
            native.request('show',page='cache-refresh')
            native.request('reload',page='cache-refresh')
            assert native.evaluate('document.querySelector("#content")?.textContent.length || 0',page='cache-refresh')>40
            assert arrivals.count('/book/a/reload.html')==before+1
            import base64
            (home/'refreshed-browser.png').write_bytes(base64.b64decode(native.request('snapshot',page='cache-refresh')))
            result.update(hard_refresh_replaced_cached_denial=True, refresh_requests=1,
                          refresh_screenshot=str(home/'refreshed-browser.png'))
        # Stop a new chapter pool, preserve valid cache, and resume it on the same
        # live native browser. It is enough to verify lifecycle independently of
        # downloading another site's real book.
        result.update(stage='passed',elapsed=round(elapsed,3),chapters=6,pages=7,peak=peak,cookie_failures=cookie_failures,
                      txt_byte_identical=True,resume_requests=0,browser_alive_after_batch=True,helper_pid=pid,
                      navigations=sum(event.get('kind')=='request' and not event.get('transport') for event in events),
                      background_requests=sum(event.get('transport')=='session-http' for event in events))
    except Exception as ex:
        result.update(stage='failed',error=repr(ex),last_events=events[-6:])
    finally:
        save(progress,result); finished.set(); server.shutdown(); server.server_close()

threading.Thread(target=run,daemon=True).start()
def poll():
    if finished.is_set():
        panel.close()
        def done():
            if panel.closed.is_set():
                result['browser_exited_on_app_close']=all(s.process.poll() is not None for s in panel.sessions.values())
                save(progress,result); root.destroy()
            else: root.after(100,done)
        done(); return
    save(progress,dict(stage='running',requests=len(arrivals),chapters=sum(e['kind']=='chapter' for e in events),
                       latest=events[-1].get('message') if events else '',updated=time.time(),port=server.server_port))
    root.after(500,poll)
root.after(500,poll)
root.after(55000,lambda:control.cancel.set())
root.mainloop(); print(json.dumps(result,ensure_ascii=False),flush=True)
sys.exit(0 if result['stage']=='passed' else 1)

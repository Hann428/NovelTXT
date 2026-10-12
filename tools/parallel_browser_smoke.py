"""Two full synthetic books through real concurrent tabs and a shared site cookie."""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src'),str(ROOT/'tests')]
from novel_app import core, network
from novel_app.parallel import ParallelBatch, TaskControl, SiteGate
from novel_app.browser_parallel import BrowserBroker
from novel_app.storage import save
from test_parallel import synthetic_pages

home=ROOT/'evidence/parallel-browser'/uuid4().hex
pages=synthetic_pages()
lock=threading.Lock();active=peak=0;cookie_failures=0;starts=[];events=[]
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_GET(self):
        global active,peak,cookie_failures
        if self.path=='/seed':
            self.send_response(200);self.send_header('Set-Cookie','fixture_login=1; Path=/; SameSite=Lax')
            self.end_headers();self.wfile.write(b'<title>Fixture login</title><p>seeded</p>');return
        if not self.path.startswith('/book/'):
            self.send_response(404);self.end_headers();return
        with lock:
            active+=1;peak=max(peak,active);starts.append(time.monotonic())
        try:
            time.sleep(1.4)
            if 'fixture_login=1' not in self.headers.get('Cookie',''):
                with lock:cookie_failures+=1
                self.send_response(401);self.end_headers();return
            html=pages['https://books.example'+self.path].encode('utf-8')
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(html)));self.end_headers();self.wfile.write(html)
        finally:
            with lock:active-=1

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
base=f'http://127.0.0.1:{server.server_port}'
print(f'fixture={base} progress=evidence/parallel-browser-progress.json; automatic shutdown',flush=True)
original_network=network.validate_url;original_core=core.validate_url
def validate(url,resolve=False):
    return url if url.startswith(base+'/') else original_network(url,resolve=resolve)
network.validate_url=core.validate_url=validate
def emit(**event):
    events.append(event)
    save(ROOT/'evidence/parallel-browser-progress.json',dict(updated_at=time.time(),last_event=event,events=len(events),fixture_port=server.server_port))
    if event['kind'] in {'active','chapter','done','job_status','job_finished'}:
        print(json.dumps({k:v for k,v in event.items() if k!='result'},ensure_ascii=False),flush=True)
control=network.Control(emit)
broker=BrowserBroker(max_sites=2,headed=False)
opts=dict(delay=1,pause_every=1000,pause_seconds=0,mode='浏览器模式',proxy=False,refresh=False,
          output=home/'下载',books=2,site_concurrency=2,adaptive=False)
batch=ParallelBatch(control,opts,lambda host:home/'profile',lambda host:'fixture')
batch._browser=broker
started=time.monotonic()
try:
    seed=TaskControl(control,'seed',base+'/seed')
    broker.get('fixture',home/'profile',seed,SiteGate('fixture',control),base+'/seed')
    broker.release('fixture','seed')
    batch.run([('a',base+'/book/a/'),('b',base+'/book/b/')])
    done=[event for event in events if event['kind']=='done']
    assert len(done)==2, [event for event in events if event['kind']=='job_status']
    assert all(event['result']['status']=='complete' and event['result']['downloaded']==2 for event in done)
    assert peak==2 and cookie_failures==0
    for event in done:
        raw=Path(event['result']['output']).read_bytes()
        assert raw.index('第1章'.encode())<raw.index('第2章'.encode())
        assert raw.count('第二页内容'.encode())==1
        assert b'\r\n\r\n' in raw and '\u3000\u3000'.encode() in raw
    intervals=[later-earlier for earlier,later in zip(sorted(starts),sorted(starts)[1:])]
    assert min(intervals)>.9
    assert not broker.thread.is_alive()
    result=dict(passed=True,fixture_only=True,real_browser=True,complete_books=2,chapters_per_book=2,
                peak_same_site_requests=peak,shared_cookie_kept=True,minimum_request_interval=round(min(intervals),3),
                chapter_order_and_crlf_preserved=True,browser_thread_stopped=True,elapsed_seconds=round(time.monotonic()-started,2),
                target_site_parallel_verified=False)
    save(ROOT/'evidence/parallel-browser-result.json',result);print(json.dumps(result),flush=True)
finally:
    if broker.thread.is_alive():broker.close()
    network.validate_url=original_network;core.validate_url=original_core
    server.shutdown();server.server_close();thread.join(3)
    print('Fixture server stopped',flush=True)

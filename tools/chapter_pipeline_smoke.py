"""Real Edge fixture: compare v9 with v10 under the same per-site pacing."""
import importlib.util
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app import core, network, browser_parallel, parallel
from novel_app.storage import save

home=ROOT/'evidence/chapter-pipeline'/uuid4().hex
home.mkdir(parents=True)
progress=ROOT/'evidence/chapter-pipeline-progress.json'
stop=ROOT/'evidence/chapter-pipeline.stop'
phase='setup';events=[];arrivals=[];active=peak=0;cookie_failures=0;lock=threading.Lock()
prose='\u3000\u3000清晨的院子里桂花落在石桌上小林推开门沿着小路走到桥边看见远处有人正在收拾钓具。'
pages={'/book/a/':'<h1>流水线测试书</h1><div id="list">'+''.join(f'<a href="{i}.html">第{i}章 测试</a>' for i in range(1,7))+'</div>'}
for i in range(1,7):
    suffix=' (1/2)' if i==1 else ''
    nextpage='<a href="1_2.html">下一页</a>' if i==1 else ''
    pages[f'/book/a/{i}.html']=f'<h1>第{i}章 测试{suffix}</h1><div id="content"><p>{prose}章节{i}。</p><p></p><p>第二段落，继续保留文字。</p></div>{nextpage}'
pages['/book/a/1_2.html']=f'<h1>第1章 测试 (2/2)</h1><div id="content"><p>{prose}第二页内容。</p></div>'

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_GET(self):
        global active,peak,cookie_failures
        path=urlsplit(self.path).path
        if path not in pages:
            self.send_response(404);self.end_headers();return
        with lock:
            active+=1;peak=max(peak,active);arrivals.append(dict(phase=phase,path=path,started=time.monotonic()))
            save(ROOT/'evidence/chapter-pipeline-arrivals.json',arrivals)
        try:
            time.sleep(1.4)
            if path!='/book/a/' and 'fixture_login=1' not in self.headers.get('Cookie',''):
                with lock:cookie_failures+=1
                self.send_response(401);self.end_headers();return
            html=pages[path].encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Set-Cookie','fixture_login=1; Path=/; SameSite=Lax')
            self.send_header('Content-Length',str(len(html)));self.end_headers();self.wfile.write(html)
        finally:
            with lock:active-=1

def old_module(name):
    spec=importlib.util.spec_from_file_location(f'novel_app._baseline_{name}',ROOT/f'evidence/backups/rate-limit-20261009/{name}.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module

old_core,old_browser,old_parallel=[old_module(name) for name in ('core','browser_parallel','parallel')]
old_parallel.Runner=old_core.Runner
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
base=f'http://127.0.0.1:{server.server_port}';book_url=base+'/book/a/'
original_validate=network.validate_url
def validate(url,resolve=False):
    return url if urlsplit(url).netloc==urlsplit(base).netloc and url.startswith(base+'/') else original_validate(url,resolve=resolve)
network.validate_url=core.validate_url=old_core.validate_url=validate
config=lambda url:dict(content_selector='#content',title_selector='h1')
core.site_config=old_core.site_config=browser_parallel.site_config=old_browser.site_config=config
controls=[];brokers=[];measurements=[]
def emit(**event):
    events.append(dict(phase=phase,started=time.monotonic(),**event))
    save(progress,dict(phase=phase,updated_at=time.time(),last_event={k:v for k,v in event.items() if k!='result'},
                       events=len(events),completed=sum(e['kind']=='chapter' for e in events),fixture_port=server.server_port,stop_file=str(stop)))
    if event['kind'] in {'chapter','done','job_status'}:
        print(json.dumps(dict(phase=phase,**{k:v for k,v in event.items() if k!='result'}),ensure_ascii=False),flush=True)

finished=threading.Event()
def watchdog():
    deadline=time.monotonic()+75
    while not finished.wait(.2):
        if stop.exists() or time.monotonic()>deadline:
            for control in controls:control.cancel.set()
            return
threading.Thread(target=watchdog,daemon=True).start()
print(f'fixture={base}; progress={progress}; stop by creating {stop}; automatic shutdown',flush=True)
try:
    modes=(('v9',old_parallel,old_browser),('v10',parallel,browser_parallel))
    if '--only-v10' in sys.argv:
        from novel_app.storage import load
        measurements.append(load(ROOT/'evidence/chapter-pipeline-v9-result.json'))
        modes=modes[1:]
    for phase,mod,browser in modes:
        peak=0
        control=network.Control(emit);controls.append(control)
        broker=browser.BrowserBroker(max_sites=1,headed=False);brokers.append(broker)
        opts=dict(delay=1,pause_every=1000,pause_seconds=0,mode='浏览器模式',proxy=False,refresh=False,
                  output=home/phase/'downloads',books=1,site_concurrency=2,chapter_concurrency=3,adaptive=False)
        batch=mod.ParallelBatch(control,opts,lambda host:home/phase/'profile',lambda host:'fixture');batch._browser=broker
        started=time.monotonic();batch.run([('book',book_url)]);elapsed=time.monotonic()-started
        done=[e for e in events if e['phase']==phase and e['kind']=='done']
        assert len(done)==1 and done[0]['result']['status']=='complete' and done[0]['result']['downloaded']==6, events[-6:]
        raw=Path(done[0]['result']['output']).read_bytes()
        assert raw.count('第二页内容'.encode())==1 and b'\r\n\r\n' in raw and '\u3000\u3000'.encode() in raw
        assert all(raw.index(f'第{i}章'.encode())<raw.index(f'第{i+1}章'.encode()) for i in range(1,6))
        starts=[a['started'] for a in arrivals if a['phase']==phase]
        issued=[e['started'] for e in events if e['phase']==phase and e['kind']=='request']
        assert min(b-a for a,b in zip(issued,issued[1:]))>=.99,issued
        assert not broker.thread.is_alive()
        measurement=dict(version=phase,elapsed_seconds=round(elapsed,3),chapters=6,pages=7,peak_requests=peak,
                         minimum_interval=round(min(b-a for a,b in zip(starts,starts[1:])),3),output=done[0]['result']['output'],
                         session_http_requests=sum(e.get('transport')=='session-http' for e in events if e['phase']==phase))
        measurement['minimum_issued_interval']=round(min(b-a for a,b in zip(issued,issued[1:])),3)
        save(ROOT/f'evidence/chapter-pipeline-{phase}-result.json',measurement)
        measurements.append(measurement);print(json.dumps(measurement),flush=True)
    assert measurements[1]['peak_requests']==2 and cookie_failures==0
    assert Path(measurements[0]['output']).read_bytes()==Path(measurements[1]['output']).read_bytes()
    phase='resume';request_count=len(arrivals)
    resumed=parallel.ParallelBatch(network.Control(emit),opts,lambda host:home/'v10/profile',lambda host:'fixture')
    resumed.run([('book',book_url)])
    assert len(arrivals)==request_count and resumed._browser is None
    result=dict(passed=True,fixture_only=True,real_browser=True,cookie_failures=cookie_failures,measurements=measurements,
                txt_byte_identical=True,resume_network_requests=0,elapsed_reduction_percent=round(100*(1-measurements[1]['elapsed_seconds']/measurements[0]['elapsed_seconds']),1),
                target_site_speed_verified=False)
    save(ROOT/'evidence/chapter-pipeline-result.json',result);print(json.dumps(result),flush=True)
finally:
    finished.set()
    for control in controls:control.cancel.set()
    for broker in brokers:
        if broker.thread.is_alive():broker.close()
    server.shutdown();server.server_close();server_thread.join(3)
    print('Fixture server and browser threads stopped',flush=True)

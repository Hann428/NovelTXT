import threading
import time
from pathlib import Path
from collections import Counter

import httpx
import pytest

from novel_app import network, parallel
from novel_app.gui import session_site
from novel_app.storage import load


def options(tmp_path, **changes):
    return dict(delay=0, pause_every=1000, pause_seconds=0, mode='普通网页', proxy=False,
                refresh=False, output=tmp_path/'output', books=3, site_concurrency=2,
                adaptive=False, **changes)


def test_adaptive_increase_decrease_and_hard_ceiling():
    gate=parallel.SiteGate('example.org',network.Control(),limit=2,delay=0,pause_every=1000)
    for _ in range(40):
        gate.acquire(network.Control())
        gate.release(.1)
    assert gate.limit==gate.maximum==2
    gate.acquire(network.Control());gate.release(1,False)
    assert gate.limit==1 and gate.successes==0
    gate.next_start=0
    gate.acquire(network.Control());gate.release(10)
    assert gate.limit==1


def test_shared_gate_limits_inflight_requests():
    gate=parallel.SiteGate('example.org',network.Control(),limit=2,adaptive=False,delay=0,pause_every=1000)
    lock=threading.Lock()
    active=peak=0
    def request():
        nonlocal active,peak
        gate.acquire(network.Control())
        with lock:
            active+=1;peak=max(peak,active)
        time.sleep(.04)
        with lock: active-=1
        gate.release(.04)
    threads=[threading.Thread(target=request) for _ in range(6)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(2)
    assert all(not thread.is_alive() for thread in threads)
    assert peak==2 and gate.active==0


def test_interval_is_between_request_starts_without_extra_chapter_delay(monkeypatch):
    now=[100.0]
    monkeypatch.setattr(parallel.time,'monotonic',lambda:now[0])
    gate=parallel.SiteGate('example.org',network.Control(),limit=2,adaptive=False,delay=6,pause_every=1000)
    gate.acquire(network.Control())
    gate.start(network.Control())
    assert gate.next_start==106
    now[0]=104;gate.release(4)
    assert gate.next_start==106  # Completion does not add another six seconds.
    now[0]=106;gate.acquire(network.Control());gate.start(network.Control())
    assert gate.next_start==112
    gate.release(0)


def test_stop_selected_does_not_cancel_other_task():
    parent=network.Control()
    first=parallel.TaskControl(parent,'a','https://example.org/a')
    second=parallel.TaskControl(parent,'b','https://example.org/b')
    first.cancel.set()
    with pytest.raises(network.Cancelled):first.checkpoint()
    second.checkpoint()
    parent.cancel.set()
    with pytest.raises(network.Cancelled):second.checkpoint()


def test_pause_selected_and_global_stop_release_waiting_thread():
    parent=network.Control()
    task=parallel.TaskControl(parent,'a','https://example.org/a')
    task.paused.set()
    completed=threading.Event()
    def run():
        with pytest.raises(network.Cancelled):task.checkpoint()
        completed.set()
    thread=threading.Thread(target=run);thread.start()
    assert not completed.wait(.05)
    parent.cancel.set();thread.join(1)
    assert completed.is_set()


def test_verification_blocks_only_its_site_and_is_cancellable():
    parent=network.Control()
    control=parallel.TaskControl(parent,'a','https://a.example/')
    first=parallel.SiteGate('a.example',parent,delay=0)
    second=parallel.SiteGate('b.example',parent,delay=0)
    first.verification(True)
    finished=threading.Event()
    def blocked():
        with pytest.raises(network.Cancelled):first.acquire(control)
        finished.set()
    thread=threading.Thread(target=blocked);thread.start()
    second.acquire(network.Control());second.release(.1)
    assert not finished.is_set()
    control.cancel.set();thread.join(1)
    assert finished.is_set()


def synthetic_pages():
    pages={}
    for book in ('a','b'):
        base=f'https://books.example/book/{book}/'
        pages[base]=f'<h1>测试书{book}</h1><div id="list"><a href="1.html">第1章 起点</a><a href="2.html">第2章 远方</a></div>'
        prose='\u3000\u3000'+book+'清晨的院子里桂花落在石桌上小林推开门沿着小路走到桥边看见远处有人正在收拾钓具。'
        def chapter(title, suffix='', next_link=''):
            return f'<h1>{title}</h1><div id="content"><p>{prose}{suffix}</p><p></p><p>第二段落，继续保留文字。</p></div>{next_link}'
        pages[base+'1.html']=chapter('第1章 起点 (1/2)', next_link='<a href="1_2.html">下一页</a>')
        pages[base+'1_2.html']=chapter('第1章 起点 (2/2)','第二页内容')
        pages[base+'2.html']=chapter('第2章 远方','结束')
    return pages


def test_two_complete_books_parallel_and_resume_preserve_order_and_format(tmp_path,monkeypatch):
    pages=synthetic_pages()
    lock=threading.Lock();active=peak=0;calls=[]
    second_started=threading.Event()
    events=[]
    def handler(request):
        nonlocal active,peak
        with lock:
            active+=1;peak=max(peak,active);calls.append(str(request.url))
        if str(request.url).endswith('2.html'):second_started.set()
        if str(request.url).endswith('1.html'):assert second_started.wait(2)
        time.sleep(.04)
        with lock:active-=1
        return httpx.Response(200,text=pages[str(request.url)],headers={'content-type':'text/html; charset=utf-8'})
    original=network.Fetcher
    def fetcher(control,**kwargs):
        instance=original(control,**kwargs)
        instance.client.close()
        instance.client=httpx.Client(transport=httpx.MockTransport(handler))
        return instance
    monkeypatch.setattr(network,'Fetcher',fetcher)
    monkeypatch.setattr(network,'validate_url',lambda url,resolve=False:url)
    jobs=[('a','https://books.example/book/a/'),('b','https://books.example/book/b/')]
    opts=options(tmp_path)
    batch=parallel.ParallelBatch(network.Control(lambda **e:events.append(e)),opts,
                                 lambda host:tmp_path/'profiles'/host,session_site)
    batch.run(jobs)
    assert peak==2
    done=[e for e in events if e['kind']=='done']
    assert {e['item'] for e in done}=={'a','b'}
    assert all(e['result']['status']=='complete' and e['result']['downloaded']==2 for e in done)
    for event in done:
        content=Path(event['result']['output']).read_bytes()
        assert b'\r\n\r\n' in content and '\u3000\u3000'.encode() in content
        assert content.index('第1章'.encode())<content.index('第2章'.encode())
        assert content.count('第二页内容'.encode())==1
    first_calls=len(calls)
    batch.run(jobs)
    assert len(calls)==first_calls  # All valid cache pages reused without network.
    assert all(gate.active==0 for gate in batch.gates.values())


def test_one_site_failure_does_not_stop_other_site(tmp_path,monkeypatch):
    events=[];runs=[]
    class Runner:
        def __init__(self,fetcher,*args,**kwargs):self.fetcher=fetcher
        def run(self,url):
            runs.append(url)
            if 'a.example' in url:raise parallel.SiteBlocked('HTTP 429')
    monkeypatch.setattr(parallel,'Runner',Runner)
    jobs=[('a','https://a.example/1'),('b','https://b.example/1'),('c','https://a.example/2')]
    batch=parallel.ParallelBatch(network.Control(lambda **e:events.append(e)),options(tmp_path),
                                 lambda host:tmp_path/host,session_site)
    batch.run(jobs)
    assert 'https://b.example/1' in runs
    assert batch.gates['a.example'].blocked=='HTTP 429'
    assert batch.gates['b.example'].blocked is None


def test_plain_403_pauses_site_without_verification_and_other_site_continues(tmp_path,monkeypatch):
    events=[];runs=[]
    class Runner:
        def __init__(self,*args,**kwargs):pass
        def run(self,url):
            runs.append(url)
            if 'a.example' in url:raise network.AccessDenied(403,url)
    monkeypatch.setattr(parallel,'Runner',Runner)
    jobs=[('a','https://a.example/1'),('b','https://b.example/1'),('c','https://a.example/2')]
    batch=parallel.ParallelBatch(network.Control(lambda **e:events.append(e)),options(tmp_path),
                                 lambda host:tmp_path/host,session_site)
    batch.run(jobs)
    assert 'https://b.example/1' in runs and 'https://a.example/2' not in runs
    statuses={e['item']:e['status'] for e in events if e['kind']=='job_status'}
    assert statuses['a']==statuses['c']=='访问被拒绝'
    failures=[e for e in events if e['kind']=='job_status' and e['status']=='访问被拒绝']
    assert all(e['error_url']=='https://a.example/1' and e['error_status']==403 for e in failures)
    assert not any(e['kind']=='manual' for e in events)
    gate=batch.gates['a.example'];task=parallel.TaskControl(batch.control,'a',jobs[0][1])
    for operation in (gate.acquire,gate.start):
        with pytest.raises(network.AccessDenied):operation(task)
    assert not gate.verifying


def test_scheduler_honors_book_limit_and_fair_site_start(tmp_path,monkeypatch):
    events=[];lock=threading.Lock();active=peak=0;first=[];site_active=Counter();site_peak=Counter()
    class Runner:
        def __init__(self,*args,**kwargs):pass
        def run(self,url):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active);first.append(url)
                site=url.split('/')[2]
                site_active[site]+=1;site_peak[site]=max(site_peak[site],site_active[site])
            time.sleep(.02)
            with lock:active-=1;site_active[site]-=1
    monkeypatch.setattr(parallel,'Runner',Runner)
    opts=options(tmp_path);opts['books']=2
    jobs=[(str(i),f'https://a.example/book/{i}') for i in range(6)]+[('b','https://b.example/book/1')]
    batch=parallel.ParallelBatch(network.Control(lambda **e:events.append(e)),opts,
                                 lambda host:tmp_path/host,session_site)
    batch.run(jobs)
    assert peak==2 and len(first)==7
    assert 'https://b.example/book/1' in first[:2]
    assert site_peak == {'a.example':1,'b.example':1}


def test_single_book_chapters_overlap_finish_out_of_order_export_and_resume_in_order(tmp_path,monkeypatch):
    pages=synthetic_pages();base='https://books.example/book/a/'
    events=[];lock=threading.Lock();active=peak=0;finished=[];calls=[]
    second_started=threading.Event()
    def handler(request):
        nonlocal active,peak
        url=str(request.url)
        with lock:
            calls.append(url)
            if url.endswith('.html'):active+=1;peak=max(peak,active)
        if url.endswith('2.html'):second_started.set()
        if url.endswith('1.html'):assert second_started.wait(2)
        time.sleep(.12 if url.endswith('1.html') else .025)
        with lock:
            if url.endswith('.html'):active-=1;finished.append(url)
        return httpx.Response(200,text=pages[url],headers={'content-type':'text/html; charset=utf-8'})
    original=network.Fetcher
    def fetcher(control,**kwargs):
        instance=original(control,**kwargs);instance.client.close()
        instance.client=httpx.Client(transport=httpx.MockTransport(handler));return instance
    monkeypatch.setattr(network,'Fetcher',fetcher)
    monkeypatch.setattr(network,'validate_url',lambda url,resolve=False:url)
    batch=parallel.ParallelBatch(network.Control(lambda **e:events.append(e)),options(tmp_path),lambda host:tmp_path/host,session_site)
    batch.run([('a',base)])
    assert peak==2 and finished[0].endswith('2.html')
    done=[e for e in events if e['kind']=='done'][-1]['result']
    raw=Path(done['output']).read_bytes()
    assert done['status']=='complete' and raw.index('第1章'.encode())<raw.index('第2章'.encode())
    assert raw.count('第二页内容'.encode())==1 and b'\r\n\r\n' in raw
    downloaded=[e['downloaded'] for e in events if e['kind']=='chapter']
    assert downloaded==[1,2]
    assert any(e['kind']=='page' and e['page_current']==2 for e in events)
    count=len(calls);batch.run([('a',base)])
    assert len(calls)==count and not batch.gates['books.example'].inflight


def test_parallel_same_title_exports_cannot_overwrite_at_identical_timestamp(tmp_path,monkeypatch):
    from datetime import datetime
    from novel_app import core
    from novel_get.fetch import Page
    class FixedDate(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,10,9,12,0,0,tzinfo=tz)
    monkeypatch.setattr(core,'datetime',FixedDate)
    pages=synthetic_pages()
    for book in ('a','b'):
        url=f'https://books.example/book/{book}/'
        pages[url]=pages[url].replace(f'测试书{book}','同名小说')
    monkeypatch.setattr(parallel.JobFetcher,'get',lambda self,url,referer=None:Page(url,pages[url]))
    batch=parallel.ParallelBatch(network.Control(),options(tmp_path),lambda host:tmp_path/host,session_site)
    batch.run([('a','https://books.example/book/a/'),('b','https://books.example/book/b/')])
    exports=list((tmp_path/'output').rglob('*.txt'))
    assert len(exports)==2
    assert {path.name for path in exports} == {'同名小说.txt'}
    reports=[load(path) for path in (tmp_path/'output').rglob('*.report.json')]
    assert {report['input_url'] for report in reports}=={'https://books.example/book/a/','https://books.example/book/b/'}


def test_stop_chapter_pool_preserves_out_of_order_cache_and_resumes_only_missing(tmp_path,monkeypatch):
    from novel_get.fetch import Page
    from novel_app.storage import digest
    pages=synthetic_pages();base='https://books.example/book/a/'
    calls=[];events=[];parent=network.Control()
    def emit(**event):
        events.append(event)
        if event['kind']=='chapter':parent.cancel.set()
    parent.emit=emit
    def get(self,url,referer=None):
        calls.append(url)
        # Hold chapter 1 until chapter 2 has reached the coordinator and caused
        # cancellation. A fixed sleep raced disk/UI load and could save page 1.
        if url.endswith('1.html'):assert parent.cancel.wait(3), 'Coordinator never cancelled after chapter 2'
        self.control.checkpoint()
        return Page(url,pages[url])
    monkeypatch.setattr(parallel.JobFetcher,'get',get)
    batch=parallel.ParallelBatch(parent,options(tmp_path),lambda host:tmp_path/host,session_site)
    batch.run([('a',base)])
    folder=tmp_path/'output/.novel-cache'/digest(base)[:16]
    assert load(folder/'manifest.json')['status']=='stopped'
    assert load(folder/'chapters'/(digest(base+'2.html')+'.json'))['title']=='第2章 远方'
    assert not load(folder/'chapters'/(digest(base+'1.html')+'.json'))
    first=len(calls)
    resumed=parallel.ParallelBatch(network.Control(lambda **e:events.append(e)),options(tmp_path),lambda host:tmp_path/host,session_site)
    resumed.run([('a',base)])
    assert calls[first:]==[base+'1.html',base+'1_2.html']
    assert [e for e in events if e['kind']=='done'][-1]['result']['status']=='complete'

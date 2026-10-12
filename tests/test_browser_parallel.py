import asyncio
import threading
from types import SimpleNamespace

import pytest

from novel_app import network
from novel_app.parallel import TaskControl, SiteGate
from novel_app.browser_parallel import BrowserBroker


class FakePage:
    def __init__(self,context):
        self.context=context;self.url='about:blank';self.main_frame=object();self.listeners={}
    def on(self,event,callback):self.listeners.setdefault(event,[]).append(callback)
    def remove_listener(self,event,callback):self.listeners[event].remove(callback)
    def response(self):
        response=SimpleNamespace(request=SimpleNamespace(resource_type='document'),frame=self.main_frame,
                                 status=200 if self.context.verified else 403,headers={})
        for listener in self.listeners.get('response',[]):listener(response)
    async def goto(self,url,**kwargs):self.url=url;self.response()
    async def reload(self,**kwargs):self.response()
    async def content(self):
        return '<title>可读正文</title><p>完成</p>' if self.context.verified else '<title>Just a moment...</title>'
    async def evaluate(self,script):return await self.content()
    def is_closed(self):return False


class FakeContext:
    def __init__(self,verified=False):self.verified=verified;self.pages=[]
    async def new_page(self):
        page=FakePage(self);self.pages.append(page);return page
    async def close(self):pass


def fake_broker(monkeypatch):
    broker=BrowserBroker(headed=True)
    contexts={'a':FakeContext(),'b':FakeContext(True)}
    async def session(site,profile,control):
        if site not in broker.sessions:
            broker.sessions[site]=dict(context=contexts[site],pages={},idle=[],verification=asyncio.Lock())
            return broker.sessions[site],False
        return broker.sessions[site],True
    monkeypatch.setattr(broker,'_session',session)
    return broker,contexts


def test_verification_owner_shared_by_two_books_other_site_keeps_running(monkeypatch):
    manual=threading.Event();events=[]
    def emit(**event):
        events.append(event)
        if event['kind']=='manual':manual.set()
    parent=network.Control(emit);broker,contexts=fake_broker(monkeypatch)
    gate=SiteGate('a',parent,delay=0,limit=2,adaptive=False,pause_every=1000)
    other=SiteGate('b',parent,delay=0)
    results=[];errors=[]
    def request(site,item,gate):
        task=TaskControl(parent,item,f'https://{site}.example/{item}')
        held=False
        try:
            gate.acquire(task);held=True
            results.append((item,broker.get(site,'unused-profile',task,gate,task.url)))
        except Exception as exc:errors.append(exc)
        finally:
            if held:gate.release(.1)
            broker.release(site,item)
    first=threading.Thread(target=request,args=('a','first',gate))
    second=threading.Thread(target=request,args=('a','second',gate))
    try:
        first.start();assert manual.wait(3)
        assert gate.verifying
        second.start()
        request('b','other',other)
        assert any(item=='other' for item,_ in results)
        assert second.is_alive()
        async def solve():
            contexts['a'].verified=True
            for page in contexts['a'].pages:page.response()
        broker.call(solve())
        first.join(3);second.join(3)
        assert not first.is_alive() and not second.is_alive() and not errors
        assert {item for item,_ in results}=={'first','second','other'}
        assert sum(event['kind']=='manual' for event in events)==1
        assert not gate.verifying
    finally:
        parent.cancel.set()
        first.join(3)
        if second.ident:second.join(3)
        broker.close()


def test_global_stop_exits_manual_wait_and_closes_broker(monkeypatch):
    manual=threading.Event();errors=[]
    parent=network.Control(lambda **event:manual.set() if event['kind']=='manual' else None)
    broker,_=fake_broker(monkeypatch)
    task=TaskControl(parent,'first','https://a.example/book/')
    gate=SiteGate('a',parent,delay=0)
    def request():
        try:broker.get('a','unused-profile',task,gate,task.url)
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=request)
    try:
        thread.start();assert manual.wait(3)
        parent.cancel.set();thread.join(3)
        assert not thread.is_alive() and len(errors)==1 and isinstance(errors[0],network.Cancelled)
        assert not gate.verifying
    finally:
        parent.cancel.set();thread.join(3);broker.close()
    assert not broker.thread.is_alive()


def test_closed_browser_pauses_site_instead_of_reopening_for_every_book():
    broker=BrowserBroker()
    parent=network.Control();task=TaskControl(parent,'a','https://a.example/book/')
    async def setup():
        broker.sessions['a']=dict(context=FakeContext(),pages={},idle=[],closed=True)
    try:
        broker.call(setup())
        with pytest.raises(network.SiteBlocked):
            broker.call(broker._session('a','unused-profile',task))
        assert broker.runtime is None
    finally:broker.close()


@pytest.mark.parametrize('status', [None,200,403])
def test_plain_forbidden_page_never_starts_manual_wait(monkeypatch,status):
    broker,_=fake_broker(monkeypatch);events=[]
    control=TaskControl(network.Control(lambda **e:events.append(e)), 'a','https://a.example/chapter')
    gate=SiteGate('a',control.parent,delay=0)
    async def forbidden(self): return '<title>403 Forbidden</title><h1>403 Forbidden</h1><hr>nginx'
    def reply(self):
        if status is not None:
            response=SimpleNamespace(request=SimpleNamespace(resource_type='document'),frame=self.main_frame,
                                     status=status,headers={})
            for listener in self.listeners.get('response',[]):listener(response)
    monkeypatch.setattr(FakePage,'content',forbidden);monkeypatch.setattr(FakePage,'response',reply)
    try:
        with pytest.raises(network.AccessDenied): broker.get('a','unused',control,gate,control.url)
        assert not gate.verifying and not any(e['kind']=='manual' for e in events)
    finally: control.cancel.set();broker.close()


def test_challenge_becoming_plain_403_exits_manual_wait(monkeypatch):
    events=[];manual=threading.Event();errors=[]
    def emit(**event):
        events.append(event)
        if event['kind']=='manual':manual.set()
    parent=network.Control(emit);broker,contexts=fake_broker(monkeypatch)
    original=FakePage.content
    async def content(self):
        if getattr(self.context,'denied',False):return '<title>403 Forbidden</title><h1>403 Forbidden</h1>'
        return await original(self)
    monkeypatch.setattr(FakePage,'content',content)
    def response(self):
        reply=SimpleNamespace(request=SimpleNamespace(resource_type='document'),frame=self.main_frame,
                              status=403,headers={} if getattr(self.context,'denied',False) else {'cf-mitigated':'challenge'})
        for listener in self.listeners.get('response',[]):listener(reply)
    monkeypatch.setattr(FakePage,'response',response)
    control=TaskControl(parent,'a','https://a.example/chapter');gate=SiteGate('a',parent,delay=0)
    def request():
        try:broker.get('a','unused',control,gate,control.url)
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=request)
    try:
        thread.start();assert manual.wait(3)
        async def deny():
            contexts['a'].denied=True
            for page in contexts['a'].pages:page.response()
        broker.call(deny());thread.join(3)
        assert not thread.is_alive() and len(errors)==1 and isinstance(errors[0],network.AccessDenied)
        assert not gate.verifying and any(e['kind']=='manual_done' for e in events)
    finally:parent.cancel.set();thread.join(3);broker.close()


@pytest.mark.parametrize('status', [None, 200, 404, 410, 500])
def test_rendered_missing_page_not_waiting_for_human(monkeypatch, status):
    broker, _=fake_broker(monkeypatch)
    events=[]
    control=TaskControl(network.Control(lambda **e:events.append(e)), 'a', 'https://a.example/missing')
    gate=SiteGate('a', control.parent, delay=0)
    async def missing(self): return '<title>404 Not Found</title><h1>404 Not Found</h1>'
    def reply(self):
        if status is not None:
            response=SimpleNamespace(request=SimpleNamespace(resource_type='document'), frame=self.main_frame,
                                     status=status, headers={})
            for listener in self.listeners.get('response', []): listener(response)
    monkeypatch.setattr(FakePage, 'content', missing)
    monkeypatch.setattr(FakePage, 'response', reply)
    try:
        with pytest.raises(network.PageHTTPError): broker.get('a', 'unused', control, gate, control.url)
        assert not gate.verifying and not any(e['kind']=='manual' for e in events)
    finally: control.cancel.set(); broker.close()

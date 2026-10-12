import asyncio
import threading
import pytest

from novel_app import browser_parallel, network
from novel_app.parallel import SiteGate, TaskControl, ChapterControl
from test_browser_parallel import FakeContext, FakePage, fake_broker
from test_parallel import synthetic_pages


class FetchPage(FakePage):
    async def evaluate(self,script,url=None):
        if url is None:
            return await self.content()
        self.context.active+=1
        self.context.peak=max(self.context.peak,self.context.active)
        try:
            await asyncio.sleep(.025)
            self.context.fetches.append(url)
            limited=self.context.limited and len(self.context.fetches)==1
            return dict(url=url,code=429 if limited else 200,challenge=False,retry_after='0',
                        type='text/html; charset=utf-8',html='<title>Too Many Requests</title>' if limited else self.context.html[url])
        finally:self.context.active-=1

    async def goto(self,url,**kwargs):
        self.context.navigations.append(url)
        await super().goto(url,**kwargs)

    async def content(self):
        return self.context.html.get(self.url,'<title>可读目录</title>')


class FetchContext(FakeContext):
    def __init__(self,limited=False):
        super().__init__(True)
        self.limited=limited;self.active=self.peak=0
        self.fetches=[];self.navigations=[]
        self.html={url.replace('books.example','a.example'):value for url,value in synthetic_pages().items()}

    async def new_page(self):
        page=FetchPage(self);self.pages.append(page);return page


def setup(monkeypatch,limited=False):
    broker,contexts=fake_broker(monkeypatch);contexts['a']=FetchContext(limited)
    events=[];parent=network.Control(lambda **e:events.append(e))
    task=TaskControl(parent,'book','https://a.example/book/a/')
    gate=SiteGate('a',parent,delay=0,limit=2,adaptive=False,pause_every=1000);gate.rate_floor=0
    monkeypatch.setattr(browser_parallel,'site_config',lambda url:{'content_selector':'#content','title_selector':'h1'})
    return broker,contexts['a'],task,gate,events


def test_cached_directory_seeds_once_and_chapters_use_background_html_not_navigation(monkeypatch):
    broker,context,task,gate,events=setup(monkeypatch)
    results=[];errors=[]
    def request(index):
        child=ChapterControl(task,f'book:{index}')
        try:
            gate.acquire(child)
            results.append(broker.request('a','unused',child,gate,f'https://a.example/book/a/{index}.html',transport_key='book'))
        except Exception as exc:errors.append(exc)
        finally:gate.release(.1,control=child)
    threads=[threading.Thread(target=request,args=(i,)) for i in (1,2)]
    try:
        for thread in threads:thread.start()
        for thread in threads:thread.join(3)
        assert not errors and len(results)==2 and all(not t.is_alive() for t in threads)
        assert context.navigations==[task.url] and context.peak==2
        assert sorted(context.fetches)==['https://a.example/book/a/1.html','https://a.example/book/a/2.html']
        assert len(context.pages)==1 and not gate.inflight and gate.active==0
    finally:
        task.cancel.set()
        for thread in threads:thread.join(3)
        broker.close()


def test_background_429_automatically_recovers_and_never_opens_chapter(monkeypatch):
    broker,context,task,gate,events=setup(monkeypatch,limited=True)
    child=ChapterControl(task,'book:1');url='https://a.example/book/a/1.html'
    try:
        result=broker.request('a','unused',child,gate,url,transport_key='book')
        assert '第1章' in result.html
        assert context.fetches==[url,url] and context.navigations==[task.url]
        assert not any(e['kind']=='manual' for e in events)
        assert gate.limit==1 and gate.restricted and not gate.rate_recovering and not gate.inflight
    finally:task.cancel.set();broker.close()


@pytest.mark.parametrize('status', [200, 404, 410, 500])
def test_background_error_never_opens_verification_or_repeats_request(monkeypatch, status):
    broker, context, task, gate, events=setup(monkeypatch)
    original=FetchPage.evaluate
    async def evaluate(self, script, url=None):
        data=await original(self, script, url)
        if url is not None:
            data.update(code=status, html='<title>404 Not Found</title><h1>404 Not Found</h1>')
        return data
    monkeypatch.setattr(FetchPage, 'evaluate', evaluate)
    try:
        with pytest.raises(network.PageHTTPError):
            broker.request('a', 'unused', ChapterControl(task, 'book:1'), gate,
                           'https://a.example/book/a/1.html', transport_key='book')
        assert len(context.fetches)==1 and context.navigations==[task.url]
        assert not any(e['kind']=='manual' for e in events)
        assert not gate.verifying and not gate.blocked and not gate.inflight
    finally: task.cancel.set(); broker.close()


def test_background_plain_403_never_falls_back_or_starts_manual_wait(monkeypatch):
    broker,context,task,gate,events=setup(monkeypatch)
    original=FetchPage.evaluate
    async def evaluate(self,script,url=None):
        data=await original(self,script,url)
        if url is not None:data.update(code=403,html='<title>403 Forbidden</title><h1>403 Forbidden</h1><hr>nginx')
        return data
    monkeypatch.setattr(FetchPage,'evaluate',evaluate)
    try:
        with pytest.raises(network.AccessDenied):
            broker.request('a','unused',ChapterControl(task,'book:1'),gate,
                           'https://a.example/book/a/1.html',transport_key='book')
        assert len(context.fetches)==1 and context.navigations==[task.url]
        assert not gate.verifying and not any(e['kind']=='manual' for e in events)
    finally:task.cancel.set();broker.close()

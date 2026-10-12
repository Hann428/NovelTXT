import asyncio
import threading
import queue
import pytest

from novel_app.embedded_browser import NativeContext, NativePage, EmbeddedBroker
from novel_app.parallel import ParallelBatch
from novel_app.network import Control


class Session:
    def __init__(self):
        self.commands = []
        self.closed = False
        self.listeners = []

    def subscribe(self, listener): self.listeners.append(listener)
    def unsubscribe(self, listener): self.listeners.remove(listener)
    def evaluate(self, script, **_): return 'https://example.org/book/1'
    def request(self, op, **args):
        self.commands.append((op, args))
        if op == 'navigate': return {'url':args['url']}
        if op == 'evaluate': return {'ok': True}
        return True


def test_batch_detaches_pages_but_keeps_browser_and_origin_alive():
    async def run():
        session = Session()
        context = await NativeContext(session, lambda page:None).initialize()
        await context.new_page(); await context.new_page()
        await context.close()
        assert not session.closed and not session.listeners
        assert [(op,args['page']) for op,args in session.commands if op=='close_page'] == [('close_page','page-1'),('close_page','page-2')]
        assert session.commands[-1] == ('show',{'page':'main'})
        reused = await NativeContext(session, lambda page:None).initialize()
        assert reused.pages[0].url == 'https://example.org/book/1'
        await reused.close()
    asyncio.run(run())


def test_native_response_keeps_rate_and_challenge_headers_for_existing_gate():
    async def run():
        context = NativeContext(Session(), lambda page:None)
        page = NativePage(context,'main'); context.pages.append(page)
        replies = []
        page.on('response', replies.append)
        page.event({'kind':'response','code':429,'challenge':'','retry_after':'60'})
        assert replies[0].status == 429 and replies[0].headers['retry-after'] == '60'
        assert replies[0].frame is page and replies[0].request.resource_type == 'document'
        page.event({'kind':'response','code':403,'challenge':'challenge'})
        assert replies[1].headers['cf-mitigated'] == 'challenge'
        page.remove_listener('response', replies.append)
        await context.close()
    asyncio.run(run())


def test_navigation_completed_preserves_status_without_document_response_event():
    async def run():
        context=NativeContext(Session(), lambda page:None)
        page=NativePage(context, 'main'); replies=[]
        page.on('response', replies.append)
        page.event({'kind':'navigation', 'code':404, 'url':'https://example.org/missing'})
        assert page.url.endswith('/missing') and replies[0].status==404
        assert replies[0].headers=={} and replies[0].status_only
        await context.close()
    asyncio.run(run())


def test_native_navigation_start_resets_previous_document_state():
    async def run():
        context=NativeContext(Session(),lambda page:None)
        page=NativePage(context,'main');state={'code':403,'challenge':True}
        page.on('navigation_start',state.clear)
        page.event({'kind':'navigation_start','url':'https://example.org/forbidden'})
        assert state == {}
        await context.close()
    asyncio.run(run())


def test_native_js_argument_is_serialized_without_code_interpolation():
    async def run():
        context = NativeContext(Session(), lambda page:None)
        page = NativePage(context,'main')
        argument = 'https://example.org/";alert(1)//'
        await page.evaluate('url => url',argument)
        expression = context.session.commands[-1][1]['expression']
        assert '\\"' in expression and expression.startswith('(url => url)(')
        await context.close()
    asyncio.run(run())


def test_native_reload_uses_refresh_operation_instead_of_cached_navigation():
    async def run():
        session=Session();context=NativeContext(session,lambda page:None)
        page=NativePage(context,'main',url='https://example.org/chapter/1')
        await page.reload()
        assert session.commands==[('reload',{'page':'main'})]
        assert page.url=='https://example.org/chapter/1'
        await context.close()
    asyncio.run(run())


def test_browser_factory_is_lazy_and_defaults_are_unchanged():
    created = []
    marker = object()
    def factory(**options): created.append(options); return marker
    batch = ParallelBatch(Control(), {'books':2}, lambda host:None, lambda host:host, browser_factory=factory)
    assert not created and batch._browser is None
    assert batch.browser is marker and batch.browser is marker
    assert created == [{'max_sites':2,'headed':True}]


@pytest.mark.parametrize('backend,expected', [('内置浏览器','browser'),('独立浏览器','http')])
def test_auto_mode_uses_selected_backends_own_profile(tmp_path, monkeypatch, backend, expected):
    from novel_app import gui, parallel
    native = tmp_path/'native'; native.mkdir()
    legacy = tmp_path/'legacy-not-created'
    modes = []
    class Runner:
        def __init__(self, fetcher, *_args, **_kwargs): modes.append((fetcher.mode,fetcher.profile))
        def run(self, _url): pass
    monkeypatch.setattr(parallel,'Runner',Runner)
    app = gui.App.__new__(gui.App)
    app.control = Control()
    app.events = queue.Queue()
    app.browser_profile = lambda _host:legacy
    app.browser_panel = type('Panel',(),{'profile':lambda self,_site:native,'broker':lambda self,**_:None})()
    opts={'books':1,'browser_backend':backend,'mode':'自动选择','proxy':False,'delay':1,
          'pause_every':1000,'pause_seconds':0,'output':tmp_path/'downloads','refresh':False}
    app.work([('a','https://www.banshanren.com/novel/test')],'download',opts)
    assert modes == [(expected,native if backend=='内置浏览器' else legacy)]
    assert app.batch is None

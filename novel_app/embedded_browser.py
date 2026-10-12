"""Adapt native pages to the downloader's existing bounded browser queue."""
import asyncio
import json
import time
from collections import OrderedDict
from types import SimpleNamespace

from .browser_parallel import BrowserBroker, checkpoint
from novel_get.fetch import DownloadError


class NativePage:
    def __init__(self, context, key, url='about:blank'):
        self.context, self.key, self.url = context, key, url
        self.main_frame = self
        self.listeners = {}
        self.closed = False

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        if callback in self.listeners.get(event, []):
            self.listeners[event].remove(callback)

    def event(self, data):
        if data.get('kind') == 'navigation_start':
            for callback in tuple(self.listeners.get('navigation_start', [])):
                callback()
        if data.get('kind') == 'navigation':
            self.url = data.get('url') or self.url
            if data.get('code'):
                # WebView2 may omit the separate document response event.
                # NavigationCompleted still reports 404/410 reliably; preserve
                # existing challenge headers when updating only the status.
                response = SimpleNamespace(status=data['code'], headers={}, frame=self, status_only=True,
                                           request=SimpleNamespace(resource_type='document'))
                for callback in tuple(self.listeners.get('response', [])):
                    callback(response)
        if data.get('kind') == 'response':
            headers = {'cf-mitigated': data.get('challenge', ''), 'retry-after': data.get('retry_after', '')}
            response = SimpleNamespace(status=data['code'], headers=headers, frame=self,
                                       request=SimpleNamespace(resource_type='document'))
            for callback in tuple(self.listeners.get('response', [])):
                callback(response)

    async def command(self, op, **arguments):
        return await asyncio.to_thread(self.context.session.request, op, page=self.key, **arguments)

    async def goto(self, url, **_):
        result = await self.command('navigate', url=url)
        self.url = result['url']

    async def reload(self, **_):
        await self.command('reload')

    async def content(self):
        return await self.command('content')

    async def evaluate(self, script, arg=None):
        expression = '(' + script + ')(' + ('' if arg is None else json.dumps(arg, ensure_ascii=False)) + ')'
        return await self.command('evaluate', expression=expression)

    async def wait_for_selector(self, selector, **options):
        deadline = time.monotonic() + options.get('timeout', 10000) / 1000
        while time.monotonic() < deadline:
            visible = await self.evaluate("s => {const e=document.querySelector(s);return !!e && e.getClientRects().length>0;}", selector)
            if visible: return
            await asyncio.sleep(.2)
        raise DownloadError('正文元素等待超时')

    def is_closed(self):
        return self.closed or self.context.session.closed

    async def bring_to_front(self):
        self.context.show(self.key)
        await self.command('show')

    async def close(self):
        if not self.closed:
            await self.command('close_page')
            self.closed = True


class NativeContext:
    def __init__(self, session, show):
        self.session, self.show = session, show
        self.loop = asyncio.get_running_loop()
        self.pages = []
        self.session.subscribe(self._received)

    def _received(self, event):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._dispatch, event)

    def _dispatch(self, event):
        for page in self.pages:
            if page.key == event.get('page'):
                page.event(event)

    async def initialize(self):
        await asyncio.to_thread(self.session.request, 'new_page', page='main')
        url = await asyncio.to_thread(self.session.evaluate, 'location.href', page='main')
        self.pages.append(NativePage(self, 'main', url))
        return self

    async def new_page(self):
        # Existing batch cleanup keeps only "main", so keys stay bounded.
        key = 'page-' + str(len(self.pages))
        await asyncio.to_thread(self.session.request, 'new_page', page=key)
        page = NativePage(self, key); self.pages.append(page); return page

    async def close(self):
        self.session.unsubscribe(self._received)
        # Keep a live origin page and WebView2 environment throughout app life.
        for page in self.pages[1:]:
            await page.close()
        if self.pages and not self.session.closed:
            await asyncio.to_thread(self.session.request, 'show', page='main')
        for page in self.pages:
            page.listeners.clear()


class EmbeddedBroker(BrowserBroker):
    def __init__(self, provider, show, max_sites=3):
        self.provider, self.show_site = provider, show
        super().__init__(max_sites=max_sites, headed=True)

    async def _session(self, site, profile, control):
        async with self.session_lock:
            if site in self.sessions:
                if self.sessions[site]['context'].session.closed:
                    raise DownloadError('内置浏览器已退出，请停止并重新继续下载')
                self.sessions.move_to_end(site)
                return self.sessions[site], True
            await checkpoint(control)
            # Provider posts a request to Tk and resolves a Future on Tk's thread.
            native = await asyncio.to_thread(self.provider, site, control)
            context = await NativeContext(native, lambda page: self.show_site(site, page)).initialize()
            session = dict(context=context, pages={}, idle=list(context.pages), verification=asyncio.Lock(), closed=False)
            self.sessions[site] = session
            control.emit(kind='session', site=site, message=f'{site}：使用内置浏览器，共享登录与验证状态')
            return session, False

    async def _window(self, context, page, state):
        if state == 'normal':
            await page.bring_to_front()

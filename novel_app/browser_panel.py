"""Browser module in the main Tk window. All Tk calls remain on its UI thread."""
import json
import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from tkinter import ttk
import tkinter as tk
from urllib.parse import urlsplit

from .webview2 import NativeSession
from .embedded_browser import EmbeddedBroker
from .network import validate_url


class BrowserPanel:
    def __init__(self, root, home, backend, *, fixture_port=0):
        self.root, self.home, self.backend = root, home, backend
        self.events = queue.Queue()
        self.sessions, self.frames = {}, {}
        self.page_info = {}
        self.inspections = set()
        self.closing = False
        self.fixture_port = fixture_port
        self.closed = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='embedded-ui')
        self.panel = ttk.Frame(root, padding=10)
        self.panel.rowconfigure(1, weight=1); self.panel.columnconfigure(0, weight=1)
        bar = ttk.Frame(self.panel); bar.grid(row=0, column=0, sticky='ew', pady=(0, 8)); bar.columnconfigure(2, weight=1)
        ttk.Button(bar, text='返回下载队列', command=self.hide).grid(row=0, column=0, padx=(0,8))
        ttk.Combobox(bar, textvariable=backend, state='readonly', width=10, values=('内置浏览器', '独立浏览器')).grid(row=0,column=1)
        self.address = tk.StringVar()
        ttk.Entry(bar, textvariable=self.address).grid(row=0,column=2,sticky='ew',padx=8)
        ttk.Button(bar, text='打开', command=self.navigate).grid(row=0,column=3)
        ttk.Button(bar, text='刷新', command=self.reload).grid(row=0,column=4,padx=(8,0))
        self.tabs = ttk.Notebook(self.panel); self.tabs.grid(row=1,column=0,sticky='nsew')
        self.tabs.bind('<<NotebookTabChanged>>', self.selected)
        self.status = tk.StringVar(value='浏览器会话在软件运行期间保持。切换回队列或最小化不会关闭；网站可能要求再次验证。')
        ttk.Label(self.panel,textvariable=self.status,width=1,wraplength=900).grid(row=2,column=0,sticky='ew',pady=8)
        self.after = root.after(80,self.poll)

    def show(self, site=None):
        self.panel.place(x=0,y=0,relwidth=1,relheight=1); self.panel.lift()
        if site in self.frames: self.tabs.select(self.frames[site])

    def hide(self):
        self.panel.place_forget()

    def current(self):
        tab = self.tabs.select()
        return next((site for site, frame in self.frames.items() if str(frame) == tab), None)

    def selected(self, _=None):
        site = self.current()
        if site:
            session = self.sessions[site]
            self.address.set(getattr(session,'current_url',''))
            self.display_page(site)

    def display_page(self, site):
        session = self.sessions[site]
        info = self.page_info.get((site, session.current_page), {})
        title = info.get('title') or info.get('url') or '网页'
        code = info.get('code')
        response = f' · HTTP {code}' if code and code >= 400 else ''
        note = ('；仅查看失败页面，下载任务不会自动继续' if session.current_page == 'inspection'
                else '；下载进度请返回队列查看')
        self.status.set(f'当前浏览页：{title}{response}{note}')

    def open_url(self, site, url):
        """A separate inspection page never navigates a downloader-owned page."""
        validate_url(url)
        if self.closing or site in self.inspections:
            return
        self.inspections.add(site)
        result = Future()
        self.events.put(('create', site, result))
        self.show(site)
        self.status.set('正在打开失败页面；下载保持原状态，可随时返回队列')
        def run():
            try:
                session = result.result(timeout=30)
                session.request('new_page', page='inspection')
                session.request('show', page='inspection')
                self.events.put(('reveal', site, None))
                session.request('navigate', page='inspection', url=url)
            except Exception as ex:
                result.cancel()
                self.events.put(('error', site, str(ex)))
            finally:
                self.events.put(('inspection_done', site, None))
        self.pool.submit(run)

    def profile(self, site):
        # Reuse the user's just-verified prototype profiles without copying a live
        # database or changing the legacy Playwright browser profile.
        old_host = {'41nr.com':'m.41nr.com', 'banshanren.com':'www.banshanren.com'}.get(site,site)
        old = self.home / 'evidence/webview2-prototype/profiles' / old_host
        return old if old.is_dir() else self.home / 'data/webview2-profiles' / site

    def provider(self, site, control):
        result = Future()
        self.events.put(('create',site,result))
        try:
            while True:
                control.checkpoint()
                if self.closing: raise RuntimeError('内置浏览器正在关闭')
                try: return result.result(timeout=.2)
                except TimeoutError: continue
        except Exception:
            result.cancel()
            raise

    def broker(self, max_sites=3, headed=True):
        return EmbeddedBroker(self.provider, lambda site,page:self.events.put(('show',site,page)),max_sites)

    def operate(self, site, op, *, page=None, **args):
        if site not in self.sessions: return
        session = self.sessions[site]
        page = page or getattr(session, 'current_page', 'main')
        def run():
            try: session.request(op, page=page, **args)
            except Exception as ex: self.events.put(('error',site,str(ex)))
        self.pool.submit(run)

    def navigate(self):
        site = self.current(); url = self.address.get().strip()
        if site: self.operate(site,'navigate',url=url)

    def reload(self):
        site = self.current()
        if site: self.operate(site,'reload')

    def poll(self):
        for _ in range(50):
            try: kind,site,data = self.events.get_nowait()
            except queue.Empty: break
            if kind == 'create':
                if data.cancelled(): continue
                try:
                    if self.closing: raise RuntimeError('浏览器正在关闭')
                    if site not in self.sessions or self.sessions[site].closed:
                        if site not in self.frames:
                            frame = ttk.Frame(self.tabs); self.tabs.add(frame,text=site); self.frames[site] = frame
                        self.root.update_idletasks()
                        self.sessions[site] = NativeSession(self.frames[site].winfo_id(), self.profile(site),
                            emit=lambda event,site=site:self.events.put(('event',site,event)), fixture_port=self.fixture_port)
                        self.sessions[site].current_page = 'main'
                    data.set_result(self.sessions[site])
                except Exception as ex: data.set_exception(ex)
            elif kind == 'show':
                self.show(site)
                self.operate(site, 'show', page=data)
            elif kind == 'reveal':
                self.show(site)
            elif kind == 'inspection_done':
                self.inspections.discard(site)
            elif kind == 'event':
                if data.get('kind') == 'page_selected':
                    self.sessions[site].current_page = data['page']
                    self.sessions[site].current_url = data.get('url','')
                    if site == self.current():
                        self.address.set(data.get('url',''))
                        self.display_page(site)
                if data.get('kind') in {'navigation_start', 'navigation'}:
                    session = self.sessions[site]
                    self.page_info[(site, data.get('page'))] = data
                    if data.get('page') == session.current_page:
                        session.current_url = data.get('url','')
                        if site == self.current():
                            self.address.set(session.current_url)
                            self.display_page(site)
                elif data.get('kind') in ('fatal','process_failed','exit'):
                    self.status.set(f'{site}：内置浏览器异常，请停止任务后重试；如原型仍打开，请先关闭原型。')
            elif kind == 'error': self.status.set(f'{site}：{data}')
        if not self.closing: self.after = self.root.after(80,self.poll)

    def close(self):
        if self.closing: return
        self.closing = True
        try: self.root.after_cancel(self.after)
        except tk.TclError: pass
        def finish():
            self.pool.shutdown(wait=True,cancel_futures=True)
            for session in self.sessions.values():
                try: session.close()
                except Exception: pass
            self.closed.set()
        threading.Thread(target=finish,name='webview2-close',daemon=True).start()

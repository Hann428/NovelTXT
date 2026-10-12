"""Standalone compatibility prototype; never opens legacy download profiles."""
import argparse
import json
import queue
import sys
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import ttk
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor' / 'Novel-Get' / 'src'))
from novel_app.webview2 import NativeSession
from novel_app.browser_parallel import LIGHTWEIGHT_GET

DEFAULTS = ['https://m.41nr.com/book/28425/', 'https://www.banshanren.com/novel/cunsemanyuan']


class Prototype:
    def __init__(self, root, home, urls):
        self.root, self.home = root, home
        self.sessions = {}
        self.events = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='webview-prototype')
        self.closing = False
        root.title('逐页 · 内置浏览器兼容性原型（不影响当前下载）')
        root.geometry('1120x820'); root.minsize(760, 540)
        root.rowconfigure(2, weight=1); root.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(root, padding=8); toolbar.grid(row=0, column=0, sticky='ew')
        toolbar.columnconfigure(0, weight=1)
        self.url = tk.StringVar(value=urls[0])
        entry = ttk.Entry(toolbar, textvariable=self.url); entry.grid(row=0, column=0, sticky='ew')
        entry.bind('<Return>', lambda _: self.open_url())
        ttk.Button(toolbar, text='打开地址', command=self.open_url).grid(row=0, column=1, padx=5)
        ttk.Button(toolbar, text='后退', command=lambda: self.command('back')).grid(row=0, column=2)
        ttk.Button(toolbar, text='刷新', command=lambda: self.command('reload')).grid(row=0, column=3, padx=5)
        ttk.Button(toolbar, text='检查验证状态', command=self.inspect).grid(row=0, column=4)
        ttk.Button(toolbar, text='同会话后台请求测试', command=self.fetch).grid(row=0, column=5, padx=5)
        ttk.Label(root, text='在下方网页内手动完成验证/登录。切换标签不会关闭会话；原型关闭后仍保留登录资料。', padding=8).grid(row=1, column=0, sticky='w')
        self.notebook = ttk.Notebook(root); self.notebook.grid(row=2, column=0, sticky='nsew', padx=8)
        self.notebook.bind('<<NotebookTabChanged>>', self.selected)
        self.status = tk.StringVar(value='正在初始化 WebView2…')
        ttk.Label(root, textvariable=self.status, padding=8, wraplength=1000).grid(row=3, column=0, sticky='ew')
        ttk.Label(root, text='仅原型，不会启动整书下载。浏览器网络由 WebView2 自身发起，没有开放远程调试端口。', padding=8).grid(row=4, column=0, sticky='w')
        self.log = home / 'prototype.jsonl'; home.mkdir(parents=True, exist_ok=True)
        self.record({'kind': 'prototype_start', 'pid': __import__('os').getpid(), 'urls': urls})
        for url in urls:
            self.add_site(url)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def record(self, event):
        # Do not persist HTML, cookies, signed URL queries, credentials or JS values.
        safe = {key: value for key, value in event.items() if key not in ('html', 'url', 'expression')}
        if event.get('url'):
            p = urlsplit(event['url']); safe['location'] = p.scheme + '://' + p.netloc + p.path
        safe['time'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        with self.log.open('a', encoding='utf-8') as file:
            file.write(json.dumps(safe, ensure_ascii=False) + '\n')

    def add_site(self, url):
        site = urlsplit(url).hostname or ''
        if not site: raise ValueError('请输入有效网址')
        if site not in self.sessions:
            frame = ttk.Frame(self.notebook); self.notebook.add(frame, text=site)
            self.notebook.select(frame); self.root.update_idletasks()
            session = NativeSession(frame.winfo_id(), self.home / 'profiles' / site,
                                    emit=lambda event, site=site: self.events.put((site, event)))
            self.sessions[site] = (frame, session, url)
            self.record({'kind': 'helper_started', 'site': site, 'pid': session.process.pid})
            self.submit(site, lambda: session.request('navigate', url=url), '打开页面')
        return site

    def current(self):
        selected = self.notebook.select()
        return next((site for site, data in self.sessions.items() if str(data[0]) == selected), None)

    def selected(self, _=None):
        site = self.current()
        if site: self.url.set(self.sessions[site][2])

    def submit(self, site, work, label):
        self.status.set(f'{site}：{label}中…界面仍可操作')
        future = self.pool.submit(work)
        def done(f):
            try: event = {'kind': 'operation', 'label': label, 'result': f.result()}
            except Exception as ex: event = {'kind': 'operation_error', 'label': label, 'error': str(ex)}
            self.events.put((site, event))
        future.add_done_callback(done)

    def open_url(self):
        url = self.url.get().strip(); site = urlsplit(url).hostname
        if not site or urlsplit(url).scheme not in ('http', 'https'):
            self.status.set('请输入 http/https 网址'); return
        if site not in self.sessions:
            self.add_site(url); return
        frame, session, _ = self.sessions[site]; self.notebook.select(frame)
        self.submit(site, lambda: session.request('navigate', url=url), '打开页面')

    def command(self, op):
        site = self.current()
        if site:
            session = self.sessions[site][1]
            self.submit(site, lambda: session.request(op), op)

    def inspect(self):
        site = self.current()
        if not site: return
        session = self.sessions[site][1]
        script = """JSON.stringify({title:document.title, characters:document.body.innerText.length,
          challenge:/just a moment|verify you are human|checking your browser|验证您是真人|验证你是人类/i.test(document.title+' '+document.body.innerText.slice(0,1800)),
          links:document.querySelectorAll('a[href]').length, ready:document.readyState})"""
        self.submit(site, lambda: json.loads(session.evaluate(script)), '验证状态')

    def fetch(self):
        site = self.current()
        if not site: return
        session = self.sessions[site][1]
        # One normal same-origin request, no automated CAPTCHA clicking.
        expression = '(' + LIGHTWEIGHT_GET + ')(location.href).then(r=>JSON.stringify({code:r.code,characters:(r.html||" ").length,challenge:r.challenge,error:r.error||null}))'
        self.submit(site, lambda: json.loads(session.evaluate(expression)), '后台请求')

    def poll(self):
        while True:
            try: site, event = self.events.get_nowait()
            except queue.Empty: break
            if event.get('kind') == 'navigation':
                frame, session, old = self.sessions[site]
                self.sessions[site] = (frame, session, event.get('url') or old)
                if site == self.current(): self.url.set(self.sessions[site][2])
            self.record({'site': site, **event})
            self.status.set(site + '：' + json.dumps({k:v for k,v in event.items() if k not in ('url', 'hwnd', 'parent')}, ensure_ascii=False))
        if not self.closing: self.root.after(100, self.poll)

    def close(self):
        if self.closing: return
        self.closing = True; self.status.set('正在保存会话并关闭原型浏览器…')
        def shutdown():
            for _, session, _ in self.sessions.values(): session.close()
            self.events.put(('', {'kind': 'closed'}))
        threading.Thread(target=shutdown, daemon=True).start()
        def finish():
            if all(session.closed for _, session, _ in self.sessions.values()):
                self.record({'kind': 'prototype_closed'}); self.pool.shutdown(wait=False, cancel_futures=True); self.root.destroy()
            else: self.root.after(100, finish)
        finish()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=ROOT / 'evidence' / 'webview2-prototype')
    parser.add_argument('urls', nargs='*')
    args = parser.parse_args()
    app = Prototype(tk.Tk(), args.home.resolve(), args.urls or DEFAULTS)
    app.root.mainloop()

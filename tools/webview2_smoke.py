"""Real WebView2 integration checks against a local fixture, no live-site load."""
import base64
import hashlib
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if '--bundle' in sys.argv:
    sys._MEIPASS = str(Path(sys.argv[sys.argv.index('--bundle') + 1]).resolve())
from novel_app.webview2 import NativeSession

home = ROOT / 'evidence' / ('webview2-smoke-' + time.strftime('%Y%m%d-%H%M%S'))
home.mkdir(parents=True)
progress = home / 'progress.json'
active = peak = requests = 0
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global active, peak, requests
        with lock:
            active += 1; peak = max(peak, active); requests += 1
        try:
            if self.path.startswith('/chapter'):
                time.sleep(.35)
                if 'session=fixture-login' not in self.headers.get('Cookie', ''):
                    self.send_response(401); self.end_headers(); return
                body = '第一段，保留排版。\n\n　　第二段。'.encode()
            else:
                body = '<!doctype html><meta charset="utf-8"><title>内置浏览器本地测试</title><h1>WebView2 显示测试</h1><p>浏览器位于 Tk 软件窗口内。</p>'.encode()
            self.send_response(200)
            if self.path.startswith('/login'):
                self.send_header('Set-Cookie', 'session=fixture-login; Max-Age=86400; HttpOnly; SameSite=Lax; Path=/')
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        finally:
            with lock: active -= 1
    def log_message(self, *_): pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_port
base = f'http://127.0.0.1:{port}'
root = tk.Tk(); root.title('逐页 · WebView2 本地自动测试'); root.geometry('860x580')
root.rowconfigure(1, weight=1); root.columnconfigure(0, weight=1)
label = ttk.Label(root, text='本地原型测试中，不影响当前小说下载', padding=10); label.grid(row=0, column=0, sticky='ew')
frame = ttk.Frame(root); frame.grid(row=1, column=0, sticky='nsew'); root.update_idletasks()
parent_hwnd = frame.winfo_id()
session = NativeSession(parent_hwnd, home / 'profile', fixture_port=port)
events = queue.Queue()
result = {'pid': os.getpid(), 'helper_pid': session.process.pid, 'home': str(home), 'expected_parent': parent_hwnd}


def state(stage, **values):
    result.update(stage=stage, updated=time.strftime('%Y-%m-%dT%H:%M:%S%z'), **values)
    progress.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    events.put(stage)


def run():
    global session
    try:
        state('initializing')
        session.request('navigate', url=base + '/login')
        status = session.request('status')
        assert status['parent'] == parent_hwnd, (parent_hwnd, status)
        assert status['width'] > 500 and status['height'] > 300, status
        title = session.evaluate('document.title')
        assert title == '内置浏览器本地测试', title
        png = base64.b64decode(session.request('snapshot'))
        (home / 'embedded-page.png').write_bytes(png)
        state('native child and rendered page OK', embedding=status, title=title)
        expression = "Promise.all(['/chapter/1','/chapter/2'].map(async u=>{let r=await fetch(u,{credentials:'include'});return {code:r.status,text:await r.text()}}))"
        bodies = session.evaluate(expression)
        assert all(r['code'] == 200 and r['text'] == '第一段，保留排版。\n\n　　第二段。' for r in bodies), bodies
        assert peak >= 2, peak
        state('concurrent cookie-backed requests OK', peak=peak, text_hash=hashlib.sha256(bodies[0]['text'].encode()).hexdigest())
        denied = []
        for url in ('file:///C:/Windows/win.ini', 'http://127.0.0.1:1/private', 'http://192.168.1.1/'):
            try: session.request('navigate', url=url)
            except RuntimeError: denied.append(url)
        assert len(denied) == 3
        state('private URLs denied', denied_count=len(denied))
        first = session.process.pid; session.close(); assert session.process.poll() is not None
        time.sleep(1)
        session = NativeSession(parent_hwnd, home / 'profile', fixture_port=port)
        session.request('navigate', url=base + '/chapter/3')
        persisted = session.evaluate("fetch('/chapter/4',{credentials:'include'}).then(r=>r.status)")
        assert persisted == 200, persisted
        state('restart persisted login OK', first_helper=first, second_helper=session.process.pid, cookie_after_restart=persisted)
        events.put('resize')
        time.sleep(.7)
        resized = session.request('status')
        assert abs(resized['width'] - resized['parent_width']) < 3 and abs(resized['height'] - resized['parent_height']) < 3, resized
        assert resized['width'] > status['width'], (status, resized)
        session.close(); assert session.process.poll() is not None
        state('passed', resized=resized, requests=requests, helper_exited=True)
    except Exception as ex:
        state('failed', error=repr(ex))
        try: session.close()
        except Exception: pass
    finally:
        server.shutdown(); events.put('done')


def poll():
    while True:
        try: event = events.get_nowait()
        except queue.Empty: break
        if event == 'resize': root.geometry('1060x680')
        elif event == 'done': root.destroy(); return
        else: label.config(text=event)
    root.after(50, poll)


threading.Thread(target=run, daemon=True).start()
root.after(50, poll)
root.after(55000, lambda: (state('watchdog_timeout'), root.destroy()))
root.mainloop()
print(json.dumps(result, ensure_ascii=False))
sys.exit(0 if result.get('stage') == 'passed' else 1)

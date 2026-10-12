"""Native WebView2 child window. No debug port or website-visible IPC bridge."""
import json
import subprocess
import sys
import threading
from concurrent.futures import Future
from pathlib import Path


def host_binary():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    release = base / 'native' / 'release' / 'WebViewHost.exe'
    return release if release.is_file() else base / 'native' / 'bin' / 'WebViewHost.exe'


class NativeSession:
    def __init__(self, parent_hwnd, profile, emit=lambda event: None, *, fixture_port=0):
        exe = host_binary()
        if not exe.is_file():
            raise RuntimeError('内置浏览器组件未构建，请运行 native/build_webview2.ps1')
        self.emit = emit
        self.lock = threading.Lock()
        self.pending, self.counter = {}, 0
        self.listeners = []
        self.ready = threading.Event()
        self.startup_error = None
        self.closed = False
        profile = Path(profile).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [str(exe), str(int(parent_hwnd)), str(profile), str(int(fixture_port))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='strict', bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.reader = threading.Thread(target=self._read, name='webview2-ipc', daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                event = json.loads(line.lstrip('\ufeff'))
                if event.get('kind') == 'ready':
                    self.ready.set()
                if event.get('kind') == 'fatal':
                    self.startup_error = event.get('error', 'WebView2 启动失败')
                    self.ready.set()
                if 'id' in event:
                    with self.lock:
                        future = self.pending.pop(event['id'], None)
                    if future is not None and not future.done():
                        if 'error' in event:
                            future.set_exception(RuntimeError(event['error']))
                        else:
                            future.set_result(event.get('result'))
                else:
                    self.emit(event)
                    with self.lock:
                        listeners = list(self.listeners)
                    for listener in listeners:
                        try:
                            listener(event)
                        except RuntimeError:
                            # A batch event loop may already be detaching.
                            pass
        except (ValueError, OSError) as exc:
            self.startup_error = str(exc)
        finally:
            self.closed = True
            self.ready.set()
            with self.lock:
                futures = list(self.pending.values())
                self.pending.clear()
            for future in futures:
                if not future.done():
                    future.set_exception(RuntimeError(self.startup_error or '内置浏览器已退出'))
            self.emit({'kind': 'exit', 'pid': self.process.pid})

    def subscribe(self, listener):
        with self.lock:
            self.listeners.append(listener)

    def unsubscribe(self, listener):
        with self.lock:
            if listener in self.listeners:
                self.listeners.remove(listener)

    def request(self, op, *, timeout=30, **arguments):
        """Call from workers only: waiting for browser initialization never freezes Tk."""
        if not self.ready.wait(timeout):
            raise TimeoutError('内置浏览器初始化超时')
        if self.closed or self.startup_error:
            raise RuntimeError(self.startup_error or '内置浏览器已关闭')
        future = Future()
        with self.lock:
            self.counter += 1
            identity = self.counter
            self.pending[identity] = future
            try:
                self.process.stdin.write(json.dumps({'id': identity, 'op': op, **arguments}, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.pending.pop(identity, None)
                raise
        try:
            return future.result(timeout=timeout)
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def evaluate(self, expression, *, page='main', timeout=30):
        return self.request('evaluate', page=page, expression=expression, timeout=timeout)

    def close(self):
        """Graceful shutdown of our own helper; force only that helper if it hangs."""
        if self.process.poll() is not None:
            return
        try:
            self.request('shutdown', timeout=3)
            self.process.wait(timeout=5)
        except (RuntimeError, OSError, TimeoutError, subprocess.TimeoutExpired):
            self.process.terminate()
            self.process.wait(timeout=5)
        finally:
            self.closed = True
            for stream in (self.process.stdin, self.process.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

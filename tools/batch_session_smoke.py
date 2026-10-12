"""Real-browser session test against an isolated loopback fixture, not a target site."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'vendor/Novel-Get/src')]
import novel_app.network as network
from novel_app.storage import save


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/book/1':
            body = '<title>First book</title><script>document.cookie="fixture_verified=1; path=/"; sessionStorage.setItem("fixture_verified","1");</script><p>First book content</p>'
        else:
            cookies = self.headers.get('Cookie', '')
            body = '<title>Second book</title><p id="state">missing</p><script>if (sessionStorage.getItem("fixture_verified")==="1") document.getElementById("state").textContent="same-tab-session-kept";</script>'
            body += '<p>' + ('cookie-kept' if 'fixture_verified=1' in cookies else 'cookie-missing') + '</p>'
        encoded = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
port = server.server_port
print(f'Fixture port={port}; stops automatically in finally', flush=True)
# Test-only exception. Production validation still rejects local/private URLs.
original_validate = network.validate_url
def fixture_validate(url, resolve=False):
    if url.startswith(f'http://127.0.0.1:{port}/'):
        return url
    return original_validate(url, resolve=resolve)
network.validate_url = fixture_validate
home = ROOT / 'evidence/batch-session-smoke' / uuid4().hex
control = network.Control()
try:
    with network.FetcherPool(control) as pool:
        opts = dict(mode='browser', profile=home / 'profile', headless=True, manual=False, delay=1)
        first, reused = pool.get(**opts)
        first.get(f'http://127.0.0.1:{port}/book/1')
        tab = first.tab
        second, reused = pool.get(**opts)
        page = second.get(f'http://127.0.0.1:{port}/book/2')
        assert reused and second is first and second.tab is tab
        assert 'cookie-kept' in page.html and 'cookie-missing' not in page.html
        assert '>same-tab-session-kept<' in page.html
    result = dict(passed=True, real_browser=True, fixture_only=True, same_tab=True,
                  cookie_kept=True, session_storage_kept=True, live_sessions_after_exit=len(pool.sessions),
                  target_site_no_repeat_challenge_verified=False)
    save(ROOT / 'evidence/batch-session-smoke-result.json', result)
    print(json.dumps(result), flush=True)
finally:
    network.validate_url = original_validate
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    print('Fixture server stopped', flush=True)

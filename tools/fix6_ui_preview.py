"""Inspect the queue and real native browser using an isolated localhost fixture."""
import argparse
import base64
import ctypes
import json
import subprocess
import sys
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.gui import App
from novel_app.storage import save
import novel_app.browser_panel as panel_module

parser=argparse.ArgumentParser();parser.add_argument('--capture-python',required=True);args=parser.parse_args()
home=ROOT/'evidence/fix6-ui-preview'
url='https://m.41nr.com/book/28425/'
failed='https://m.41nr.com/chapter/28425/5828743.html'
save(home/'data/preferences.json',{'output':str(home/'downloads')})
save(home/'data/queue.json',[
    dict(title='都市奇缘',status='访问被拒绝',progress='1049/1929',url=url,error_url=failed,error_status=403,error_message='HTTP 403：网站拒绝访问；断点保留'),
    dict(title='男欢女爱',status='可继续（缓存）',progress='39/560',url='https://m.41nr.com/book/20091/')])
requests=[]
class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append(self.path)
        status=403 if self.path=='/chapter/836' else 200
        title='403 Forbidden' if status==403 else '后台目录'
        html=f'<html><head><meta charset="utf-8"><title>{title}</title></head><body><h1 style="text-align:center">{title}</h1><hr><p style="text-align:center">nginx</p></body></html>'.encode('utf-8')
        self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(html)));self.end_headers();self.wfile.write(html)
    def log_message(self,*_):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
threading.Thread(target=server.serve_forever,daemon=True).start()
prefix=f'http://127.0.0.1:{server.server_port}'
original_validate=panel_module.validate_url
panel_module.validate_url=lambda address:address if address.startswith(prefix+'/') else original_validate(address)
root=tk.Tk();app=App(root,home);panel=app.browser_panel;panel.fixture_port=server.server_port
root.geometry('1100x760+20+20');root.update()
item=app.table.get_children()[0];app.table.selection_set(item);app.show_selected_error()
original_open=panel.open_url
panel.open_url=lambda site,address:original_open(site,prefix+'/chapter/836') if address==failed else original_open(site,address)

def pump_until(predicate, timeout=30):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>deadline:raise RuntimeError('fixture timed out: '+panel.status.get())
        root.update();time.sleep(.02)
def capture(name):
    root.update_idletasks()
    top=ctypes.windll.user32.GetAncestor(root.winfo_id(),2)
    script='from PIL import ImageGrab;import sys;ImageGrab.grab(window=int(sys.argv[1])).save(sys.argv[2])'
    subprocess.run([args.capture_python,'-c',script,str(top),str(home/name)],check=True,timeout=10,capture_output=True)
try:
    assert str(app.failure_button['state'])=='normal'
    app.failure_button.focus_set();root.update();capture('queue.png')
    root.geometry('940x760');root.update();capture('queue-minimum.png')
    assert app.table.winfo_height()>=180
    root.geometry('1100x760');root.update();app.open_failure_page()
    pump_until(lambda:not panel.inspections and 'HTTP 403' in panel.status.get())
    assert panel.address.get()==prefix+'/chapter/836' and app.worker is None and not app.waiting_jobs
    session=panel.sessions['41nr.com']
    future=panel.pool.submit(session.request,'navigate',page='background',url=prefix+'/book/')
    pump_until(lambda:future.done() and ('41nr.com','background') in panel.page_info)
    future.result()
    assert '403 Forbidden' in panel.status.get() and panel.address.get()==prefix+'/chapter/836'
    deadline=time.monotonic()+.7
    while time.monotonic()<deadline:root.update();time.sleep(.02)
    future=panel.pool.submit(session.request,'snapshot',page='inspection')
    pump_until(future.done)
    (home/'browser-content.png').write_bytes(base64.b64decode(future.result()))
    capture('browser.png')
    panel.hide();root.update();capture('returned-queue.png')
    assert app.table.item(item,'values')[1:3]==('访问被拒绝','1049/1929')
    assert not app.waiting_jobs and app.worker is None
    save(home/'result.json',dict(passed=True,requests=requests,visible_page=session.current_page,
         browser_status=panel.status.get(),address=panel.address.get(),download_started=False,source_data_written=False,
         screenshots=['queue.png','queue-minimum.png','browser.png','browser-content.png','returned-queue.png']))
finally:
    panel.close();pump_until(lambda:panel.closed.is_set(),15)
    for timer in root.tk.call('after','info'):root.after_cancel(timer)
    root.destroy();server.shutdown();server.server_close()
print(json.dumps(json.loads((home/'result.json').read_text(encoding='utf-8')),ensure_ascii=True),flush=True)

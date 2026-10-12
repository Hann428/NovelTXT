"""Render cooldown feedback in the existing Tk UI without starting downloads."""
import argparse
import ctypes
import json
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.gui import App
from novel_app.storage import save

parser=argparse.ArgumentParser();parser.add_argument('--capture-python',required=True);args=parser.parse_args()
home=ROOT/'evidence/fix8-ui-preview'
save(home/'data/preferences.json',{'output':str(home/'downloads')})
save(home/'data/queue.json',[
    dict(title='都市奇缘',status='可继续（缓存）',progress='1169/1929',url='https://m.41nr.com/book/28425/'),
    dict(title='男欢女爱',status='可继续（缓存）',progress='39/560',url='https://m.41nr.com/book/20091/')])
root=tk.Tk();app=App(root,home);root.geometry('1100x760+20+20');root.update()
item=app.table.get_children()[0];app.table.selection_set(item);app.busy(True)
app.events.put(dict(kind='active',item=item,url='https://m.41nr.com/book/28425/',message='开始处理'))
app.events.put(dict(kind='job_status',item=item,status='访问冷却',message='41nr.com：已识别缓存403，等待60秒后重读1/2；同站暂停，其他站点继续'))
app.events.put(dict(kind='wait',item=item,message='41nr.com：缓存403冷却，剩余54秒；重读1/2'))
def pump():
    until=time.monotonic()+5
    root.update()
    while not app.events.empty() or app._display_events:
        if time.monotonic()>until:raise RuntimeError('UI event queue did not drain')
        root.update();time.sleep(.02)
def capture(name):
    root.update_idletasks();window=ctypes.windll.user32.GetAncestor(root.winfo_id(),2)
    code='from PIL import ImageGrab;import sys;ImageGrab.grab(window=int(sys.argv[1])).save(sys.argv[2])'
    subprocess.run([args.capture_python,'-c',code,str(window),str(home/name)],check=True,timeout=10,capture_output=True)
try:
    pump();capture('cooldown.png')
    assert app.table.item(item,'values')[1:3]==('访问冷却','1169/1929')
    assert '54秒' in app.status.get() and not app.waiting_jobs
    assert str(app.stop_button['state'])=='normal'
    root.geometry('940x760');pump();capture('cooldown-minimum.png')
    assert app.table.winfo_height()>=180
    app.stop_button.invoke();assert app.control.cancel.is_set()
    save(home/'result.json',dict(passed=True,rendered_sizes=['1100x760','940x760'],
         progress_preserved=True,countdown_visible=True,stop_button_works=True,
         download_started=False,screenshots=['cooldown.png','cooldown-minimum.png']))
finally:
    app.browser_panel.close()
    for timer in root.tk.call('after','info'):root.after_cancel(timer)
    root.destroy()
print(json.dumps(json.loads((home/'result.json').read_text('utf-8')),ensure_ascii=True))

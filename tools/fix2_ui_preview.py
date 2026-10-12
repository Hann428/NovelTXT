"""Render the resumed count and clear errors in an isolated copy of UI metadata."""
import sys,time,json,subprocess,argparse,ctypes
from ctypes import wintypes
import tkinter as tk
from pathlib import Path
parser=argparse.ArgumentParser(); parser.add_argument('--capture-python',required=True); args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.gui import App
from novel_app.storage import load,save,digest
home=ROOT/'evidence/fix2-ui-preview'
jobs=load(ROOT/'data/queue.json',[])
save(home/'data/queue.json',jobs)
save(home/'data/preferences.json',{**load(ROOT/'data/preferences.json',{}),'output':str(home/'downloads')})
for job in jobs:
    state=load(ROOT/'下载/.novel-cache'/digest(job['url'])[:16]/'manifest.json',{})
    if state: save(home/'downloads/.novel-cache'/digest(job['url'])[:16]/'manifest.json',state)
root=tk.Tk(); app=App(root,home); root.geometry('1100x760+20+20'); root.update()
items=app.table.get_children(); values=[list(app.table.item(i,'values')) for i in items]
assert values[0][0]=='都市奇缘' and values[0][2]=='642/1929', values
assert values[1][0]=='男欢女爱', values
app.events.put(dict(kind='job_status',item=items[0],status='需处理',message='示例：缺失分页，已下载章节保留，请查看报告'))
app.events.put(dict(kind='idle',message='本轮处理结束'))
def capture():
    try:
        root.update_idletasks()
        assert app.status.get().startswith('本轮未完成：')
        assert app.table.winfo_height()>=180
        app.start_button.focus_set(); root.update()
        bounds=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())
        first=wintypes.POINT(bounds[0],bounds[1]); last=wintypes.POINT(bounds[2],bounds[3])
        convert=ctypes.windll.user32.LogicalToPhysicalPointForPerMonitorDPI
        convert.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
        assert convert(root.winfo_id(),ctypes.byref(first)) and convert(root.winfo_id(),ctypes.byref(last))
        bounds=(first.x,first.y,last.x,last.y)
        script='from PIL import ImageGrab;import sys;ImageGrab.grab(bbox=tuple(map(int,sys.argv[1:5]))).save(sys.argv[5])'
        subprocess.run([args.capture_python,'-c',script,*map(str,bounds),str(home/'preview.png')],check=True,timeout=10,capture_output=True)
        save(home/'result.json',{'passed':True,'rows':values,'status':app.status.get(),'summary':app.summary.get(),
             'list_height':app.table.winfo_height(),'screenshot':str(home/'preview.png'),'source_cache_written':False})
    finally:
        app.browser_panel.close()
        root.destroy()
root.after(900,capture); root.mainloop()
print(json.dumps(load(home/'result.json',{}),ensure_ascii=True),flush=True)

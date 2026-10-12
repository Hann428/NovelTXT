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
home=ROOT/'evidence/fix5-ui-preview'
jobs=load(ROOT/'data/queue.json',[])
save(home/'data/queue.json',jobs)
save(home/'data/preferences.json',{**load(ROOT/'data/preferences.json',{}),'output':str(home/'downloads')})
for job in jobs:
    state=load(ROOT/'下载/.novel-cache'/digest(job['url'])[:16]/'manifest.json',{})
    if state: save(home/'downloads/.novel-cache'/digest(job['url'])[:16]/'manifest.json',state)
root=tk.Tk(); app=App(root,home); root.geometry('1100x760+20+20'); root.update()
items=app.table.get_children(); values=[list(app.table.item(i,'values')) for i in items]
assert values[0][0]=='都市奇缘' and int(values[0][2].split('/')[0])>=702, values
assert values[1][0]=='男欢女爱', values
app.busy(True)
app.row_progress[items[0]]=tuple(map(int,values[0][2].split('/')))
app.table.selection_set(items[0])
app.events.put(dict(kind='job_status',item=items[0],status='持续限流',message='HTTP 429：持续限流，已暂停本站；1049 章缓存保留，请稍后继续'))
app.events.put(dict(kind='job_status',item=items[1],status='服务器暂不可用',message='HTTP 503：服务器要求冷却；39 章缓存保留，请稍后继续'))
app.events.put(dict(kind='idle',message='本轮处理结束'))
def capture():
    try:
        root.update_idletasks()
        assert '本轮未完成 2 本' in app.summary.get()
        assert app.table.item(items[0],'values')[1]=='持续限流'
        assert 'HTTP 429' in app.detail.get() and app.status.get().startswith('本轮未完成：')
        assert not app.waiting_jobs
        assert app.table.winfo_height()>=180
        app.start_button.focus_set(); root.update()
        bounds=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())
        first=wintypes.POINT(bounds[0],bounds[1]); last=wintypes.POINT(bounds[2],bounds[3])
        convert=ctypes.windll.user32.LogicalToPhysicalPointForPerMonitorDPI
        convert.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
        assert convert(root.winfo_id(),ctypes.byref(first)) and convert(root.winfo_id(),ctypes.byref(last))
        bounds=(first.x,first.y,last.x,last.y)
        # Capture only this test window, including when another app covers it.
        script='from PIL import ImageGrab;import sys;ImageGrab.grab(window=int(sys.argv[1])).save(sys.argv[2])'
        top=ctypes.windll.user32.GetAncestor(root.winfo_id(),2)
        captured=subprocess.run([args.capture_python,'-c',script,str(top),str(home/'preview.png')],timeout=10,capture_output=True)
        if captured.returncode:
            raise RuntimeError(captured.stderr.decode('utf-8', errors='replace'))
        save(home/'result.json',{'passed':True,'rows':[list(app.table.item(i,'values')) for i in items],'status':app.status.get(),'summary':app.summary.get(),
             'list_height':app.table.winfo_height(),'screenshot':str(home/'preview.png'),'source_cache_written':False,
             'icon_bitmap':root.iconbitmap()})
    finally:
        app.browser_panel.close()
        for timer in root.tk.call('after','info'): root.after_cancel(timer)
        root.destroy()
root.after(900,capture); root.mainloop()
result=load(home/'result.json',{});result['rows']=result['rows'][:2];print(json.dumps(result,ensure_ascii=True),flush=True)

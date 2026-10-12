"""Isolated layout checks: live manifests may keep advancing during this test."""
import json
import sys
import tkinter as tk
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.gui import App
from novel_app.storage import load,save
jobs=load(ROOT/'data/queue.json',[]); prefs=load(ROOT/'data/preferences.json',{})
cases=[]
for scale in (1,1.25,1.5,2):
    for width,height in ((940,760),(1100,760),(1100,880)):
        home=ROOT/'evidence/webview2-layout'/uuid4().hex
        save(home/'data/queue.json',jobs); save(home/'data/preferences.json',{**prefs,'output':str(home/'downloads')})
        root=tk.Tk(); root.withdraw(); root.attributes('-alpha',0); root.tk.call('tk','scaling',scale)
        try:
            app=App(root,home); root.geometry(f'{width}x{height}'); root.deiconify(); root.update()
            assert len(app.table.get_children())==len(jobs)
            list_height=app.table.winfo_height(); assert list_height>=180
            for item in app.table.get_children()[:2]:
                box=app.table.bbox(item); assert box and box[1]+box[3]<=list_height
            def inside(widget):
                x=widget.winfo_rootx()-root.winfo_rootx(); y=widget.winfo_rooty()-root.winfo_rooty()
                return x>=0 and y>=0 and x+widget.winfo_width()<=root.winfo_width() and y+widget.winfo_height()<=root.winfo_height()
            for button in (app.start_button,app.auth_button,app.stop_button,app.task_pause_button,app.task_stop_button,app.delete_button):
                assert inside(button) and button.winfo_width()>=button.winfo_reqwidth(), (scale,width,height,button['text'])
            app.browser_panel.show(); root.update(); assert inside(app.browser_panel.panel)
            app.browser_panel.hide(); root.update(); assert app.table.winfo_height()==list_height
            app.status.set('很长的进度说明'*100); root.update(); assert app.table.winfo_height()==list_height
            cases.append({'scale':scale,'width':width,'height':height,'list_height':list_height,'restored_books':len(jobs)})
        finally:
            app.browser_panel.close()
            for timer in root.tk.call('after','info'): root.after_cancel(timer)
            root.destroy()
save(ROOT/'evidence/webview2-layout-result.json',{'passed':True,'cases':cases,'live_data_written':False})
print(json.dumps({'passed':True,'cases':cases}),flush=True)

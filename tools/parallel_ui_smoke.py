"""Map transparent windows and exercise interleaved progress/control events."""
import json
import sys
import time
import tkinter as tk
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.gui import App
from novel_app.storage import save

root=tk.Tk();root.withdraw();root.attributes('-alpha',0)
root.tk.call('tk','scaling',1.5)
home=ROOT/'evidence/parallel-ui'/uuid4().hex
app=App(root,home)
root.deiconify();root.update()
root.geometry('1100x760');root.update()
for button in (app.start_button,app.pause_button,app.stop_button,app.task_pause_button,app.task_stop_button,app.delete_button):
    assert button.winfo_viewable()
    x=button.winfo_rootx()-root.winfo_rootx();y=button.winfo_rooty()-root.winfo_rooty()
    assert x>=0 and y>=0 and x+button.winfo_width()<=root.winfo_width()
    assert y+button.winfo_height()<=root.winfo_height()

items=[app.table.insert('','end',values=('同名小说','待下载','0/1000',f'https://example.org/book/{i}')) for i in range(2)]
root.update()
assert app.table.winfo_height() >= 180
for item in items:
    box=app.table.bbox(item)
    assert box and box[1]+box[3] <= app.table.winfo_height()
for item in items:app.events.put(dict(kind='active',item=item,message='并行界面测试'))
for index in range(1,1001):
    for item in items:
        app.events.put(dict(kind='chapter',item=item,title='同名小说',current=index,downloaded=index,total=1000))
started=time.monotonic();last=started;gaps=[];errors=[]
def finish():
    for item in items:assert app.table.item(item,'values')[2]=='1000/1000'
    app.waiting_jobs={item:{'url':f'https://example.org/{item}'} for item in items}
    app.waiting_jobs.pop(items[0]);app.verification_buttons()
    assert app.waiting_manual and str(app.auth_button['state'])=='normal'
    app.table.selection_set(items[1]);assert app.verification_items()==[items[1]]
    calls=[]
    app.batch=type('Batch',(),{'command':lambda self,selected,action:calls.append((tuple(selected),action))})()
    app.task_command('pause');app.task_command('stop')
    assert calls==[((items[1],),'pause'),((items[1],),'stop')]
    app.persist()
    result=dict(passed=True,interleaved_same_title_rows_correct=True,remaining_verification_kept=True,
                selected_controls_correct=True,main_action_buttons_visible=True,list_rows_visible=True,
                events=2002,elapsed_seconds=round(time.monotonic()-started,3),max_ui_gap_seconds=round(max(gaps),3))
    save(ROOT/'evidence/parallel-ui-result.json',result)
    print(json.dumps(result),flush=True)
def heartbeat():
    global last
    try:
        now=time.monotonic();gaps.append(now-last);last=now
        if app.events.empty() and not app._display_events:
            finish();root.destroy();return
        if now-started>30:raise RuntimeError('UI test timed out')
        root.after(10,heartbeat)
    except Exception as exc:
        errors.append(exc);root.destroy()
root.after(10,heartbeat);root.mainloop()
if errors:raise errors[0]

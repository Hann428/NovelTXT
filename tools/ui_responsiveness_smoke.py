"""Exercise a cache-progress burst in a withdrawn Tk window, without networking."""
import json
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.gui import App
from novel_app.storage import save

root=tk.Tk()
root.withdraw()
app=App(root,ROOT/'evidence/ui-responsiveness')
for callback in root.tk.splitlist(root.tk.call('after','info')):
    root.after_cancel(callback)
item=app.table.insert('','end',values=('测试书','待下载','0/2000','https://example.org/book/1'))
app.events.put(dict(kind='active',item=item,message='开始压力测试'))
for i in range(1,2001):
    app.events.put(dict(kind='chapter',message=f'缓存 {i}/2000',title='测试书',current=i,downloaded=i,total=2000))
started=last=time.monotonic()
gaps=[]
app.poll()
assert app.events.qsize()>0,'A single callback drained the whole queue and starved Tk'
def heartbeat():
    global last
    now=time.monotonic()
    gaps.append(now-last)
    last=now
    if app.events.empty() and not app._display_events:
        result={'events':2001,'elapsed_seconds':round(now-started,3),'heartbeat_count':len(gaps),
                'max_ui_gap_seconds':round(max(gaps),3),'final_progress':app.table.item(item,'values')[2]}
        result['passed']=max(gaps)<.5 and result['final_progress']=='2000/2000' and len(gaps)>5
        save(ROOT/'evidence/ui-responsiveness-result.json',result)
        print(json.dumps(result),flush=True)
        root.destroy()
    elif now-started>30:
        print('FAILED: UI queue did not drain within 30 seconds',flush=True)
        root.destroy()
    else:
        root.after(10,heartbeat)
root.after(10,heartbeat)
root.mainloop()

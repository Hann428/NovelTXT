"""Exercise delete preview, cancellation and confirmation on synthetic files."""
import sys,time,json,tkinter as tk
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.gui import App
from novel_app.storage import save,digest
from novel_app import gui

home=ROOT/'evidence/deletion-ui-smoke'/uuid4().hex
output=home/'下载'
url='https://example.org/book/delete-test'
cache=output/'.novel-cache'/digest(url)[:16]
save(cache/'manifest.json',dict(input_url=url,title='删除按钮测试',downloaded=1,total=1,status='complete'))
save(cache/'chapters/1.json',dict(text='合成测试内容'))
(output/'test.txt').write_text('合成测试内容',encoding='utf-8')
save(output/'test.report.json',dict(input_url=url))
for index in range(30):
    (output/f'历史导出-{index:02}.txt').write_text('合成测试内容',encoding='utf-8')
    save(output/f'历史导出-{index:02}.report.json',dict(input_url=url))
(output/'keep.txt').write_text('必须保留',encoding='utf-8')
root=tk.Tk();root.withdraw();root.attributes('-alpha',0)
scaling=float(sys.argv[1]) if len(sys.argv)>1 else 1.5
root.tk.call('tk','scaling',scaling)
# Map transparent test windows so Tk computes real button visibility without
# putting a test dialog over the user's work.
original_toplevel=tk.Toplevel
def transparent_toplevel(*args,**kwargs):
    win=original_toplevel(*args,**kwargs)
    win.attributes('-alpha',0)
    return win
gui.tk.Toplevel=transparent_toplevel
app=App(root,home)
root.deiconify()
item=app.table.get_children()[0]
app.table.selection_set(item)
stage=0
started=time.monotonic()
layout_checks=[]
errors=[]

def check_layout(button):
    win=button.winfo_toplevel()
    for width,height in [(800,470),(560,300)]:
        win.geometry(f'{width}x{height}')
        win.update()
        assert button.winfo_viewable(), 'Confirmation button is not visible'
        x=button.winfo_rootx()-win.winfo_rootx()
        y=button.winfo_rooty()-win.winfo_rooty()
        assert 0<=x and x+button.winfo_width()<=win.winfo_width()
        assert 0<=y and y+button.winfo_height()<=win.winfo_height(), 'Button clipped below dialog'
        assert button.winfo_height()>=button.winfo_reqheight()
        box=next(w for w in descendants(win) if w.winfo_class()=='Text')
        assert box.yview()[1]<1, 'Long deletion list should scroll'
        layout_checks.append(dict(width=width,height=height,button_y=y,button_height=button.winfo_height()))
def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)
def step():
    global stage
    if time.monotonic()-started>15:
        raise RuntimeError('Delete UI test timed out')
    buttons=[w for w in descendants(root) if w.winfo_class()=='TButton']
    if stage==0:
        app.delete_button.invoke();stage=1
    elif stage==1:
        cancel=next((w for w in buttons if w.cget('text')=='取消'),None)
        if cancel:
            check_layout(cancel)
            cancel.invoke()
            assert (output/'test.txt').exists() and cache.exists()
            stage=2
    elif stage==2:
        app.delete_button.invoke();stage=3
    elif stage==3:
        confirm=next((w for w in buttons if w.cget('text')=='确认删除'),None)
        if confirm:
            check_layout(confirm)
            confirm.invoke();stage=4
    elif stage==4 and not (app.worker and app.worker.is_alive()) and not app.table.exists(item):
        assert not (output/'test.txt').exists() and not cache.exists()
        assert (output/'keep.txt').exists()
        assert list(output.glob('*.txt'))==[output/'keep.txt']
        result=dict(passed=True,cancel_keeps_files=True,confirm_deletes_cache_and_exports=True,
                    unrelated_file_kept=True,row_removed=True,scaling=scaling,layout_checks=layout_checks)
        save(home/'result.json',result)
        save(ROOT/f'evidence/deletion-ui-smoke-{scaling}-result.json',result)
        print(json.dumps(result),flush=True)
        root.destroy();return
    root.after(80,safe_step)
def safe_step():
    try:
        step()
    except Exception as exc:
        errors.append(exc)
        root.destroy()
root.after(50,safe_step)
root.mainloop()
if errors:
    raise errors[0]

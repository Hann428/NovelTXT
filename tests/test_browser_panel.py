"""Exercise the actual Tk event routing without running a user's browser."""
from pathlib import Path
import subprocess
import sys


def test_visible_page_status_ignores_background_navigation_and_inspection_is_separate(tmp_path):
    script = r'''
import sys, time, tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import Future
root_path=Path(sys.argv[1]); sys.path[:0]=[str(root_path),str(root_path/'vendor/Novel-Get/src')]
import novel_app.browser_panel as module
root=tk.Tk(); root.withdraw()
panel=module.BrowserPanel(root,Path(sys.argv[2]),tk.StringVar(value='内置浏览器'))
commands=[]
class Session:
    closed=False; current_page='main'; current_url='https://a.example/book/'
    def request(self,op,**args):
        commands.append((op,args))
        if op=='show': panel.events.put(('event','a.example',dict(kind='page_selected',page=args['page'],url=self.current_url)))
        if op=='navigate': panel.events.put(('event','a.example',dict(kind='navigation',page=args['page'],url=args['url'],title='403 Forbidden',code=403)))
        return True
    def close(self): pass
module.NativeSession=lambda *a,**kw:Session()
def event(site,page,title,url,code=200):
    panel.events.put(('event',site,dict(kind='navigation',page=page,title=title,url=url,code=code))); panel.poll()
try:
    for site in ('a.example','b.example'):
        frame=module.ttk.Frame(panel.tabs); panel.tabs.add(frame,text=site); panel.frames[site]=frame
        panel.sessions[site]=Session()
    panel.tabs.select(panel.frames['a.example']); root.update(); panel.selected()
    event('a.example','main','目录','https://a.example/book/')
    initial=panel.status.get()
    event('a.example','page-1','后台第836章','https://a.example/chapter/836')
    event('b.example','main','其他站目录','https://b.example/book/')
    assert panel.status.get()==initial and panel.address.get()=='https://a.example/book/'
    panel.tabs.select(panel.frames['b.example']); root.update(); panel.selected()
    assert '其他站目录' in panel.status.get()
    panel.tabs.select(panel.frames['a.example']); root.update(); panel.selected()
    panel.open_url('a.example','https://a.example/chapter/836')
    deadline=time.monotonic()+3
    while panel.inspections and time.monotonic()<deadline:
        root.update(); time.sleep(.01)
    assert not panel.inspections
    assert panel.address.get()=='https://a.example/chapter/836'
    assert '403 Forbidden' in panel.status.get() and 'HTTP 403' in panel.status.get()
    assert '不会自动继续' in panel.status.get()
    assert [args['page'] for op,args in commands if op=='navigate']==['inspection']
    event('a.example','main','目录更新','https://a.example/book/')
    assert '403 Forbidden' in panel.status.get()
    panel.events.put(('event','a.example',dict(kind='page_selected',page='main',url='https://a.example/book/')));panel.poll()
    assert '目录更新' in panel.status.get() and panel.address.get()=='https://a.example/book/'
finally:
    panel.close()
    for timer in root.tk.call('after','info'):root.after_cancel(timer)
    root.destroy()
'''
    result=subprocess.run([sys.executable,'-c',script,str(Path(__file__).resolve().parents[1]),str(tmp_path)],
                          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
    assert result.returncode == 0,result.stdout+result.stderr


def test_failure_target_survives_restart_and_does_not_start_download(tmp_path):
    script = r'''
import sys, tkinter as tk
from pathlib import Path
root_path=Path(sys.argv[1]);sys.path[:0]=[str(root_path),str(root_path/'vendor/Novel-Get/src')]
from novel_app.gui import App
from novel_app.storage import save,digest
home=Path(sys.argv[2]);url='https://m.41nr.com/book/28425/'; failed='https://m.41nr.com/chapter/28425/5828743.html'
save(home/'data/queue.json',[dict(title='都市奇缘',status='可继续',progress='1049/1929',url=url)])
save(home/'下载/.novel-cache'/digest(url)[:16]/'manifest.json',dict(input_url=url,title='都市奇缘',status='blocked',downloaded=1049,total=1929,error='HTTP 403：拒绝访问',error_url=failed,error_status=403,error_chapter_title='第836章'))
root=tk.Tk();root.withdraw();app=App(root,home)
def drain():
    while not app.events.empty() or app._display_events:app.poll()
try:
    item=app.table.get_children()[0];app.table.selection_set(item);app.show_selected_error()
    assert app.table.item(item,'values')[1:3]==('访问被拒绝','1049/1929')
    assert str(app.failure_button['state'])=='normal'
    opened=[];app.browser_panel.open_url=lambda site,url:opened.append((site,url))
    app.open_failure_page()
    assert opened==[('41nr.com',failed)] and app.worker is None
    app.last_warnings[item]='此前404：另一个章节'
    app.events.put(dict(kind='job_status',item=item,status='访问被拒绝',message='HTTP 403：当前章节',error_url=failed,error_status=403,error_chapter_title='第836章'));drain()
    assert '此前404' not in app.detail.get()
    app.persist()
    app.remember_failure(item,dict(error_url='http://127.0.0.1/'))
    app.remember_failure(item,dict(error_url='https://different.example/a'))
    assert app.error_pages[item]['error_url']==failed
    app.events.put(dict(kind='active',item=item,url=url));drain()
    assert item not in app.error_pages and str(app.failure_button['state'])=='disabled'
finally:
    app.browser_panel.close()
    for timer in root.tk.call('after','info'):root.after_cancel(timer)
    root.destroy()
root=tk.Tk();root.withdraw();app=App(root,home)
try:
    item=app.table.get_children()[0]
    assert app.error_pages[item]['error_url']==failed
    assert app.batch_errors[item].count('第836章：')==1
finally:
    app.browser_panel.close()
    for timer in root.tk.call('after','info'):root.after_cancel(timer)
    root.destroy()
'''
    result=subprocess.run([sys.executable,'-c',script,str(Path(__file__).resolve().parents[1]),str(tmp_path)],
                          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
    assert result.returncode==0,result.stdout+result.stderr

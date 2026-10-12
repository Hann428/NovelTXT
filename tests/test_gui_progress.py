"""Keep Tk ownership and destruction in one isolated main thread/process."""
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize('status',['需处理','访问被拒绝','持续限流','服务器暂不可用','权限或订阅限制','代理需要认证','需要登录'])
def test_directory_stage_and_error_stay_visible_while_next_book_runs(tmp_path,status):
    script = r'''
import sys,tkinter as tk
from pathlib import Path
root_path=Path(sys.argv[1])
sys.path[:0]=[str(root_path),str(root_path/'vendor/Novel-Get/src')]
from novel_app.gui import App
root=tk.Tk(); root.withdraw(); app=App(root,Path(sys.argv[2]))
def drain():
    while not app.events.empty() or app._display_events: app.poll()
try:
    first=app.table.insert('', 'end', values=('都市奇缘','下载中','702/1929','https://m.41nr.com/book/28425/'))
    second=app.table.insert('', 'end', values=('男欢女爱','排队中','0/100','https://m.41nr.com/book/20091/'))
    app.row_progress[first]=(702,1929)
    app.table.selection_set(first)
    app.events.put(dict(kind='job_status',item=first,status=sys.argv[3],message='缺失分页，缓存保留'))
    app.events.put(dict(kind='active',item=second,url='https://m.41nr.com/book/20091/'))
    app.events.put(dict(kind='toc',item=second,toc_pages=16,total=320,title='男欢女爱'))
    app.events.put(dict(kind='request',item=second,url='https://m.41nr.com/book/20091/index_17.html'))
    drain()
    assert app.table.item(second,'values')[1:3] == ('目录第 16 页','发现 320 章')
    assert app.row_progress[first] == (702,1929)
    assert app.table.item(first,'values')[1]==sys.argv[3] and not app.waiting_jobs
    assert '需处理 1 本' in app.summary.get()
    assert app.detail.get() == '都市奇缘：缺失分页，缓存保留'
    app.table.selection_remove(first)
    app.events.put(dict(kind='request',item=second,url='https://m.41nr.com/chapter/20091/1.html'))
    app.events.put(dict(kind='chapter',item=second,downloaded=1,total=560,title='男欢女爱'))
    drain()
    assert app.detail.get() == 'https://m.41nr.com/chapter/20091/1.html'
    assert app.table.item(second,'values')[1:3] == ('下载中','1/560')
    app.events.put(dict(kind='idle',message='本轮处理结束'))
    drain()
    assert app.status.get().startswith('本轮未完成：') and not app.waiting_jobs
finally:
    app.browser_panel.close()
    for timer in root.tk.call('after','info'): root.after_cancel(timer)
    root.destroy()
'''
    result=subprocess.run([sys.executable,'-c',script,str(Path(__file__).resolve().parents[1]),str(tmp_path),status],
                          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

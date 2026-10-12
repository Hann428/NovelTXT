"""Local controls for the two user-requested acceptance downloads."""
import os
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from .storage import JobLock, digest, load, write_text


def jobs(home):
    home = Path(home)
    specs = [
        ('41nr', 'https://m.41nr.com/book/28425/', '下载', 'data/browser-profile', 'full-41nr-recovery', '--browser-minimized', '3'),
        ('搬山人', 'https://www.banshanren.com/novel/cunsemanyuan', 'evidence/live-download', 'evidence/banshanren-gui/data/browser-profile', 'full-banshanren-background', '--browser-headless', '3'),
    ]
    return [dict(name=name, url=url, output=home/output, profile=home/profile, key=key, mode=mode, delay=delay,
                 folder=home/output/'.novel-cache'/digest(url)[:16], stop=home/'logs'/('stop-'+key))
            for name,url,output,profile,key,mode,delay in specs]


def running(job):
    try:
        with JobLock(job['folder']):
            return False
    except RuntimeError:
        return True


def request_stop(job):
    if running(job):
        write_text(job['stop'], 'Stop requested by the user.\n')
        return True
    return False


def resume_job(home, job):
    if running(job):
        return '正在运行，不重复启动'
    state = load(job['folder']/'manifest.json', {})
    if state.get('status') == 'complete':
        return '下载已完成，等待最终验收'
    home = Path(home)
    executable = home/'.venv/Scripts/python.exe'
    if not executable.exists():
        raise RuntimeError('未找到项目运行环境，请保留项目中的 .venv 文件夹')
    job['stop'].unlink(missing_ok=True)
    logs = home/'logs'
    logs.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    base = logs/(job['key']+'-'+stamp)
    args = [str(executable), '-u', str(home/'launcher.py'), '--cli', job['url'],
            '--output', str(job['output']), '--delay', job['delay'], job['mode'],
            '--profile', str(job['profile']), '--stop-file', str(job['stop'])]
    with base.with_suffix('.log').open('w', encoding='utf-8') as out, base.with_suffix('.error.log').open('w', encoding='utf-8') as err:
        process = subprocess.Popen(args, cwd=home, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    write_text(logs/(job['key']+'.pid'), str(process.pid))
    return f'已启动 PID {process.pid}；日志 {base.name}.log'


def main(home, action='status'):
    root = tk.Tk()
    root.title('两本全量测试 · 停止与续传')
    root.geometry('790x360')
    root.minsize(700, 320)
    root.option_add('*Font', ('Microsoft YaHei UI', 10))
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='关机前停止并保存；下次开机继续，已有页面不会重新下载。').pack(anchor='w')
    rows = []
    for job in jobs(home):
        label = ttk.Label(frame, text=job['name'])
        label.pack(anchor='w', pady=(15, 4))
        bar = ttk.Progressbar(frame, maximum=100)
        bar.pack(fill='x')
        rows.append((job, label, bar))
    status = tk.StringVar(value='正在读取实际任务状态')
    ttk.Label(frame, textvariable=status, wraplength=730).pack(anchor='w', pady=15)
    controls = ttk.Frame(frame)
    controls.pack(fill='x')
    stopping = False
    starting_until = 0

    def stop_all():
        nonlocal stopping
        stopping = True
        try:
            for job, _, _ in rows:
                request_stop(job)
            status.set('正在停止；等待当前网页处理结束。显示“可以关机”后再关机。')
        except Exception as exc:
            status.set(f'停止请求失败：{exc}')

    def resume_all():
        nonlocal stopping, starting_until
        import time
        stopping = False
        starting_until = time.monotonic()+8
        messages = []
        for job, _, _ in rows:
            try:
                messages.append(job['name']+'：'+resume_job(home,job))
            except Exception as exc:
                messages.append(job['name']+'：'+str(exc))
        status.set('\n'.join(messages))

    ttk.Button(controls, text='停止两本并保存', command=stop_all).pack(side='left')
    resume = ttk.Button(controls, text='继续两本测试', command=resume_all)
    resume.pack(side='left', padx=10)
    ttk.Button(controls, text='关闭此窗口', command=root.destroy).pack(side='right')
    ttk.Label(frame, text='关闭此控制窗口不会停止后台下载。41nr 的浏览器请最小化保留。').pack(anchor='w', pady=12)

    def poll():
        import time
        active = False
        try:
            for job, label, bar in rows:
                state = load(job['folder']/'manifest.json', {})
                busy = running(job)
                active |= busy
                count,total = state.get('downloaded',0),state.get('total',0)
                label.configure(text=f"{job['name']}：{count}/{total or '未知'} 章 · {'任务运行中（可能限速或等待验证）' if busy else '已停止 / 可续传'}")
                bar['value'] = count/max(1,total)*100
            resume.configure(state='disabled' if (stopping and active) or time.monotonic()<starting_until else 'normal')
            if stopping and not active:
                status.set('两项任务均已停止，进度已保留，现在可以关机。下次双击“继续两本测试”。')
        except Exception as exc:
            status.set(f'无法核实任务状态，请先不要关机：{exc}')
        root.after(1000,poll)
    poll()
    if action == 'stop':
        root.after(200,stop_all)
    elif action == 'resume':
        root.after(200,resume_all)
    root.mainloop()

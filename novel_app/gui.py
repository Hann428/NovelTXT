import json
import os
import queue
import re
import threading
import time
import sys
from collections import deque
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
from urllib.parse import urlsplit

from novel_get.fetch import SiteBlocked, DownloadError

from .core import Runner
from .network import Control, FetcherPool, Cancelled, validate_url, StopWithoutVerification, blocked_status
from .sites import list_books, clean_book_identity
from .storage import load, save
from .batch import parse_urls, SPEEDS
from .deletion import plan_delete, execute_delete

BG, PANEL, FG, MUTED, ACCENT = "#101820", "#17232e", "#e5edf5", "#9baebe", "#38c9a0"


def session_site(host):
    if host in {'41nr.com', 'm.41nr.com', 'www.41nr.com'}:
        return '41nr.com'
    if host in {'banshanren.com', 'www.banshanren.com'}:
        return 'banshanren.com'
    return host or 'unknown'


class App:
    def __init__(self, root, home):
        self.root, self.home = root, Path(home)
        self.data = self.home / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.logs = self.home / "logs"
        self.logs.mkdir(exist_ok=True)
        self.logfile = self.logs / ("session-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".jsonl")
        self.events = queue.Queue()
        self._display_events = deque()
        self.control = Control(lambda **data: self.events.put(data))
        self.worker = None
        self.batch = None
        self.waiting_jobs = {}
        self.running_items = set()
        self.row_progress = {}
        self.batch_errors = {}
        self.error_pages = {}
        self.last_warnings = {}
        self.active = None
        self.waiting_manual = False
        self.closing = False
        self._persist_pending = False
        self._last_persist = 0
        self.settings = load(self.data / "preferences.json", {}) or {}
        self.out = tk.StringVar(value=self.settings.get("output", str(self.home / "下载")))
        self.mode = tk.StringVar(value=self.settings.get("mode", "自动选择"))
        self.delay = tk.StringVar(value=str(self.settings.get("delay", 3)))
        self.speed = tk.StringVar(value=self.settings.get("speed", "均衡"))
        if self.speed.get() not in SPEEDS:
            self.speed.set("均衡")
        self.refresh = tk.BooleanVar(value=False)
        self.proxy = tk.BooleanVar(value=False)
        self.books = tk.StringVar(value=str(self.settings.get('books', 3)))
        self.site_concurrency = tk.StringVar(value=str(self.settings.get('site_concurrency', 2)))
        self.chapter_concurrency = tk.StringVar(value=str(self.settings.get('chapter_concurrency', 3)))
        self.adaptive = tk.BooleanVar(value=self.settings.get('adaptive', True))
        self.browser_backend = tk.StringVar(value=self.settings.get('browser_backend', '内置浏览器'))
        self.summary = tk.StringVar(value='同站每次 1 本；书内章节并行；共享登录与验证')
        self.status = tk.StringVar(value="就绪 · 批量粘贴目录链接，每本保存为一个 TXT")
        self.detail = tk.StringVar(value="段落与空行保留  /  逐章缓存  /  完整性报告")
        self.build()
        from .browser_panel import BrowserPanel
        self.browser_panel = BrowserPanel(root, self.home, self.browser_backend)
        old = load(self.data / "queue.json", []) or []
        for job in old:
            if isinstance(job, dict) and isinstance(job.get("url"), str):
                status = job.get("status", "待下载")
                if status in {"下载中", "解析中", "等待操作", "已暂停"} or status.startswith(('目录第', '正文第')):
                    status = "可继续"
                title, _ = clean_book_identity(job.get('title', '待解析'), url=job['url'])
                item = self.table.insert("", "end", values=(title, status, job.get("progress", "—"), job["url"]))
                self.remember_failure(item, job)
        self.restore_cache_progress()
        self.show_selected_error()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(120, self.poll)

    def build(self):
        r = self.root
        r.title("逐页 · 小说 TXT 下载器")
        # One application group and icon for the executable, window and taskbar.
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Han.NovelTXT.PageTurn')
        assets = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'assets'
        if (assets / 'book-turn.png').exists():
            self._app_icon = tk.PhotoImage(file=str(assets / 'book-turn.png'))
            r.iconphoto(True, self._app_icon)
        if sys.platform == 'win32' and (assets / 'book-turn.ico').exists():
            r.iconbitmap(default=str(assets / 'book-turn.ico'))
        height = min(880, r.winfo_screenheight() - 100)
        r.geometry(f"1100x{height}")
        r.minsize(940, min(760, height))
        r.configure(bg=BG)
        r.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(r)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("TButton", background="#263b4a", foreground=FG, padding=(12, 7), borderwidth=0)
        style.map("TButton", background=[("active", "#365465"), ("disabled", "#20313d")])
        style.configure("Primary.TButton", background=ACCENT, foreground="#0b2921", font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#69dabc")])
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TEntry", fieldbackground=PANEL, foreground=FG, padding=5)
        style.configure("TCombobox", fieldbackground=PANEL, foreground=FG, arrowcolor=FG)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=33, borderwidth=0)
        style.configure("Treeview.Heading", background="#223441", foreground=MUTED, padding=8)
        style.map("Treeview", background=[("selected", "#285949")])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL, borderwidth=0)
        layout = ttk.Frame(r, padding=16)
        layout.pack(fill="both", expand=True)
        layout.columnconfigure(0, weight=1)
        layout.rowconfigure(1, weight=1, minsize=180)
        outer = ttk.Frame(layout)
        outer.grid(row=0, column=0, sticky="ew")
        ttk.Label(outer, text="小说目录链接 · 每行一个，也可粘贴带链接的文字").pack(anchor="w")
        self.input = tk.Text(outer, height=2, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", padx=10, pady=6, undo=True)
        self.input.pack(fill="x", pady=(7, 8))
        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Button(row, text="＋ 加入队列", command=self.add_urls).pack(side="left")
        ttk.Button(row, text="导入链接文件", command=self.import_file).pack(side="left", padx=8)
        self.list_button = ttk.Button(row, text="解析列表并选书", command=lambda: self.start("list"))
        self.list_button.pack(side="left")
        ttk.Label(row, text="列表解析只读取输入的那一页", style="Muted.TLabel").pack(side="right")
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(10, 8))
        ttk.Label(row, text="保存到").pack(side="left", padx=(0, 10))
        ttk.Entry(row, textvariable=self.out).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="选择文件夹", command=self.choose_folder).pack(side="left", padx=8)
        ttk.Button(row, text="打开目录", command=self.open_folder).pack(side="left")
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(0, 8))
        ttk.Combobox(row, textvariable=self.mode, state="readonly", width=13, values=("自动选择", "普通网页", "浏览器模式")).pack(side="left")
        ttk.Label(row, text="速度").pack(side="left", padx=(12, 4))
        speed = ttk.Combobox(row, textvariable=self.speed, state="readonly", width=5, values=tuple(SPEEDS))
        speed.pack(side="left")
        speed.bind('<<ComboboxSelected>>', lambda _: self.delay.set(str(SPEEDS[self.speed.get()][0])))
        ttk.Label(row, text="网页请求间隔（秒）").pack(side="left", padx=(18, 5))
        ttk.Combobox(row, textvariable=self.delay, width=5, values=(1, 3, 5, 10, 15, 30)).pack(side="left")
        ttk.Checkbutton(row, text="刷新目录 / 追更", variable=self.refresh).pack(side="left", padx=15)
        ttk.Checkbutton(row, text="HTTP 使用环境代理", variable=self.proxy).pack(side="left")
        row = ttk.Frame(outer)
        row.pack(fill='x',pady=(0,10))
        ttk.Label(row,text='同时下载书数').pack(side='left')
        ttk.Combobox(row,textvariable=self.books,state='readonly',width=3,values=tuple(range(1,9))).pack(side='left',padx=(5,14))
        ttk.Label(row,text='每站并发上限').pack(side='left')
        ttk.Combobox(row,textvariable=self.site_concurrency,state='readonly',width=3,values=(1,2,3,4)).pack(side='left',padx=5)
        ttk.Label(row,text='每书章节并发').pack(side='left',padx=(10,0))
        ttk.Combobox(row,textvariable=self.chapter_concurrency,state='readonly',width=3,values=(1,2,3,4)).pack(side='left',padx=5)
        ttk.Checkbutton(row,text='自动调节并发',variable=self.adaptive).pack(side='left',padx=12)
        frame = ttk.Frame(layout)
        frame.grid(row=1, column=0, sticky="nsew")
        self.table = ttk.Treeview(frame, columns=("title", "status", "progress", "url"), show="headings", selectmode="extended", height=6)
        for name, heading, width in [("title", "书名", 230), ("status", "状态", 115), ("progress", "章节", 100), ("url", "来源地址", 460)]:
            self.table.heading(name, text=heading)
            self.table.column(name, width=width, minwidth=70)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        self.table.bind('<<TreeviewSelect>>', self.show_selected_error)
        scroll.pack(side="right", fill="y")
        footer=ttk.Frame(layout)
        footer.grid(row=2,column=0,sticky='ew')
        outer=footer
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=8)
        self.start_button = ttk.Button(row, text="开始 / 继续下载", style="Primary.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.sample_button = ttk.Button(row, text="试下前 3 章", command=lambda: self.start("sample"))
        self.sample_button.pack(side="left", padx=8)
        self.auth_button = ttk.Button(row, text="登录 / 验证", command=self.verify)
        self.auth_button.pack(side="left", padx=(0, 8))
        self.pause_button = ttk.Button(row, text="暂停全部", command=self.pause, state="disabled")
        self.pause_button.pack(side="left")
        self.stop_button = ttk.Button(row, text="停止并保存", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.remove_button = ttk.Button(row, text="移除选中", command=self.remove)
        self.remove_button.pack(side="right")
        self.manual_button = ttk.Button(row, text="已处理，继续", style="Primary.TButton", command=self.confirm, state="disabled")
        self.manual_button.pack(side="right", padx=8)
        management = ttk.Frame(outer)
        management.pack(fill='x',pady=(0,8))
        self.task_pause_button=ttk.Button(management,text='暂停/继续选中',command=lambda:self.task_command('pause'),state='disabled')
        self.task_pause_button.pack(side='left')
        self.task_stop_button=ttk.Button(management,text='停止选中',command=lambda:self.task_command('stop'),state='disabled')
        self.task_stop_button.pack(side='left',padx=8)
        self.delete_button = ttk.Button(management,text='删除文件和缓存',command=self.delete_selected)
        self.delete_button.pack(side='right')
        ttk.Button(management,text='查看日志',command=self.show_log).pack(side='left',padx=(0,8))
        ttk.Button(management,text='站点浏览器',command=lambda:self.browser_panel.show()).pack(side='left')
        self.failure_button = ttk.Button(management, text='查看失败页', command=self.open_failure_page, state='disabled')
        self.failure_button.pack(side='left', padx=8)
        self.bar = ttk.Progressbar(outer, mode="determinate", maximum=100)
        self.bar.pack(fill="x")
        ttk.Label(outer,textvariable=self.summary,style='Muted.TLabel').pack(anchor='w',pady=(4,0))
        # Long URLs/messages must not change the height of the task list.
        # The log window retains full messages while these lines show live progress.
        ttk.Label(outer, textvariable=self.status, width=1).pack(fill="x", pady=(4, 2))
        ttk.Label(outer, textvariable=self.detail, style="Muted.TLabel", width=1).pack(fill="x")
        self.log_window = tk.Toplevel(r)
        self.log_window.withdraw()
        self.log_window.title("逐页 · 运行日志")
        self.log_window.geometry("860x420")
        self.log_window.minsize(500, 220)
        self.log_window.configure(bg=BG)
        self.log_window.protocol("WM_DELETE_WINDOW", self.log_window.withdraw)
        ttk.Label(self.log_window, text="完整状态以站点当前目录为准。遇到访问限制会暂停；待核对文件与完整文件分开标记。", style="Muted.TLabel", wraplength=800, padding=8).pack(side="bottom", fill="x")
        self.log = tk.Text(self.log_window, bg="#0c1219", fg=MUTED, relief="flat", padx=8, pady=6, state="disabled", wrap="word")
        log_scroll = ttk.Scrollbar(self.log_window, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

    def show_log(self):
        self.log_window.deiconify()
        self.log_window.lift()

    def show_selected_error(self, event=None):
        self.failure_button.configure(state='normal' if any(i in self.error_pages for i in self.table.selection()) else 'disabled')
        for item in self.table.selection():
            if item in self.batch_errors:
                title = self.table.item(item, 'values')[0]
                self.detail.set(f'{title}：{self.batch_errors[item]}')
                return True
        return False

    def remember_failure(self, item, data):
        url = data.get('error_url')
        if not url or not self.table.exists(item):
            return
        try:
            validate_url(url)
            job_url = self.table.item(item, 'values')[3]
            if session_site(urlsplit(url).hostname) != session_site(urlsplit(job_url).hostname):
                return
        except (ValueError, TypeError, DownloadError):
            return
        self.error_pages[item] = {key:data.get(key) for key in ('error_url', 'error_status', 'error_chapter_title')}
        message = data.get('error_message') or data.get('message') or data.get('error')
        if message:
            chapter = data.get('error_chapter_title')
            self.batch_errors[item] = (f'{chapter}：' if chapter and not message.startswith(f'{chapter}：') else '') + message

    def open_failure_page(self):
        item = next((i for i in self.table.selection() if i in self.error_pages), None)
        if item is None:
            return
        url = self.error_pages[item]['error_url']
        self.browser_panel.open_url(session_site(urlsplit(url).hostname), url)
        self.status.set('查看失败页面；任务保持原状态。该章恢复正常后，返回队列点“开始 / 继续下载”')

    def persist(self):
        jobs = []
        for item in self.table.get_children():
            title, status, progress, url = self.table.item(item, "values")
            failure = self.error_pages.get(item, {})
            jobs.append(dict(title=title, status=status, progress=progress, url=url, **failure,
                             **({'error_message':self.batch_errors[item]} if failure and item in self.batch_errors else {})))
        save(self.data / "queue.json", jobs)
        save(self.data / "preferences.json", {"output": self.out.get(), "mode": self.mode.get(), "delay": self.delay.get(), "speed":self.speed.get(),
                                             'books':self.books.get(),'site_concurrency':self.site_concurrency.get(),
                                             'chapter_concurrency':self.chapter_concurrency.get(),'adaptive':self.adaptive.get(),
                                             'browser_backend':self.browser_backend.get()})
        self._persist_pending = False
        self._last_persist = time.monotonic()

    def restore_cache_progress(self):
        existing = {self.table.item(i,"values")[3]:i for i in self.table.get_children()}
        for path in (Path(self.out.get())/'.novel-cache').glob('*/manifest.json'):
            state=load(path,{})
            url=state.get('input_url')
            if not isinstance(url,str):
                continue
            try:
                validate_url(url)
            except Exception:
                continue
            title, _ = clean_book_identity(state.get('title','待解析'), state.get('author',''), url)
            values=(title,'已完成' if state.get('status')=='complete' else '可继续（缓存）',
                    f"{state.get('downloaded',0)}/{state.get('total',0)}",url)
            if url in existing:
                self.table.item(existing[url],values=values)
            else:
                existing[url]=self.table.insert('', 'end',values=values)
            item = existing[url]
            if state.get('status') == 'blocked':
                self.remember_failure(item, state)
                if state.get('error_status') == 403:
                    self.table.set(item, 'status', '访问被拒绝')

    def add_urls(self, urls=None):
        urls = parse_urls(self.input.get("1.0", "end")) if urls is None else urls
        existing = {self.table.item(i, "values")[3] for i in self.table.get_children()}
        invalid = []
        added = duplicates = 0
        for url in urls:
            try:
                validate_url(url)
                if url not in existing:
                    self.table.insert("", "end", values=("待解析", "待下载", "—", url))
                    existing.add(url)
                    added += 1
                else:
                    duplicates += 1
            except Exception as exc:
                invalid.append(str(exc))
        self.persist()
        if invalid:
            messagebox.showwarning("部分地址无效", "\n".join(dict.fromkeys(invalid)))
        if urls:
            self.input.delete("1.0", "end")
        self.status.set(f"加入 {added} 本；重复 {duplicates} 条；无效 {len(invalid)} 条" if urls else "未识别到链接，请粘贴 http:// 或 https:// 开头的小说地址")

    def import_file(self):
        path = filedialog.askopenfilename(filetypes=[("链接文本", "*.txt"), ("所有文件", "*.*")])
        if path:
            try:
                self.add_urls(parse_urls(Path(path).read_text(encoding="utf-8-sig")))
            except (OSError, UnicodeError) as exc:
                messagebox.showerror("无法导入", str(exc))

    def choose_folder(self):
        path = filedialog.askdirectory(initialdir=self.out.get())
        if path:
            self.out.set(path)
            self.restore_cache_progress()
            self.persist()

    def open_folder(self):
        path = Path(self.out.get()).resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("无法打开文件夹", str(exc))

    def remove(self):
        if self.worker and self.worker.is_alive():
            return
        for item in self.table.selection():
            self.table.delete(item)
        self.persist()

    def delete_selected(self):
        if self.worker and self.worker.is_alive():
            return
        selected=self.table.selection()
        if not selected:
            return messagebox.showinfo('选择小说','请先选中要删除的小说，可多选。')
        targets=[(item,self.table.item(item,'values')[0],self.table.item(item,'values')[3]) for item in selected]
        output=Path(self.out.get()).resolve()
        self.busy(True)
        self.status.set('正在核对选中小说的文件、报告与缓存…')
        self.pause_button.configure(state='disabled')
        self.stop_button.configure(state='disabled')
        self.task_pause_button.configure(state='disabled')
        self.task_stop_button.configure(state='disabled')
        def prepare():
            try:
                self._delete_plans=[(item,title,plan_delete(output,url)) for item,title,url in targets]
                self.events.put(dict(kind='delete_preview',message='删除范围已核对，请确认'))
            except Exception as exc:
                self.events.put(dict(kind='delete_error',message=f'无法准备删除：{exc}'))
        self.worker=threading.Thread(target=prepare,daemon=False)
        self.worker.start()

    def confirm_delete(self):
        win=tk.Toplevel(self.root)
        win.title('确认删除文件和缓存')
        win.geometry('800x470')
        win.minsize(560, 300)
        win.configure(bg=BG)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)
        win.transient(self.root)
        win.grab_set()
        warning=ttk.Label(win,text='将永久删除下面列出的文件和整个缓存目录，并移除任务。删除后无法断点续传。',wraplength=760)
        warning.grid(row=0,column=0,sticky='ew',padx=16,pady=12)
        content=ttk.Frame(win)
        content.grid(row=1,column=0,sticky='nsew',padx=16,pady=8)
        content.columnconfigure(0,weight=1)
        content.rowconfigure(0,weight=1)
        # Only the file list grows or shrinks; the action row always keeps its height.
        box=tk.Text(content,wrap='word',height=1,width=1,bg=PANEL,fg=FG)
        box.grid(row=0,column=0,sticky='nsew')
        scrollbar=ttk.Scrollbar(content,orient='vertical',command=box.yview)
        scrollbar.grid(row=0,column=1,sticky='ns')
        box.configure(yscrollcommand=scrollbar.set)
        win.bind('<Configure>',lambda event: warning.configure(wraplength=max(200,event.width-32)) if event.widget is win else None)
        for _,title,plan in self._delete_plans:
            box.insert('end',f'{title}\n来源：{plan.url}\n保存目录：{plan.root}\n')
            for path in plan.files:
                box.insert('end',f'文件：{path}\n')
            if plan.cache.exists():
                box.insert('end',f'整个缓存目录：{plan.cache}\n')
            if not plan.files and not plan.cache.exists():
                box.insert('end','未找到对应文件或缓存，仅移除列表任务。\n')
            box.insert('end','\n')
        box.configure(state='disabled')
        def cancel():
            win.destroy()
            self.busy(False)
            self.status.set('已取消删除，文件和缓存未改动')
        def apply():
            win.destroy()
            def perform():
                for item,title,plan in self._delete_plans:
                    try:
                        count=execute_delete(plan)
                        self.events.put(dict(kind='deleted',item=item,message=f'{title}：已删除 {count} 个导出文件/报告及缓存'))
                    except Exception as exc:
                        self.events.put(dict(kind='delete_error',item=item,message=f'{title} 删除未完成：{exc}'))
                self.events.put(dict(kind='idle',message='删除操作结束；未成功的任务仍保留在列表中'))
            self.worker=threading.Thread(target=perform,daemon=False)
            self.worker.start()
        buttons=ttk.Frame(win)
        buttons.grid(row=2,column=0,sticky='ew',padx=16,pady=12)
        ttk.Button(buttons,text='取消',command=cancel).pack(side='right')
        ttk.Button(buttons,text='确认删除',command=apply).pack(side='right',padx=10)
        win.protocol('WM_DELETE_WINDOW',cancel)

    def start(self, action="download"):
        if self.closing:
            return
        if self.worker and self.worker.is_alive():
            return
        try:
            delay = float(self.delay.get())
            if not 1 <= delay <= 300:
                raise ValueError("请求间隔需在 1～300 秒之间")
            books, site_concurrency = int(self.books.get()), int(self.site_concurrency.get())
            chapter_concurrency = int(self.chapter_concurrency.get())
            if not 1 <= books <= 8 or not 1 <= site_concurrency <= 4:
                raise ValueError('同时下载书数需为 1～8，每站请求并发上限需为 1～4')
            if not 1 <= chapter_concurrency <= 4:
                raise ValueError('每书章节并发需为 1～4')
            output = Path(self.out.get()).resolve()
            output.mkdir(parents=True, exist_ok=True)
        except (ValueError, OSError) as exc:
            return messagebox.showerror("设置有误", str(exc))
        if action == "list":
            urls = parse_urls(self.input.get("1.0", "end"))
            if len(urls) != 1:
                return messagebox.showinfo("输入列表页", "请在上方输入一个分类页、书单页或目录页地址。")
            jobs = [(None, urls[0])]
        else:
            if self.input.get("1.0", "end").strip():
                self.add_urls()
            selected = self.table.selection()
            ids = selected or self.table.get_children()
            jobs = [(i, self.table.item(i, "values")[3]) for i in ids if selected or self.table.item(i, "values")[1] != "已完成"]
            if not jobs:
                return messagebox.showinfo("添加小说", "请先加入目录链接，或选中需要重新处理的任务。")
        self.persist()
        self.control = Control(lambda **data: self.events.put(data))
        _, pause_every, pause_seconds = SPEEDS[self.speed.get()]
        self.batch_errors.clear()
        self.last_warnings.clear()
        options = dict(delay=delay, pause_every=pause_every, pause_seconds=pause_seconds, mode=self.mode.get(), proxy=self.proxy.get(), refresh=self.refresh.get(), output=output,
                       books=1 if action=='list' else books,site_concurrency=site_concurrency,
                       chapter_concurrency=chapter_concurrency,adaptive=self.adaptive.get(),
                       browser_backend=self.browser_backend.get())
        self.control.emit(kind='batch_settings', message=f'本轮设置：同站间隔 {delay:g} 秒、每次 1 本；跨站最多 {options["books"]} 本；每书 {chapter_concurrency} 章；站点请求上限 {site_concurrency}；运行中修改设置于下轮生效')
        self.row_progress.clear()
        self.running_items.clear()
        self.waiting_jobs.clear()
        self.busy(True)
        self.worker = threading.Thread(target=self.work, args=(jobs, action, options), daemon=False)
        self.worker.start()

    def browser_profile(self, host):
        site = session_site(host)
        base = self.data / 'browser-profiles'
        if site == '41nr.com':
            candidates = [self.data / 'browser-profile', base / site, base / 'm.41nr.com', base / 'www.41nr.com']
        elif site == 'banshanren.com':
            candidates = [self.home / 'evidence/banshanren-gui/data/browser-profile', base / site, base / 'www.banshanren.com']
        else:
            candidates = [base / site]
        return next((path for path in candidates if path.exists()), base / site)

    def work(self, jobs, action, opts):
        try:
            if 'books' in opts:
                from .parallel import ParallelBatch
                factory = self.browser_panel.broker if opts.get('browser_backend') == '内置浏览器' else None
                profile_for = (lambda host:self.browser_panel.profile(session_site(host))) if factory else self.browser_profile
                self.batch=ParallelBatch(self.control,opts,profile_for,session_site,browser_factory=factory)
                self.batch.run(jobs,action)
            else:
                with FetcherPool(self.control) as sessions:
                    self.work_jobs(jobs, action, opts, sessions)
        except Cancelled as exc:
            self.events.put(dict(kind="job_status", status="已停止", message=str(exc)))
        except Exception as exc:
            self.events.put(dict(kind="job_status", status="需处理", message=str(exc)))
        finally:
            self.batch=None
            self.events.put(dict(kind="idle", message="本轮处理结束，缓存和报告已保留"))

    def work_jobs(self, jobs, action, opts, sessions):
        blocked_hosts = set()
        try:
            for item, url in jobs:
                self.control.checkpoint()
                host = urlsplit(url).hostname
                site = session_site(host)
                self.events.put(dict(kind="active", item=item, message="开始处理", url=url))
                if site in blocked_hosts:
                    self.events.put(dict(kind="job_status", status="站点已暂停", message="该站点本轮已触发限制，跳过后续任务"))
                    continue
                existing_profile = self.browser_profile(host)
                mode = "browser" if action == "verify" or opts["mode"] == "浏览器模式" or (opts["mode"] == "自动选择" and (existing_profile.exists() or site == '41nr.com')) else "http"
                try:
                    interactive = mode == 'browser'
                    fetcher, reused = sessions.get(delay=opts["delay"], mode=mode, profile=existing_profile,
                                 pause_every=opts['pause_every'], pause_seconds=opts['pause_seconds'],
                                 proxy=opts["proxy"], headless=not interactive, manual=interactive,
                                 minimized=interactive and action != "verify")
                    self.events.put(dict(kind='session', site=site, reused=reused,
                                         message=f'{site}：' + ('复用本批浏览器会话，沿用登录与验证状态' if reused and mode == 'browser' else ('已启动专用浏览器会话' if mode == 'browser' else '使用 HTTP 会话'))))
                    if action == "list":
                        books = list_books(fetcher.get(url))
                        self.events.put(dict(kind="books", books=books, message=f"发现 {len(books)} 本候选小说"))
                    else:
                        Runner(fetcher, opts["output"], self.control, limit=3 if action == "sample" else 0,
                               refresh=opts["refresh"]).run(url)
                except SiteBlocked as exc:
                    blocked_hosts.add(site)
                    denied = isinstance(exc, StopWithoutVerification)
                    self.events.put(dict(kind="job_status", status=blocked_status(exc),
                                         message=str(exc) if denied else str(exc) + "；选中任务后点“登录 / 验证”，完成后会自动继续。"))
                except Cancelled:
                    raise
                except Exception as exc:
                    self.events.put(dict(kind="job_status", status="需处理", message=str(exc)))
        except Cancelled as exc:
            self.events.put(dict(kind="job_status", status="已停止", message=str(exc)))

    def busy(self, value):
        for button in (self.start_button, self.sample_button, self.auth_button, self.list_button, self.remove_button, self.delete_button):
            button.configure(state="disabled" if value else "normal")
        for button in (self.pause_button, self.stop_button,self.task_pause_button,self.task_stop_button):
            button.configure(state="normal" if value else "disabled")
        if not value:
            self.auth_button.configure(text="登录 / 验证")
            self.manual_button.configure(state="disabled")
            self.pause_button.configure(text="暂停全部")
            self.waiting_manual = False
            self.waiting_jobs.clear()
            self.running_items.clear()
            self.summary.set('本轮结束；排版与断点缓存保留')

    def pause(self):
        if self.control.paused.is_set():
            self.control.paused.clear()
            self.pause_button.configure(text="暂停全部")
            self.status.set("继续下载")
        else:
            self.control.paused.set()
            self.pause_button.configure(text="继续全部")
            self.status.set("已请求暂停：当前请求结束后暂停，缓存保留")

    def stop(self):
        self.control.cancel.set()
        self.status.set("正在停止：等待当前请求结束，随后保存进度（通常不超过 25 秒）")

    def confirm(self):
        if self.batch:
            self.batch.command(self.verification_items(),'approve')
            return
        self.control.approved.set()
        self.manual_button.configure(state="disabled")

    def verify(self):
        if self.worker and self.worker.is_alive():
            if self.waiting_manual:
                if self.batch:
                    self.batch.command(self.verification_items(),'show')
                else:
                    self.control.show_browser.set()
            return
        self.start("verify")

    def task_command(self, action):
        if self.batch:
            self.batch.command(self.table.selection(),action)

    def verification_items(self):
        selected=[item for item in self.table.selection() if item in self.waiting_jobs]
        return selected or list(self.waiting_jobs)[:1]

    def verification_buttons(self):
        self.waiting_manual=bool(self.waiting_jobs)
        self.manual_button.configure(state='normal' if self.waiting_manual else 'disabled')
        self.auth_button.configure(state='normal' if self.waiting_manual else 'disabled',text='打开验证页面' if self.waiting_manual else '登录 / 验证')

    def update_batch_progress(self):
        downloaded=sum(done for done,total in self.row_progress.values())
        total=sum(total for done,total in self.row_progress.values())
        self.bar['value']=downloaded/max(1,total)*100
        errors = f' · 需处理 {len(self.batch_errors)} 本（选中查看原因）' if self.batch_errors else ''
        self.summary.set(f'运行 {len(self.running_items)} 本 · 等待验证 {len(self.waiting_jobs)} 本' + errors)

    def set_row(self, item=None, **changes):
        item=self.active if item is None else item
        if item and self.table.exists(item):
            values = list(self.table.item(item, "values"))
            for key, index in {"title": 0, "status": 1, "progress": 2}.items():
                if key in changes:
                    values[index] = changes[key]
            self.table.item(item, values=values)

    def poll(self):
        if (self.data / "stop-current-task").exists():
            self.control.cancel.set()
            (self.data / "stop-current-task").unlink(missing_ok=True)
        received = []
        for _ in range(500):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            received.append(json.dumps(dict(time=datetime.now().isoformat(), **event), ensure_ascii=False) + "\n")
            previous = self._display_events[-1] if self._display_events else None
            if (previous and previous.get('kind') == event.get('kind') == 'chapter'
                    and previous.get('title') == event.get('title') and previous.get('item') == event.get('item')):
                self._display_events[-1] = event
            else:
                self._display_events.append(event)
        if received:
            with self.logfile.open('a',encoding='utf-8') as stream:
                stream.writelines(received)
        try:
            deadline = time.monotonic() + .012
            # Return to Tk frequently even when a resumed job floods the queue.
            for _ in range(25):
                if time.monotonic() >= deadline:
                    break
                if not self._display_events:
                    break
                event = self._display_events.popleft()
                kind = event.get("kind")
                item=event.get('item',self.active)
                message = event.get("message", "")
                if kind == 'idle' and self.batch_errors:
                    message = '本轮未完成：' + next(iter(self.batch_errors.values()))
                if message:
                    self.status.set(message)
                    self.log.configure(state="normal")
                    self.log.insert("end", datetime.now().strftime("%H:%M:%S") + "  " + message + "\n")
                    if int(self.log.index("end-1c").split('.')[0]) > 300:
                        self.log.delete("1.0", "51.0")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                if kind == "active":
                    self.active = event["item"]
                    self.error_pages.pop(item, None)
                    self.batch_errors.pop(item, None)
                    self.running_items.add(item)
                    self.set_row(item=item,status="解析中")
                    if not self.show_selected_error():
                        self.detail.set(event.get('url') or event.get('job_url', ''))
                elif kind == "toc":
                    self.set_row(item=item, status=f"目录第 {event.get('toc_pages', '?')} 页",
                                 progress=f"发现 {event['total']} 章", **({'title':event['title']} if event.get('title') else {}))
                    # Directory discovery is a separate stage, not zero downloaded.
                    downloaded = self.row_progress.get(item, (0, 0))[0]
                    self.row_progress[item]=(downloaded,event['total'])
                elif kind == 'request':
                    if not self.show_selected_error():
                        self.detail.set(event.get('url', event.get('job_url', '')))
                elif kind == "chapter":
                    self.set_row(item=item,title=event["title"], status="下载中", progress=f"{event['downloaded']}/{event['total']}")
                    self.row_progress[item]=(event['downloaded'],event['total'])
                elif kind == 'page':
                    self.set_row(item=item,status=f'正文第 {event["page_current"]}/{event.get("page_total") or "?"} 页')
                elif kind == "done":
                    self.error_pages.pop(item, None)
                    self.batch_errors.pop(item, None)
                    self.show_selected_error()
                    result = event["result"]
                    self.set_row(item=item,status={"complete":"已完成", "sample":"试下载完成", "incomplete":"待核对"}.get(result['status'], result['status']), title=result["title"], progress=f"{result['downloaded']}/{result['total']}")
                    self.row_progress[item]=(result['downloaded'],result['total'])
                    self.detail.set(result.get("output") or "未生成正文文件，请查看日志")
                    if result['status'] == 'incomplete':
                        self.batch_errors[item] = f"已保存 {result['downloaded']}/{result['total']} 章，缺章或疑点见报告"
                elif kind == 'warning':
                    self.last_warnings[item] = message
                elif kind == "manual":
                    self.waiting_jobs[item]=event
                    self.verification_buttons()
                    self.set_row(item=item,status="等待操作")
                    self.detail.set(event["url"])
                    if self.batch and self.batch.opts.get('browser_backend') == '内置浏览器':
                        self.batch.command([item],'show')
                elif kind == "manual_done":
                    self.waiting_jobs.pop(item,None)
                    self.verification_buttons()
                    self.set_row(item=item,status="下载中")
                elif kind == "job_status":
                    self.set_row(item=item,status=event["status"])
                    if event['status'] in {'需处理', '需要登录/验证', '需要登录', '需要真人验证', '站点已暂停', '访问被拒绝', '持续限流', '服务器暂不可用', '权限或订阅限制', '代理需要认证'}:
                        self.batch_errors[item] = message
                        self.remember_failure(item, event)
                        self.show_selected_error()
                elif kind == 'job_finished':
                    self.running_items.discard(item)
                    self.waiting_jobs.pop(item,None)
                    self.verification_buttons()
                elif kind == "books":
                    self.show_books(event["books"])
                elif kind == "idle":
                    self.busy(False)
                    self.active = None
                    if self.batch_errors:
                        self.status.set(message)
                        self.summary.set(f'本轮未完成 {len(self.batch_errors)} 本；已下载章节和缓存保留')
                        self.detail.set(next(iter(self.batch_errors.values())))
                elif kind == 'delete_preview':
                    self.confirm_delete()
                elif kind == 'deleted':
                    if self.table.exists(event['item']):
                        self.table.delete(event['item'])
                elif kind == 'delete_error':
                    if event.get('item') and self.table.exists(event['item']):
                        self.table.set(event['item'],'status','删除未完成')
                    else:
                        self.busy(False)
                if kind not in {"wait", "request"}:
                    self._persist_pending = True
                if kind in {'active','toc','chapter','done','manual','manual_done','job_finished','job_status'}:
                    self.update_batch_progress()
        except queue.Empty:
            pass
        if self._persist_pending and time.monotonic() - self._last_persist >= 1:
            try:
                self.persist()
            except OSError as exc:
                self._last_persist = time.monotonic()
                self.status.set(f"界面队列保存失败：{exc}；下载缓存由任务单独保存")
        if self.closing and not (self.worker and self.worker.is_alive()) and self.events.empty() and not self._display_events:
            self.persist()
            self.finish_close()
            return
        self.root.after(20 if not self.events.empty() or self._display_events else 120, self.poll)

    def show_books(self, books):
        if not books:
            messagebox.showinfo("解析结果", "没有识别到书籍链接。请尝试小说详情页或目录页。")
            return
        win = tk.Toplevel(self.root)
        win.title("选择要加入队列的小说")
        win.geometry("800x450")
        tree = ttk.Treeview(win, columns=("title", "url"), show="headings", selectmode="extended")
        tree.heading("title", text="书名")
        tree.heading("url", text="来源地址")
        tree.column("title", width=250)
        tree.column("url", width=520)
        tree.pack(fill="both", expand=True, padx=12, pady=12)
        for book in books:
            tree.insert("", "end", values=(book["title"], book["url"]))
        def add():
            self.add_urls([tree.item(i, "values")[1] for i in tree.selection()])
            win.destroy()
        row = ttk.Frame(win, padding=10)
        row.pack(fill="x")
        ttk.Button(row, text="全选", command=lambda: tree.selection_set(tree.get_children())).pack(side="left")
        ttk.Button(row, text="加入选中的小说", command=add).pack(side="right")

    def close(self):
        self.persist()
        if self.worker and self.worker.is_alive():
            self.closing = True
            self.stop()
        else:
            self.finish_close()

    def finish_close(self):
        if getattr(self, '_finishing_close', False): return
        self._finishing_close = True
        self.closing = True
        self.browser_panel.close()
        self.status.set('正在保存并关闭内置浏览器…')
        def wait_browser():
            if self.browser_panel.closed.is_set(): self.root.destroy()
            else: self.root.after(100, wait_browser)
        wait_browser()


def main(home, smoke=False, initial_urls=(), autorun=False, output=None, force_browser=False):
    root = tk.Tk()
    if smoke:
        root.withdraw()
        root.attributes('-alpha', 0)
    app = App(root, Path(home) / "smoke-test" if smoke else home)
    if output:
        app.out.set(str(output))
    if force_browser:
        app.mode.set("浏览器模式")
        app.delay.set("10")
    if initial_urls:
        app.add_urls(list(initial_urls))
    if autorun:
        root.after(500, app.start)
    if smoke:
        root.title("逐页 · 启动自检（自动关闭）")
        root.deiconify()
        def finish_smoke():
            root.update_idletasks()
            restored=[list(app.table.item(item,'values')) for item in app.table.get_children()]
            list_height=app.table.winfo_height()
            assert list_height>=180
            for item in app.table.get_children()[:2]:
                box=app.table.bbox(item)
                assert box and box[1]+box[3]<=list_height
            for item in app.table.get_children():
                app.table.delete(item)
            app.input.insert('1.0','https://example.org/book/1\nhttps://example.org/book/2\nhttps://example.org/book/1')
            app.add_urls()
            count=len(app.table.get_children())
            assert count==2
            save(Path(home) / "smoke-test" / "result.json", {"status":"ok", "queue_columns":list(app.table['columns']),
                 'initial_restored_queue':restored,'list_height':list_height,'restored_rows_visible':True,
                 "batch_input_deduplicated":count,"speed_presets":list(SPEEDS),"verification_button":app.auth_button['text'],
                 "delete_button":app.delete_button['text'], 'books':app.books.get(),'site_concurrency':app.site_concurrency.get(),
                 'chapter_concurrency':app.chapter_concurrency.get(),'same_site_books':1,'adaptive':app.adaptive.get()})
            root.destroy()
        root.after(1500, finish_smoke)
    root.mainloop()

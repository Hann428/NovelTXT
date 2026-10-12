"""Offline counterexamples for response classification; never touches live data."""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'vendor/Novel-Get/src')]
import httpx
from novel_app import network
from novel_app.browser_parallel import BrowserBroker
from novel_app.sites import guard_body, extract_readable
from novel_get.fetch import Page
from novel_get.parse import extract_chapter

rows = []
url = 'https://m.41nr.com/chapter/28425/5828743.html'
prose = '清晨他沿着小路走到桥边，遇见正在收拾钓具的老人，两人坐下来聊起往事，远处传来孩子们的笑声。'

def probe(name, expected, fn):
    try:
        fn()
        actual = 'accepted'
        detail = ''
    except Exception as error:
        actual = type(error).__name__
        detail = str(error)
    rows.append(dict(name=name, expected=expected, actual=actual, detail=detail))

probe('正常章节标题含安全验证', 'accepted', lambda: network.check_page_response(
    Page(url, f'<title>第12章 安全验证</title><h1 id="nr_title">第12章 安全验证</h1><div id="nr1">{prose}</div>'), 200))
probe('正常正文引用访问过于频繁', 'accepted', lambda: guard_body(
    Page(url, f'<div id="nr1">{prose}他的手机上弹出提示：访问过于频繁，请稍后再试。{prose}</div>'), '#nr1'))
probe('正文里的订阅说明没有真人验证', 'not-human-verification', lambda: guard_body(
    Page(url, '<div id="nr1">本章节需要订阅后才能阅读。</div>'), '#nr1'))
probe('200登录页仅有密码表单', 'login-required', lambda: network.check_page_response(
    Page(url, '<title>会员登录</title><form><input type="password"><button>登录</button></form>'), 200))
probe('503标题请稍候但没有验证控件', 'PageHTTPError', lambda: network.check_page_response(
    Page(url, '<title>请稍候…</title><h1>服务维护</h1>服务器暂时不可用'), 503))

class FakePage:
    def __init__(self, destination):
        self.url = destination
    async def content(self):
        return f'<h1 id="nr_title">第12章 桥边</h1><div id="nr1">{prose}</div>'

broker = BrowserBroker.__new__(BrowserBroker)
probe('同站规范网址添加查询参数', 'accepted-or-explicit-redirect', lambda: asyncio.run(
    broker._readable(FakePage(url+'?from=mobile'), {'code':200}, url)))
probe('跳转后的页面为502', 'PageHTTPError', lambda: asyncio.run(
    broker._readable(FakePage(url+'?error=1'), {'code':502}, url)))

def maintenance_body():
    page = Page(url, '<h1 id="nr_title">第12章 桥边</h1><div id="nr1">'
                '系统维护中，当前页面内容暂时无法提供，请您稍后再次访问。给您的阅读带来不便，我们深表歉意，感谢理解与支持。'
                '</div>')
    network.check_page_response(page, 200)
    guard_body(page, '#nr1')
    extract_chapter(page, title_selector='#nr_title', content_selector='#nr1', min_chars=40)

probe('200维护提示保留原章节标题与容器', 'reject-placeholder', maintenance_body)
probe('合法短尾页不足40字', 'short-page-needs-context', lambda: extract_readable(
    Page(url, '<h1 id="nr_title">第12章 桥边 (2/2)</h1><div id="nr1">他转过身，故事到此结束。</div>'),
    {'title_selector':'#nr_title', 'content_selector':'#nr1'}))

def long_server_cooldown():
    original = network.validate_url
    network.validate_url = lambda address, resolve=False: address
    try:
        with network.Fetcher(network.Control()) as fetcher:
            fetcher.client.close()
            fetcher.client = httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(503, headers={'retry-after':'3600'})))
            fetcher.pace = lambda: None
            fetcher.get(url)
    finally:
        network.validate_url = original

probe('503要求冷却一小时', 'server-cooldown-not-login', long_server_cooldown)
for status in (407, 410, 429, 451, 500, 502, 503, 504):
    probe(f'普通HTTP{status}', 'status-specific', lambda status=status: network.check_page_response(
        Page(url, '<title>请求暂时无法完成</title>'), status))

report = ROOT / 'evidence' / 'response-classification-fix5-20261010.json'
report.write_text(json.dumps(dict(offline=True, running_release_changed=False, source_revision='fix5', scenarios=rows), ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(report=str(report), scenarios=[{k:r[k] for k in ('name','expected','actual')} for r in rows]), ensure_ascii=False))

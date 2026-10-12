import asyncio
from types import SimpleNamespace

import httpx
import pytest
from novel_get.fetch import Page, DownloadError, SiteBlocked
from novel_get.parse import extract_chapter
from novel_app import network
from novel_app.browser_parallel import BrowserBroker
from novel_app.response import (RateLimited, ServerUnavailable, PermissionRequired,
    ProxyAuthenticationRequired, LoginRequired, VerificationRequired, SuspectContent, check_destination)
from novel_app.parallel import SiteGate, TaskControl
from novel_app.sites import guard_body, extract_readable
from novel_app.core import Runner
from novel_app.storage import load
from test_downloader import FakeFetcher, BASE, PROSE, chapter, toc
from test_network import fake_fetcher

URL = 'https://m.41nr.com/chapter/7/12.html'
CONFIG = {'content_selector':'#nr1', 'title_selector':'#nr_title'}


@pytest.mark.parametrize('title', ['第12章 安全验证','第12章 身份验证','第12章 Just a moment'])
def test_title_words_do_not_stop_real_prose_or_vendor_parser(title):
    page = Page(URL, f'<title>{title}</title><h1 id="nr_title">{title}</h1><div id="nr1">{PROSE}</div>')
    network.check_page_response(page,200)
    assert extract_readable(page,CONFIG).title == title


@pytest.mark.parametrize('quote', ['访问过于频繁，请稍后再试。','请完成验证后继续阅读','订阅后才能阅读'])
def test_prose_quoting_site_messages_is_not_an_access_gate(quote):
    page = Page(URL,f'<h1 id="nr_title">第12章 故事</h1><div id="nr1">{PROSE}他看到提示：“{quote}”{PROSE}</div>')
    guard_body(page,'#nr1')
    assert quote in extract_readable(page,CONFIG).text


@pytest.mark.parametrize('fragment', ['<form hidden id="captcha-form"></form>',
    '<div style="display:none"><form id="captcha-form"></form></div>',
    '<form id="captcha-form"><button>评论验证</button></form>',
    '<form hidden><input type="password"></form><div class="cf-turnstile">评论验证码</div>'])
def test_hidden_access_ui_and_comment_widgets_do_not_gate_prose(fragment):
    page=Page(URL,f'<h1 id="nr_title">第12章 故事</h1><div id="nr1">{PROSE}{fragment}</div>')
    network.check_page_response(page,200)
    guard_body(page,'#nr1')


@pytest.mark.parametrize('status,error',[(407,ProxyAuthenticationRequired),(451,PermissionRequired),(429,RateLimited)])
def test_stop_reason_is_preserved_for_same_site_queue(status,error):
    with pytest.raises(error) as raised:
        network.check_page_response(Page(URL,'<title>错误</title>'),status)
    gate=SiteGate('site',network.Control(),delay=0)
    gate.block(raised.value)
    with pytest.raises(error) as copied: gate.raise_blocked()
    assert copied.value.status==status and not gate.verifying


@pytest.mark.parametrize('status',[204,206,304,500,502,503,504])
def test_noninteractive_status_never_becomes_captcha_even_with_wait_title(status):
    with pytest.raises(network.PageHTTPError):
        network.check_page_response(Page(URL,'<title>请稍候…</title>'),status)


@pytest.mark.parametrize('status',[200,403,429,503])
def test_explicit_challenge_header_has_precedence(status):
    with pytest.raises(VerificationRequired):
        network.check_page_response(Page(URL,'<title>页面</title>'),status,True)


def test_200_dedicated_login_and_normal_login_modal_are_distinct():
    with pytest.raises(LoginRequired):
        network.check_page_response(Page(URL,'<title>会员登录</title><form><input type="password"></form>'),200)
    network.check_page_response(Page(URL,f'<title>第12章 故事</title><div id="nr1">{PROSE}</div><form><input type="password"></form>'),200)


@pytest.mark.parametrize('message,error', [('访问过于频繁，请稍后再试。',RateLimited),
    ('本章节需要订阅后才能阅读。',PermissionRequired),('本章节需要登录后才能阅读。',LoginRequired),
    ('请完成验证后继续阅读',SuspectContent),
    ('系统维护中，当前内容暂时无法提供，请您稍后再次访问。给您的阅读带来不便，我们深表歉意，感谢理解与支持。',SuspectContent)])
def test_access_or_placeholder_body_is_rejected_even_with_valid_chapter_title(message,error):
    with pytest.raises(error):
        extract_readable(Page(URL,f'<h1 id="nr_title">第12章 故事</h1><div id="nr1">{message}</div>'),CONFIG)


def test_normalized_query_is_allowed_but_different_chapter_is_not_verification():
    check_destination(Page(URL+'?from=mobile',''),URL)
    with pytest.raises(DownloadError) as raised: check_destination(Page(URL.replace('12.html','13.html'),''),URL)
    assert not isinstance(raised.value,SiteBlocked)


def test_redirect_target_http_error_precedes_url_comparison():
    class PageStub:
        url=URL+'?error=1'
        async def content(self): return '<title>502 Bad Gateway</title>'
    with pytest.raises(network.PageHTTPError) as raised:
        asyncio.run(BrowserBroker.__new__(BrowserBroker)._readable(PageStub(),{'code':502},URL))
    assert raised.value.status==502


def test_http_server_cooldown_is_not_login(monkeypatch):
    with fake_fetcher(monkeypatch,lambda req:httpx.Response(503,headers={'retry-after':'3600'})) as fetcher:
        with pytest.raises(ServerUnavailable) as raised: fetcher.get(URL)
    assert network.blocked_status(raised.value)=='服务器暂不可用'


@pytest.mark.parametrize('status',[204,206,304])
def test_raw_empty_partial_or_cache_status_is_explicit_and_not_retried(monkeypatch,status):
    calls=[]
    def reply(req):
        calls.append(req.url)
        return httpx.Response(status)
    with fake_fetcher(monkeypatch,reply) as fetcher:
        with pytest.raises(network.PageHTTPError) as raised: fetcher.get(URL)
    assert raised.value.status==status and len(calls)==1


def test_only_contextual_short_tail_completes_and_resumes(tmp_path):
    first,tail=BASE+'1.html',BASE+'1_2.html'
    text='　　他转过身，故事到此结束。'
    pages={BASE:toc([('第1章 起点',first)]), first:chapter('第1章 起点 (1/2)',next_url=tail),
           tail:f'<h1>第1章 起点 (2/2)</h1><div id="content"><p>{text}</p></div>'}
    result=Runner(FakeFetcher(pages),tmp_path,network.Control()).run(BASE)
    assert result['status']=='complete' and result['downloaded']==1
    from pathlib import Path
    assert text in Path(result['output']).read_text('utf-8')
    resume=FakeFetcher({})
    assert Runner(resume,tmp_path,network.Control()).run(BASE)['status']=='complete' and not resume.calls


def test_short_standalone_or_wrong_number_is_not_silently_accepted(tmp_path):
    first,tail=BASE+'1.html',BASE+'1_2.html'
    for heading in ('第1章 起点','第1章 起点 (3/3)'):
        page=Page(first,f'<h1>{heading}</h1><div id="content">结束。</div>')
        if '(' not in heading:
            with pytest.raises(DownloadError): extract_readable(page,{})
    pages={BASE:toc([('第1章 起点',first)]),first:chapter('第1章 起点 (1/2)',next_url=tail),
           tail:'<h1>第1章 起点 (3/3)</h1><div id="content">结束。</div>'}
    result=Runner(FakeFetcher(pages),tmp_path,network.Control()).run(BASE)
    assert result['status']=='incomplete' and result['downloaded']==0


def test_maintenance_never_enters_page_cache_or_txt_and_other_chapter_downloads(tmp_path):
    first,other=BASE+'1.html',BASE+'2.html'
    maintenance='系统维护中，当前页面内容暂时无法提供，请您稍后再次访问。给您的阅读带来不便，我们深表歉意，感谢理解与支持。'
    result=Runner(FakeFetcher({BASE:toc([('第1章 起点',first),('第2章 远方',other)]),
        first:chapter('第1章 起点',maintenance),other:chapter('第2章 远方')}),tmp_path,network.Control()).run(BASE)
    assert result['status']=='incomplete' and result['downloaded']==1
    from pathlib import Path
    assert '系统维护' not in Path(result['output']).read_text('utf-8')
    pages=[load(p) for p in tmp_path.glob('.novel-cache/*/pages/*.json')]
    assert len(pages)==1 and pages[0]['url']==other


def test_normalized_page_cache_keeps_requested_identity(tmp_path):
    from novel_app.storage import digest
    first=BASE+'1.html'
    runner=Runner(FakeFetcher({first:chapter('第1章 起点')}),tmp_path,network.Control())
    runner.folder=tmp_path/'cache';runner.folder.mkdir()
    class Canonical(FakeFetcher):
        def get(self,url,referer=None): return Page(url+'?from=mobile',chapter('第1章 起点'))
    runner.fetcher=Canonical({});runner.get_page(first,BASE,{})
    runner.fetcher=FakeFetcher({})
    assert runner.get_page(first,BASE,{}).url==first+'?from=mobile' and not runner.fetcher.calls

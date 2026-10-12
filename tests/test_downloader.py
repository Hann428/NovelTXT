import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from novel_get.fetch import Page, DownloadError, SiteBlocked

from novel_app.core import Runner
from novel_app.network import Control, Cancelled, validate_url
from novel_app.sites import site_config, guard_body
from novel_app.storage import load
from novel_app.text import preserve_text

BASE = "https://books.example/book/7/"
PROSE = "\u3000\u3000清晨的院子里，桂花落在石桌上。小林推开门，沿着小路走到桥边，看见远处有人正在收拾钓具。"


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []
    def get(self, url, referer=None):
        self.calls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return Page(url, value)


def toc(chapters, next_url=""):
    body = '<h1>测试小说</h1><div id="list">' + ''.join(f'<a href="{url}">{title}</a>' for title,url in chapters) + '</div>'
    if next_url:
        body += f'<a href="{next_url}">下一页</a>'
    return body


def chapter(title, text=PROSE, next_url=""):
    return f'<h1>{title}</h1><div id="content"><p>{text}</p><p></p><p>第二段落，继续保留文字。</p></div>' + (f'<a href="{next_url}">下一页</a>' if next_url else '')


def test_whitespace_inline_and_comments():
    body = BeautifulSoup('<div>\n <p>\u3000\u3000甲<span>乙</span> 丙<span class="z">12</span></p>\n <p></p><p>丁<br><br>戊</p>\n</div>', 'html.parser').div
    assert preserve_text(body, ".z") == "\u3000\u3000甲乙 丙\n\n丁\n\n戊"


def test_explicit_indentation_and_pre():
    body = BeautifulSoup('<div><p style="text-indent:2em">正文</p><pre>  a\n\n b</pre></div>', 'html.parser').div
    assert preserve_text(body) == "\u3000\u3000正文\n  a\n\n b"


def test_paginated_toc_and_chapter(tmp_path):
    a,b = BASE+'1.html', BASE+'2.html'
    fetch = FakeFetcher({BASE:toc([('第1章 起点',a)], BASE+'index_2.html'), BASE+'index_2.html':toc([('第2章 远方',b)]),
                         a:chapter('第1章 起点 (1/2)', next_url=BASE+'1_2.html'), BASE+'1_2.html':chapter('第1章 起点 (2/2)', PROSE+'更多文字'), b:chapter('第2章 远方', PROSE+'另一天')})
    result = Runner(fetch,tmp_path,Control()).run(BASE)
    assert result['status'] == 'complete'
    assert result['downloaded'] == result['total'] == 2
    data = Path(result['output']).read_bytes()
    assert b'\r\n' in data
    assert '\u3000\u3000'.encode() in data
    assert data.count('更多文字'.encode()) == 1
    assert b'\r\n\r\n' in data


def test_same_title_different_contents_are_kept(tmp_path):
    a,b = BASE+'1.html',BASE+'2.html'
    fetch = FakeFetcher({BASE:toc([('第1章 重名',a),('第1章 重名',b)]), a:chapter('第1章 重名'), b:chapter('第1章 重名', PROSE+'不同结尾')})
    result = Runner(fetch,tmp_path,Control()).run(BASE)
    assert result['downloaded'] == 2
    assert result['status'] == 'incomplete'
    assert '不同结尾' in Path(result['output']).read_text('utf-8')


def test_resume_does_not_refetch_good_chapter(tmp_path):
    a,b = BASE+'1.html',BASE+'2.html'
    fetch = FakeFetcher({BASE:toc([('第1章 起点',a),('第2章 远方',b)]),a:chapter('第1章 起点'), b:SiteBlocked('403')})
    with pytest.raises(SiteBlocked):
        Runner(fetch,tmp_path,Control()).run(BASE)
    fetch2 = FakeFetcher({b:chapter('第2章 远方', PROSE+'另一天')})
    result = Runner(fetch2,tmp_path,Control()).run(BASE)
    assert fetch2.calls == [b]
    assert result['status'] == 'complete'


def test_plain_403_preserves_cached_chapters_and_resumes_without_refetch(tmp_path):
    from novel_app.network import AccessDenied
    a,b=BASE+'1.html',BASE+'2.html'
    fetch=FakeFetcher({BASE:toc([('第1章 起点',a),('第2章 远方',b)]),a:chapter('第1章 起点'),b:AccessDenied(403,b)})
    with pytest.raises(AccessDenied):Runner(fetch,tmp_path,Control()).run(BASE)
    state=load(next(tmp_path.glob('.novel-cache/*/manifest.json')))
    assert state['downloaded']==1 and state['error_status']==403 and state['error_url']==b
    assert state['error_chapter_title'] == '第2章 远方'
    fetch2=FakeFetcher({b:chapter('第2章 远方',PROSE+'终点')})
    result=Runner(fetch2,tmp_path,Control()).run(BASE)
    assert fetch2.calls==[b] and result['downloaded']==2 and result['status']=='complete'


def test_resume_mid_chapter_reuses_saved_first_page(tmp_path):
    a,b=BASE+'1.html',BASE+'1_2.html'
    first=FakeFetcher({BASE:toc([('第1章 起点',a)]),
                       a:chapter('第1章 起点 (1/2)',next_url=b), b:SiteBlocked('403')})
    with pytest.raises(SiteBlocked):
        Runner(first,tmp_path,Control()).run(BASE)
    assert len(list(tmp_path.glob('.novel-cache/*/pages/*.json')))==1
    second=FakeFetcher({b:chapter('第1章 起点 (2/2)',PROSE+'第二页内容')})
    result=Runner(second,tmp_path,Control()).run(BASE)
    assert second.calls==[b]
    assert result['status']=='complete'
    assert result['chapters'][0]['pages']==[a,b]


def test_bulk_cache_resume_coalesces_progress_and_preserves_count(tmp_path,monkeypatch):
    import novel_app.core as core
    refs=[(f'第{i}章 测试',BASE+f'{i}.html') for i in range(1,61)]
    pages={BASE:toc(refs),**{url:chapter(title,PROSE+str(i)) for i,(title,url) in enumerate(refs)}}
    Runner(FakeFetcher(pages),tmp_path,Control()).run(BASE)
    events=[]
    monkeypatch.setattr(core.time,'monotonic',lambda:100)
    fetch=FakeFetcher({})
    result=Runner(fetch,tmp_path,Control(lambda **event:events.append(event))).run(BASE)
    updates=[event for event in events if event['kind']=='chapter']
    assert fetch.calls==[]
    assert len(updates)==2
    assert updates[-1]['downloaded']==60
    assert result['downloaded']==60


def test_truncated_chapter_never_marked_complete(tmp_path):
    a = BASE+'1.html'
    fetch = FakeFetcher({BASE:toc([('第1章 起点',a)]),a:chapter('第1章 起点 (1/2)')})
    result = Runner(fetch,tmp_path,Control()).run(BASE)
    assert result['status'] == 'incomplete'
    assert result['output'] is None
    assert len(result['failures']) == 1


def test_mixed_cache_holes_do_not_stop_remaining_book(tmp_path):
    from novel_app.network import PageHTTPError
    refs = [(f'第{i}章 测试', BASE+f'{i}.html') for i in range(1, 6)]
    errors = [PageHTTPError(404, refs[0][1]), DownloadError('正文尚未恢复可读'),
              DownloadError('正文存在异常字符')]
    fetch = FakeFetcher({BASE:toc(refs), **{refs[i][1]:e for i,e in enumerate(errors)},
                         **{url:chapter(title, PROSE+str(i)) for i,(title,url) in enumerate(refs[3:])}})
    result = Runner(fetch, tmp_path, Control()).run(BASE)
    assert result['status'] == 'incomplete' and result['downloaded'] == 2
    assert len(result['failures']) == 3
    assert result['failures'][0]['http_status'] == 404
    assert refs[-1][1] in fetch.calls and Path(result['output']).exists()
    # Resume reads only the holes and merges the retained good chapters in order.
    retry = FakeFetcher({url:chapter(title, PROSE+str(i+10)) for i,(title,url) in enumerate(refs[:3])})
    resumed = Runner(retry, tmp_path, Control()).run(BASE)
    assert resumed['status'] == 'complete' and resumed['downloaded'] == 5
    assert retry.calls == [url for _,url in refs[:3]]


def test_repeated_missing_pages_continue_but_repeated_parser_error_stops(tmp_path):
    from novel_app.network import PageHTTPError
    refs = [(f'第{i}章 测试', BASE+f'{i}.html') for i in range(1, 5)]
    missing = FakeFetcher({BASE:toc(refs), **{url:PageHTTPError(404,url) for _,url in refs[:3]},
                           refs[-1][1]:chapter(refs[-1][0])})
    result = Runner(missing, tmp_path/'missing', Control()).run(BASE)
    assert result['status'] == 'incomplete' and result['downloaded'] == 1
    broken = FakeFetcher({BASE:toc(refs), **{url:DownloadError('正文仍是编码数据') for _,url in refs}})
    with pytest.raises(DownloadError, match='连续三次出现相同错误'):
        Runner(broken, tmp_path/'broken', Control()).run(BASE)
    assert refs[-1][1] not in broken.calls
    assert len(load(next((tmp_path/'broken').glob('.novel-cache/*/manifest.json')))['failures']) == 3


def test_directory_events_report_stage_and_page_count(tmp_path):
    events = []
    a,b=BASE+'1.html',BASE+'2.html'
    fetch = FakeFetcher({BASE:toc([('第1章 起点',a)], BASE+'index_2.html'),
                         BASE+'index_2.html':toc([('第2章 远方',b)])})
    Runner(fetch, tmp_path, Control(lambda **e:events.append(e))).run(BASE, inspect=True)
    updates = [e for e in events if e['kind']=='toc']
    assert [(e['toc_pages'],e['total']) for e in updates] == [(1,1),(2,2)]
    assert all(e['title']=='测试小说' for e in updates)


def test_bad_cache_is_replaced(tmp_path):
    a = BASE+'1.html'
    pages = {BASE:toc([('第1章 起点',a)]),a:chapter('第1章 起点')}
    first = Runner(FakeFetcher(pages),tmp_path,Control()).run(BASE)
    cached = next(tmp_path.glob('.novel-cache/*/chapters/*.json'))
    record = json.loads(cached.read_text('utf-8'))
    record['text'] = '损坏'
    cached.write_text(json.dumps(record), 'utf-8')
    second = Runner(FakeFetcher({}),tmp_path,Control()).run(BASE)
    assert '损坏' not in Path(second['output']).read_text('utf-8')
    assert Path(first['output']).exists()
    assert first['output'] == second['output']  # Same verified source keeps its clean filename.


def test_cancel_keeps_toc_checkpoint(tmp_path):
    c = Control()
    def emit(**event):
        if event['kind']=='toc': c.cancel.set()
    c.emit = emit
    a = BASE+'1.html'
    with pytest.raises(Cancelled):
        Runner(FakeFetcher({BASE:toc([('第1章 起点',a)])}),tmp_path,c).run(BASE)
    assert load(next(tmp_path.glob('.novel-cache/*/toc.json')))['complete']
    assert load(next(tmp_path.glob('.novel-cache/*/manifest.json')))['status']=='stopped'


def test_banshanren_only_remove_comment_counters():
    url='https://www.banshanren.com/novel/demo/1'
    config=site_config(url)
    body=BeautifulSoup('<div class="chapter_content_box"><p>一共 12 人。<span class="z count_0">99</span></p></div>', 'html.parser').div
    assert preserve_text(body,config['remove_selector'])=='一共 12 人。'


def test_reject_challenge_and_unresolved_text():
    from novel_app.response import SuspectContent
    with pytest.raises(SuspectContent):
        guard_body(Page(BASE,'<div id="content">请完成验证后继续阅读</div>'),'#content')
    with pytest.raises(DownloadError):
        guard_body(Page(BASE,'<div id="content">未解码\ue011文字</div>'),'#content')


def test_partial_resume_reports_total_cached_count_before_missing_request(tmp_path,monkeypatch):
    import novel_app.core as core
    refs=[(f'第{i}章 测试',BASE+f'{i}.html') for i in range(1,61)]
    pages={BASE:toc(refs),**{url:chapter(title,PROSE+str(i)) for i,(title,url) in enumerate(refs)}}
    Runner(FakeFetcher(pages), tmp_path, Control()).run(BASE)
    snapshot=next(tmp_path.glob('.novel-cache/*/toc.json'))
    state=load(snapshot); state['chapters'].append({'title':'第61章 未完', 'url':BASE+'61.html'})
    core.save(snapshot,state); events=[]
    monkeypatch.setattr(core.time,'monotonic',lambda:100)
    class Missing(FakeFetcher):
        def get(self,url,referer=None):
            assert [e for e in events if e['kind']=='chapter'][-1]['downloaded']==60
            raise DownloadError('缺页')
    result=Runner(Missing({}),tmp_path,Control(lambda **e:events.append(e))).run(BASE)
    assert result['downloaded']==60 and result['status']=='incomplete'
    assert [e for e in events if e['kind']=='chapter'][-1]['downloaded']==60


def test_verified_41nr_paragraph_marker_preserved_but_unknown_or_inline_pua_rejected():
    url='https://m.41nr.com/chapter/28425/1.html'
    content=PROSE*5
    guard_body(Page(url,f'<div id="nr1">{content}<br><br>\ue4c6\ue4c6{content}</div>'),'#nr1')
    for body in (content+'\ue4c6'+content, '\ue011'+content, '\ufffd'+content):
        with pytest.raises(DownloadError): guard_body(Page(url,f'<div id="nr1">{body}</div>'),'#nr1')
    with pytest.raises(DownloadError): guard_body(Page(BASE,f'<div id="nr1">\ue4c6{content}</div>'),'#nr1')


def test_css_indent_and_source_indentation():
    from novel_app.sites import normalize_page
    page=normalize_page(Page('https://www.banshanren.com/novel/demo/1','<div class="chapter_content_box"><p>\n   原文第一段。\n <span class="z">7</span></p></div>'))
    body=BeautifulSoup(page.html,'html.parser').select_one('.chapter_content_box')
    assert preserve_text(body,'.z')=='\u3000\u3000原文第一段。'


@pytest.mark.parametrize('url',['file:///C:/Windows','http://localhost/','http://127.0.0.1/','http://192.168.1.1/','https://user:pass@example.org/','http://example.org:3306/'])
def test_reject_unsafe_urls(url):
    with pytest.raises(DownloadError): validate_url(url)


def test_preserve_download_order(tmp_path):
    a,b=BASE+'1.html',BASE+'2.html'
    result=Runner(FakeFetcher({BASE:toc([('第1章 起点',a),('第2章 远方',b)]),a:chapter('第1章 起点'),b:chapter('第2章 远方',PROSE+'终点')}),tmp_path,Control()).run(BASE)
    content=Path(result['output']).read_text('utf-8')
    assert content.index('第1章') < content.index('第2章')


def test_41nr_uses_chapter_heading_and_keeps_next_page():
    from novel_get.parse import extract_chapter
    url = 'https://m.41nr.com/chapter/28425/123.html'
    html = ('<h1>书名</h1><div id="nr_title">第001章 起点</div>'
            '<div id="nr"><div id="nr1">' + PROSE + '</div></div>'
            '<a href="123_2.html">下一页</a>')
    from novel_app.core import config_without_toc
    parsed = extract_chapter(Page(url, html), min_chars=40, **config_without_toc(site_config(url)))
    assert parsed.title == '第001章 起点'
    assert parsed.next_page == 'https://m.41nr.com/chapter/28425/123_2.html'
    assert '书名' not in parsed.text


def test_41nr_navigation_panel_removed_without_hiding_missing_page():
    from novel_app.core import config_without_toc
    from novel_app.sites import normalize_page
    from novel_get.parse import extract_chapter
    url = 'https://m.41nr.com/chapter/28425/123.html'
    html = ('<div id="nr_title">第001章 起点</div><div id="nr1">' + PROSE +
            '<br><br>第二段<div>本章未完，请点击下方 下一页 继续阅读</div></div>')
    config = config_without_toc(site_config(url))
    with pytest.raises(DownloadError):
        extract_chapter(normalize_page(Page(url,html)), min_chars=40, **config)
    parsed = extract_chapter(normalize_page(Page(url,html+'<a href="123_2.html">下一页</a>')), min_chars=40, **config)
    assert parsed.text == PROSE+'\n\n第二段'

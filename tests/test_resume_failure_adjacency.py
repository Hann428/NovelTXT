from pathlib import Path

import pytest
from novel_get.fetch import DownloadError
from novel_get.parse import Book, Ref

from novel_app.core import Runner
from novel_app.network import AccessDenied, Control
from novel_app.storage import load
from test_downloader import BASE, PROSE, FakeFetcher, toc, chapter


def test_sparse_old_holes_do_not_stop_resume_before_new_chapters(tmp_path):
    refs=[(f'第{i+1}章 测试',BASE+f'{i+1}.html') for i in range(10)]
    holes=(0,3,6)
    pages={BASE:toc(refs),**{url:chapter(title,PROSE+str(i)) for i,(title,url) in enumerate(refs)}}
    for i in holes:pages[refs[i][1]]=DownloadError(f'待核对旧章{i}')
    pages[refs[9][1]]=AccessDenied(403,refs[9][1])
    with pytest.raises(AccessDenied):Runner(FakeFetcher(pages),tmp_path,Control()).run(BASE)
    manifest=next(tmp_path.glob('.novel-cache/*/manifest.json'))
    assert load(manifest)['downloaded']==6
    retry=FakeFetcher({**{refs[i][1]:DownloadError('正文含未知字符') for i in holes},
                       refs[9][1]:chapter(refs[9][0],PROSE+'新章正常')})
    result=Runner(retry,tmp_path,Control()).run(BASE)
    assert retry.calls==[refs[i][1] for i in (*holes,9)]
    assert result['downloaded']==7 and result['status']=='incomplete' and len(result['failures'])==3
    text=Path(result['output']).read_text('utf-8')
    assert '新章正常' in text and '正文含未知字符' not in text
    assert text.index(refs[1][0])<text.index(refs[9][0])


@pytest.mark.parametrize('indices,stops', [([2,0,1],True),([0,4,7],False),([7,0,4],False)])
def test_stop_uses_toc_adjacency_even_when_workers_finish_out_of_order(tmp_path,indices,stops):
    refs=[Ref(f'第{i+1}章 测试',BASE+f'{i+1}.html') for i in range(9)]
    book=Book(url=BASE,title='测试小说',chapters=refs)
    runner=Runner(FakeFetcher({}),tmp_path,Control())
    runner.folder=tmp_path/'cache';runner.state={'input_url':BASE,'total':9,'downloaded':0}
    runner.chapter_results=lambda *_:iter((i,refs[i],None,False,DownloadError('正文含未知字符')) for i in indices)
    if stops:
        with pytest.raises(DownloadError,match='目录相邻三章'):runner.download(book,{})
    else:
        result=runner.download(book,{})
        assert result['status']=='incomplete' and len(result['failures'])==3

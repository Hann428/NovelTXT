from pathlib import Path
import pytest
from novel_app import core
from novel_app.network import Control
from novel_app.sites import clean_book_identity, get_book
from novel_get.fetch import Page
from test_downloader import FakeFetcher, BASE, PROSE, toc, chapter


@pytest.mark.parametrize('raw,title,author', [
    ('都市奇缘书包网最新章节目录_都市奇缘书包网(易天下)在线阅读 - 41男人小说','都市奇缘','易天下'),
    ('男欢女爱最新章节目录_男欢女爱(久石)在线阅读 - 41男人小说','男欢女爱','久石'),
    ('无作者小说最新章节目录_无作者小说在线阅读 - 41男人小说','无作者小说',''),
])
def test_41nr_cached_seo_names_clean_without_guessing_authors(raw,title,author):
    assert clean_book_identity(raw,url='https://m.41nr.com/book/28425/')==(title,author)


def test_unknown_site_legitimate_title_untouched():
    assert clean_book_identity('最新章节目录之谜',url=BASE)==('最新章节目录之谜','')


def fixture(tmp_path,title='都市奇缘',author='易天下',limit=0):
    url=BASE+'1.html'
    metadata=f'<meta property="og:novel:book_name" content="{title}"><meta property="og:novel:author" content="{author}">'
    pages={BASE:metadata+toc([('第1章 起点',url)]),url:chapter('第1章 起点',PROSE)}
    return core.Runner(FakeFetcher(pages),tmp_path,Control(),limit=limit).run(BASE)


@pytest.mark.parametrize('author,name', [('易天下','都市奇缘（易天下）.txt'), ('','都市奇缘.txt')])
def test_clean_export_name_optional_author_and_owned_resume(tmp_path,author,name):
    first=fixture(tmp_path,author=author)
    assert Path(first['output']).name==name and Path(first['output']).parent==tmp_path
    second=core.Runner(FakeFetcher({}),tmp_path,Control()).run(BASE)
    assert second['output']==first['output'] and second['author']==author
    assert len(list(tmp_path.glob('*.txt')))==1


def test_samples_use_clean_basename_in_separate_folder(tmp_path):
    sample=fixture(tmp_path,limit=1)
    full=core.Runner(FakeFetcher({}),tmp_path,Control()).run(BASE)
    assert Path(sample['output']).name==Path(full['output']).name=='都市奇缘（易天下）.txt'
    assert Path(sample['output']).parent==tmp_path/'试下载'
    assert Path(full['output']).parent==tmp_path


def test_existing_unrelated_or_user_edited_file_is_preserved(tmp_path):
    path=tmp_path/'都市奇缘（易天下）.txt'; path.write_text('用户自己的内容',encoding='utf-8')
    first=fixture(tmp_path)
    assert path.read_text('utf-8')=='用户自己的内容' and Path(first['output']).name==path.name
    assert Path(first['output']).parent!=tmp_path
    changed=Path(first['output']); changed.write_text('手工修改',encoding='utf-8')
    second=core.Runner(FakeFetcher({}),tmp_path,Control()).run(BASE)
    assert changed.read_text('utf-8')=='手工修改' and second['output']!=first['output']


def test_old_snapshot_gets_name_and_author_without_network(tmp_path):
    url='https://m.41nr.com/book/28425/'
    book=core.Book('都市奇缘书包网最新章节目录_都市奇缘书包网(易天下)在线阅读 - 41男人小说',url)
    runner=core.Runner(FakeFetcher({}),tmp_path,Control())
    runner.folder=tmp_path/'cache'
    core.save(runner.folder/'toc.json',{'version':core.PARSER_VERSION,'input_url':url,'book':core.asdict(book),
              'config':{},'queue':[],'seen':[url],'chapters':[{'title':'第1章 起点','url':url+'1.html'}],
              'complete':True,'created_at':'旧快照'})
    restored,_=runner.discover(url)
    assert (restored.title,restored.author)==('都市奇缘','易天下')

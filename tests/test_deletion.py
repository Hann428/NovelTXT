from pathlib import Path
import pytest
from novel_app.deletion import plan_delete, execute_delete, checked_path
from novel_app.storage import save,digest,JobLock

URL='https://example.org/book/1/'

def book(root,url=URL,name='同名书'):
    cache=root/'.novel-cache'/digest(url)[:16]
    save(cache/'manifest.json',{'input_url':url})
    save(cache/'chapters/1.json',{'text':'正文'})
    root.mkdir(parents=True,exist_ok=True)
    (root/(name+'.txt')).write_text('正文',encoding='utf-8')
    save(root/(name+'.report.json'),{'input_url':url})
    return cache

def test_delete_selected_book_and_samples_preserves_other_books(tmp_path):
    cache=book(tmp_path)
    other=book(tmp_path,'https://example.org/book/2/','同名书_第二本')
    sample=tmp_path/'阶段样本'
    sample.mkdir()
    (sample/'样本.txt').write_text('样本',encoding='utf-8')
    save(sample/'样本.report.json',{'url':URL})
    (tmp_path/'用户笔记.txt').write_text('保留',encoding='utf-8')
    plan=plan_delete(tmp_path,URL)
    assert len(plan.files)==4
    assert execute_delete(plan)==4
    assert not cache.exists()
    assert other.exists() and (tmp_path/'同名书_第二本.txt').exists()
    assert (tmp_path/'用户笔记.txt').exists()

def test_active_job_is_not_deleted(tmp_path):
    cache=book(tmp_path)
    with JobLock(cache):
        with pytest.raises(RuntimeError):
            plan_delete(tmp_path,URL)
    assert (tmp_path/'同名书.txt').exists()

def test_modified_file_requires_new_confirmation(tmp_path):
    book(tmp_path)
    plan=plan_delete(tmp_path,URL)
    (tmp_path/'同名书.txt').write_text('用户后来修改了正文',encoding='utf-8')
    with pytest.raises(RuntimeError,match='发生变化'):
        execute_delete(plan)
    assert (tmp_path/'同名书.txt').exists()

def test_report_output_path_cannot_delete_outside_or_unrelated_file(tmp_path):
    root=tmp_path/'output'
    book(root)
    outside=tmp_path/'private.txt'
    outside.write_text('保留',encoding='utf-8')
    save(root/'同名书.report.json',{'input_url':URL,'output':str(outside)})
    execute_delete(plan_delete(root,URL))
    assert outside.read_text('utf-8')=='保留'
    with pytest.raises(ValueError):
        checked_path(outside,root)

def test_job_started_after_confirmation_is_not_deleted(tmp_path):
    cache=book(tmp_path)
    plan=plan_delete(tmp_path,URL)
    with JobLock(cache):
        with pytest.raises(RuntimeError):
            execute_delete(plan)
    assert (cache/'chapters/1.json').exists()

def test_cache_symlink_is_rejected(tmp_path):
    cache=book(tmp_path/'output')
    other=tmp_path/'outside'
    other.mkdir()
    (other/'keep.txt').write_text('keep')
    try:
        (cache/'linked').symlink_to(other,target_is_directory=True)
    except OSError:
        pytest.skip('OS does not permit creating test symlinks')
    with pytest.raises(ValueError):
        plan_delete(tmp_path/'output',URL)
    assert (other/'keep.txt').exists()

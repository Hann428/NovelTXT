from novel_app.storage import JobLock
from novel_app.test_control import jobs, running, request_stop, resume_job


def test_stop_signals_only_active_job_without_killing_it(tmp_path):
    job=jobs(tmp_path)[0]
    assert not request_stop(job)
    assert not job['stop'].exists()
    with JobLock(job['folder']):
        assert running(job)
        assert request_stop(job)
        assert job['stop'].exists()
        assert running(job)
    assert not running(job)


def test_resume_does_not_launch_duplicate_or_clear_pending_stop(tmp_path):
    job=jobs(tmp_path)[0]
    with JobLock(job['folder']):
        request_stop(job)
        assert resume_job(tmp_path,job)=='正在运行，不重复启动'
        assert job['stop'].exists()

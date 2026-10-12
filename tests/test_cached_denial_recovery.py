import threading
import time

import pytest

from novel_app import browser_parallel, network
from novel_app.parallel import ChapterControl, SiteGate, TaskControl
from test_lightweight_browser import setup, FetchPage


URL = 'https://m.41nr.com/chapter/28425/5829167.html'
ERROR = '<title>403 Forbidden</title><h1>403 Forbidden</h1><hr>nginx'


def denied(url=URL, **changes):
    return dict(url=url, code=403, challenge=False, html=ERROR,
                cache_control='max-age=2678400', cache_status='HIT', **changes)


@pytest.mark.parametrize('change', [
    {}, {'code': 401}, {'code': 429}, {'challenge': True}, {'cache_status': None},
    {'cache_status': 'MISS'}, {'cache_control': 'no-cache'},
    {'cache_control': 'max-age=0'}, {'html': '<h1>403 Forbidden</h1><p>会员专享，请购买权限</p>'},
    {'html': '<title>登录</title><form><input type="password"></form>'},
    {'url': 'https://other.example/chapter/1'},
])
def test_only_confirmed_cached_plain_denial_is_eligible(change):
    data = denied(); data.update(change)
    assert browser_parallel.recoverable_cached_denial(data, URL) == (not change)
    assert not browser_parallel.recoverable_cached_denial(denied(url='https://other.example/book/1'), 'https://other.example/book/1')


def recovery_setup(monkeypatch, failures=1, cooldown=0, retry_after='0'):
    broker, context, task, gate, events = setup(monkeypatch)
    monkeypatch.setattr(browser_parallel, 'CACHED_DENIAL_HOSTS', {'a.example'})
    monkeypatch.setattr(browser_parallel, 'cached_denial_delay', lambda attempt: cooldown)
    original = FetchPage.evaluate
    async def evaluate(self, script, url=None):
        data = await original(self, script, url)
        if url is not None and len(context.fetches) <= failures:
            data.update(denied(url), retry_after=retry_after)
        return data
    monkeypatch.setattr(FetchPage, 'evaluate', evaluate)
    return broker, context, task, gate, events


def test_cached_denial_recovers_in_background_without_manual_or_navigation(monkeypatch):
    broker, context, task, gate, events = recovery_setup(monkeypatch)
    try:
        result = broker.request('a', 'unused', ChapterControl(task, 'book:1'), gate,
                                task.url+'1.html', transport_key='book')
        assert '第1章' in result.html and len(context.fetches) == 2
        assert context.navigations == [task.url]
        assert any(e.get('status') == '访问冷却' for e in events)
        assert not any(e['kind'] == 'manual' for e in events)
        assert gate.restricted and gate.limit == 1 and not gate.rate_recovering and not gate.inflight
    finally:
        task.cancel.set(); broker.close()


def test_persistent_cached_denial_has_only_two_retries(monkeypatch):
    broker, context, task, gate, events = recovery_setup(monkeypatch, failures=100)
    try:
        with pytest.raises(network.AccessDenied):
            broker.request('a', 'unused', ChapterControl(task, 'book:1'), gate,
                           task.url+'1.html', transport_key='book')
        assert len(context.fetches) == 3
        assert gate.blocked and not gate.rate_recovering and not gate.inflight
        assert not any(e['kind'] == 'manual' for e in events)
    finally:
        task.cancel.set(); broker.close()


def test_long_server_retry_after_is_not_ignored(monkeypatch):
    broker, context, task, gate, events = recovery_setup(monkeypatch, retry_after='3600')
    try:
        with pytest.raises(network.AccessDenied):
            broker.request('a', 'unused', ChapterControl(task, 'book:1'), gate,
                           task.url+'1.html', transport_key='book')
        assert len(context.fetches) == 1 and not gate.rate_recovering
    finally:
        task.cancel.set(); broker.close()


def test_cancel_cooldown_and_other_site_runs(monkeypatch):
    broker, context, task, gate, events = recovery_setup(monkeypatch, cooldown=90)
    errors = []
    def run():
        try:
            broker.request('a', 'unused', ChapterControl(task, 'book:1'), gate,
                           task.url+'1.html', transport_key='book')
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    try:
        thread.start(); deadline = time.monotonic()+2
        while not gate.rate_recovering and time.monotonic()<deadline:
            time.sleep(.01)
        assert gate.rate_recovering and len(context.fetches)==1
        other = TaskControl(task.parent, 'other', 'https://b.example/book/')
        assert '可读正文' in broker.get('b', 'unused', other, SiteGate('b', task.parent, delay=0), other.url).html
        task.cancel.set(); thread.join(1)
        assert not thread.is_alive() and isinstance(errors[0], network.Cancelled)
        assert not gate.rate_recovering
    finally:
        task.cancel.set(); thread.join(2); broker.close()


def test_production_backoff_does_not_poll_continuously():
    assert [browser_parallel.cached_denial_delay(i) for i in range(2)] == [60, 120]

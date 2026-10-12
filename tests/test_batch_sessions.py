import queue
from pathlib import Path

import pytest

import novel_app.gui as gui
import novel_app.network as network
from novel_get.fetch import DownloadError, SiteBlocked


@pytest.fixture
def opened(monkeypatch):
    instances = []

    class FakeFetcher:
        def __init__(self, control, **options):
            self.control, self.options = control, options
            self.closed = 0
            self.verified = False
            self.tab = object()
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed += 1

    monkeypatch.setattr(network, 'Fetcher', FakeFetcher)
    return instances


def test_hundred_same_site_jobs_keep_one_verified_session(tmp_path, opened):
    confirmations = 0
    with network.FetcherPool(network.Control()) as pool:
        for index in range(100):
            session, reused = pool.get(mode='browser', profile=tmp_path / 'site', delay=3)
            if not session.verified:
                confirmations += 1
                session.verified = True
            assert reused == (index > 0)
            assert session.tab is opened[0].tab
            assert session.closed == 0
    assert confirmations == 1
    assert len(opened) == 1 and opened[0].closed == 1


def test_pool_keeps_sites_separate_and_limits_live_browsers(tmp_path, opened):
    with network.FetcherPool(network.Control(), max_sessions=2) as pool:
        first, _ = pool.get(profile=tmp_path / 'a')
        second, _ = pool.get(profile=tmp_path / 'b')
        assert first is not second
        assert pool.get(profile=tmp_path / 'a') == (first, True)
        pool.get(profile=tmp_path / 'c')
        assert second.closed == 1 and first.closed == 0
        assert len(pool.sessions) == 2
    assert all(session.closed == 1 for session in opened)


def test_cancel_closes_all_sessions(tmp_path, opened):
    with pytest.raises(network.Cancelled):
        with network.FetcherPool(network.Control()) as pool:
            pool.get(profile=tmp_path / 'a')
            pool.get(profile=tmp_path / 'b')
            raise network.Cancelled('stop')
    assert all(session.closed == 1 for session in opened)


def app_for_batch(tmp_path):
    app = gui.App.__new__(gui.App)
    app.home, app.data = tmp_path, tmp_path / 'data'
    app.events = queue.Queue()
    app.control = network.Control(lambda **event: app.events.put(event))
    return app


def options(tmp_path):
    return dict(delay=3, pause_every=30, pause_seconds=15, mode='自动选择',
                proxy=False, refresh=False, output=tmp_path / 'downloads')


def test_batch_reuses_session_even_after_first_book_parse_error(tmp_path, opened, monkeypatch):
    app = app_for_batch(tmp_path)
    runs = []

    class FakeRunner:
        def __init__(self, fetcher, *_args, **_kwargs):
            self.fetcher = fetcher

        def run(self, url):
            runs.append((url, self.fetcher))
            assert self.fetcher.closed == 0
            if len(runs) == 1:
                self.fetcher.verified = True
                raise DownloadError('first book parse error')
            assert self.fetcher.verified

    monkeypatch.setattr(gui, 'Runner', FakeRunner)
    app.work([('1', 'https://m.41nr.com/book/28425/'),
              ('2', 'https://m.41nr.com/book/20091/')], 'sample', options(tmp_path))
    assert len(runs) == 2 and runs[0][1] is runs[1][1]
    assert len(opened) == 1 and opened[0].closed == 1
    events = list(app.events.queue)
    assert [e['reused'] for e in events if e['kind'] == 'session'] == [False, True]
    assert events[-1]['kind'] == 'idle'


def test_known_aliases_share_profile_but_unrelated_hosts_do_not(tmp_path):
    app = app_for_batch(tmp_path)
    assert app.browser_profile('m.41nr.com') == app.browser_profile('www.41nr.com')
    assert app.browser_profile('banshanren.com') == app.browser_profile('www.banshanren.com')
    assert app.browser_profile('example.org') != app.browser_profile('other.example.org')
    assert gui.session_site('fake41nr.com') == 'fake41nr.com'
    legacy = app.data / 'browser-profile'
    legacy.mkdir(parents=True)
    assert app.browser_profile('m.41nr.com') == legacy


def test_verification_and_normal_download_use_same_browser_kind(tmp_path, opened, monkeypatch):
    app = app_for_batch(tmp_path)
    app.browser_profile('www.banshanren.com').mkdir(parents=True)
    monkeypatch.setattr(gui, 'Runner', lambda *_a, **_k: type('Runner', (), {'run': lambda self, url: None})())
    jobs = [('1', 'https://www.banshanren.com/novel/test')]
    app.work(jobs, 'verify', options(tmp_path))
    app.work(jobs, 'download', options(tmp_path))
    assert len(opened) == 2
    for session in opened:
        assert session.options['headless'] is False
        assert session.options['manual'] is True
        assert session.options['mode'] == 'browser'
    assert opened[0].options['profile'] == opened[1].options['profile']


def test_blocked_site_skips_remaining_books_without_blocking_other_sites(tmp_path, opened, monkeypatch):
    app = app_for_batch(tmp_path)
    runs = []

    class FakeRunner:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, url):
            runs.append(url)
            if '41nr.com' in url:
                raise SiteBlocked('limited')

    monkeypatch.setattr(gui, 'Runner', FakeRunner)
    app.work([('1', 'https://m.41nr.com/book/1/'),
              ('2', 'https://www.41nr.com/book/2/'),
              ('3', 'https://example.org/book/3/')], 'sample', options(tmp_path))
    assert len(runs) == 2 and runs[-1] == 'https://example.org/book/3/'
    assert all(session.closed == 1 for session in opened)

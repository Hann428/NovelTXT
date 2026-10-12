import httpx
import pytest
from novel_get.fetch import SiteBlocked, DownloadError
import novel_app.network as network


def fake_fetcher(monkeypatch, handler):
    monkeypatch.setattr(network, 'validate_url', lambda url,resolve=False: url)
    f=network.Fetcher(network.Control())
    f.client.close()
    f.client=httpx.Client(transport=httpx.MockTransport(handler))
    f.pace=lambda: None
    f.control.wait=lambda *args,**kwargs: None
    return f


def test_403_not_retried(monkeypatch):
    calls=[]
    def handler(req):
        calls.append(req.url)
        return httpx.Response(403,text='<h1>Forbidden</h1>')
    with fake_fetcher(monkeypatch,handler) as f:
        with pytest.raises(network.AccessDenied): f.get('https://example.org/book/')
    assert len(calls)==1


@pytest.mark.parametrize('status', [None, 200, 403])
def test_plain_403_has_no_human_action(status):
    page=network.Page('https://example.org/chapter', '<title>403 Forbidden</title><h1>403 Forbidden</h1><hr>nginx')
    with pytest.raises(network.AccessDenied) as error:
        network.check_page_response(page,status)
    assert error.value.status == 403 and error.value.url == page.url


@pytest.mark.parametrize('html,challenge', [('<title>403 Forbidden</title>',True),
    ('<title>Just a moment...</title>',False),
    ('<form id="captcha-form"></form>',False),
    ('<form><input type="password"></form>',False)])
def test_real_challenge_or_login_on_403_still_requires_manual_action(html,challenge):
    with pytest.raises(SiteBlocked) as error:
        network.check_page_response(network.Page('https://example.org/chapter',html),403,challenge)
    assert not isinstance(error.value, network.AccessDenied)


def test_chapter_mentioning_403_is_not_an_access_error():
    network.check_page_response(network.Page('https://example.org/chapter',
        '<title>第403章 Forbidden</title><h1>第403章 禁地</h1><p>屏幕显示403 Forbidden。</p>'),200)
    network.check_page_response(network.Page('https://example.org/chapter',
        '<title>正常章节</title><p>正文</p><form hidden><input type="password"></form>'
        '<div class="cf-turnstile">评论区验证码</div>'),200)


def test_raw_http_403_challenge_header_not_lost(monkeypatch):
    with fake_fetcher(monkeypatch,lambda req:httpx.Response(403,text='<title>请稍候</title>',
                      headers={'cf-mitigated':'challenge'})) as f:
        with pytest.raises(SiteBlocked) as error: f.get('https://example.org/book/')
    assert not isinstance(error.value,network.AccessDenied)


@pytest.mark.parametrize('status', [404, 410])
def test_missing_http_resource_not_retried_or_classified_as_verification(monkeypatch, status):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, text='<title>404 Not Found</title>')
    with fake_fetcher(monkeypatch, handler) as fetcher:
        with pytest.raises(network.PageHTTPError) as error:
            fetcher.get('https://example.org/book/missing')
    assert not isinstance(error.value, SiteBlocked) and error.value.status == status
    assert len(calls) == 1


@pytest.mark.parametrize('status', [None, 200, 404, 410, 500, 502])
def test_error_page_status_without_challenge_is_not_verification(status):
    page=network.Page('https://example.org/missing', '<title>404 Not Found</title><h1>404 Not Found</h1>')
    with pytest.raises(network.PageHTTPError):
        network.check_page_response(page, status)


def test_error_detection_preserves_genuine_challenge_and_chapter_404():
    url='https://example.org/chapter'
    network.check_page_response(network.Page(url, '<h1>第404章 失落的钥匙</h1><p>404 Not Found</p>'), 200)
    with pytest.raises(SiteBlocked):
        network.check_page_response(network.Page(url, '<title>Just a moment...</title>'), 200)
    with pytest.raises(SiteBlocked):
        network.check_page_response(network.Page(url, '<title>错误页</title>'), 403, True)


def test_cross_host_redirect_not_followed(monkeypatch):
    calls=[]
    def handler(req):
        calls.append(req.url)
        return httpx.Response(302,headers={'location':'http://127.0.0.1/'})
    with fake_fetcher(monkeypatch,handler) as f:
        with pytest.raises(DownloadError): f.get('https://example.org/book/')
    assert len(calls)==1


def test_transient_failure_recovers(monkeypatch):
    calls=[]
    def handler(req):
        calls.append(req.url)
        if len(calls)==1: return httpx.Response(503)
        return httpx.Response(200,text='<title>正常页面</title>',headers={'content-type':'text/html; charset=utf-8'})
    with fake_fetcher(monkeypatch,handler) as f:
        assert '正常页面' in f.get('https://example.org/book/').html
    assert len(calls)==2


def test_200_challenge_rejected(monkeypatch):
    with fake_fetcher(monkeypatch,lambda req:httpx.Response(200,text='<title>Just a moment...</title>')) as f:
        with pytest.raises(SiteBlocked): f.get('https://example.org/book/')


def test_localized_challenge_rejected(monkeypatch):
    with fake_fetcher(monkeypatch,lambda req:httpx.Response(200,text='<title>请稍候…</title>')) as f:
        with pytest.raises(SiteBlocked): f.get('https://example.org/book/')


def test_pacing_uses_configured_interval_and_periodic_rest(monkeypatch):
    monkeypatch.setattr(network.time,'monotonic',lambda:100)
    monkeypatch.setattr(network.random,'uniform',lambda *_:0)
    waits=[]
    with network.Fetcher(network.Control(),delay=3,pause_every=30,pause_seconds=15) as f:
        f.control.wait=lambda seconds,*args,**kwargs:waits.append(seconds)
        f.last=100
        f.count=15
        f.pace()
        f.count=30
        f.pace()
    assert waits==[3,15]

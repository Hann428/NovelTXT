"""Evidence-based response classification shared by transport and extraction."""
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from novel_get.fetch import DownloadError, SiteBlocked


class PageHTTPError(DownloadError):
    def __init__(self, status, url):
        self.status, self.url = status, url
        reason = {404:'页面不存在，该章待重试', 410:'页面已移除，该章待重试',
                  204:'页面没有内容，该章待重试', 206:'只收到部分内容，未确认完整性',
                  304:'没有可用的缓存响应正文', 408:'请求超时，该章待重试'}.get(status, '页面读取失败，该章待重试')
        super().__init__(f'HTTP {status}：{reason}；{url}')


class StopWithoutVerification(SiteBlocked):
    label = '站点已暂停'
    def __init__(self, message, status=None, url=None):
        self.status, self.url = status, url
        super().__init__(message)


class AccessDenied(StopWithoutVerification):
    label = '访问被拒绝'
    def __init__(self, status, url):
        super().__init__(f'HTTP {status}：网站拒绝访问，未发现可处理的登录或真人验证；'
                         f'已暂停本站并保留断点，请稍后继续；{url}', status, url)


class RateLimited(StopWithoutVerification):
    label = '持续限流'


class ServerUnavailable(StopWithoutVerification):
    label = '服务器暂不可用'


class PermissionRequired(StopWithoutVerification):
    label = '权限或订阅限制'


class ProxyAuthenticationRequired(StopWithoutVerification):
    label = '代理需要认证'


class LoginRequired(SiteBlocked):
    label = '需要登录'


class VerificationRequired(SiteBlocked):
    label = '需要真人验证'


class SuspectContent(DownloadError):
    """Never cache/export an unconfirmed placeholder as a successful chapter."""


def blocked_status(error):
    return getattr(error, 'label', '需要登录/验证')


def visible_soup(html):
    soup = BeautifulSoup(html, 'html.parser')
    for node in list(soup.select('script, style, noscript, template, [hidden], [aria-hidden="true"]')):
        if node.parent is not None:
            node.decompose()
    for node in list(soup.select('[style]')):
        if node.attrs is not None and re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', node.get('style',''), re.I):
            node.decompose()
    return soup


def inspect_body(page, body):
    if body is None:
        return
    # Heading and navigation text are not the chapter's prose.
    copy = visible_soup(str(body))
    for node in copy.select('h1, h2, h3, [data-novel-navigation]'):
        node.decompose()
    text = copy.get_text(' ', strip=True)
    gate = re.match(r'^(?:温馨提示[:：]?\s*)?(?:本章(?:节)?|当前(?:页面|章节)|本页面|请(?:先|完成|通过)|您(?:尚未|需要)|需要|订阅后|验证后|登录后)', text)
    if copy.select_one('form#captcha-form, #captcha_btn') and (len(text) < 40 or (len(text) <= 400 and gate)):
        raise VerificationRequired(f'正文包含可操作验证；请完成后返回原章节：{page.url}')
    if len(text) > 400:
        return
    if re.fullmatch(r'(?:提示[:：]?\s*)?(?:访问|请求)过于频繁[\s，,。.!！]*(?:请稍后(?:再试|重试|访问)[\s，,。.!！]*)?', text):
        raise RateLimited(f'网站提示访问频繁，本站已暂停；保留断点，请稍后继续：{page.url}', 429, page.url)
    if gate and re.search(r'(?:订阅|购买|付费|会员专享|VIP).{0,12}(?:阅读|查看|解锁)|(?:阅读|查看).{0,12}(?:订阅|购买|付费)', text, re.I):
        raise PermissionRequired(f'章节要求权限或订阅，已暂停并保留断点；不会等待真人验证：{page.url}', url=page.url)
    if gate and re.search(r'(?:登录|登入).{0,12}(?:阅读|继续|查看)|(?:阅读|继续|查看).{0,12}(?:登录|登入)', text):
        raise LoginRequired(f'正文要求登录；请正常登录后返回原章节：{page.url}')
    # Plain text without an actual challenge is not proof of a solvable CAPTCHA.
    if gate and re.search(r'(?:完成|通过).{0,8}验证|验证后.{0,6}(?:阅读|继续)', text):
        raise SuspectContent(f'页面提示验证但没有识别到可操作控件，待核对；未保存为正文：{page.url}')
    if re.match(r'^(?:系统|网站|服务器|本站)(?:正在)?维护', text) and re.search(r'暂时(?:无法|不可用)|稍后(?:再次|再)?(?:访问|重试)|维护中', text):
        raise SuspectContent(f'页面为维护提示，待重试；未保存为正文：{page.url}')
    if re.match(r'^(?:正文|章节|页面|内容)(?:正在)?加载中', text):
        raise SuspectContent(f'正文尚未恢复可读，页面仍为加载提示：{page.url}')


def check_page_response(page, status=None, challenge=False):
    if challenge:
        raise VerificationRequired(f'网站返回真人验证页面：{page.url}')
    soup = visible_soup(page.html)
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    body = soup.select_one('#nr1, .chapter_content_box, #content, #chaptercontent, .chapter-content')
    has_prose = bool(body and len(body.get_text(strip=True)) >= 40)
    challenge_title = bool(re.fullmatch(r'(?:just a moment[\s.…!]*|(?:安全|人机|身份)验证|请稍候[.…。]*)', title, re.I))
    challenge_widget = soup.select_one('form#captcha-form, #captcha_btn, .cf-turnstile, .g-recaptcha, iframe[src*="challenges.cloudflare.com"], iframe[src*="captcha"]')
    password = soup.select_one('form input[type="password"]')
    # Real controls on a dedicated access page; normal comment widgets do not gate prose.
    if not has_prose and (soup.select_one('form#captcha-form, #captcha_btn') or
                          (challenge_widget and (challenge_title or status in {401,403}))):
        raise VerificationRequired(f'网页要求真人验证：{page.url}')
    login_title = re.fullmatch(r'(?:用户|会员|账号)?(?:登录|登入)|(?:sign in|log in|login)', title, re.I)
    if not has_prose and password and (login_title or status in {401,403}):
        raise LoginRequired(f'登录页面或会话失效；请登录后返回原章节：{page.url}')
    if status == 429:
        raise RateLimited(f'HTTP 429：访问频繁，本站已暂停；保留断点，请稍后继续：{page.url}', status, page.url)
    if status == 407:
        raise ProxyAuthenticationRequired(f'HTTP 407：代理需要认证，请检查代理配置；不是小说站真人验证：{page.url}', status, page.url)
    if status == 451:
        raise PermissionRequired(f'HTTP 451：资源因法律原因不可用，已暂停并保留断点：{page.url}', status, page.url)
    if status in {401,403}:
        # Dedicated legacy challenge titles remain supported, without matching chapter titles.
        if challenge_title and not has_prose:
            raise VerificationRequired(f'网页要求真人验证：{page.url}')
        raise AccessDenied(status, page.url)
    if (status and status >= 400) or status in {204,206,304}:
        raise PageHTTPError(status, page.url)
    if challenge_title and not has_prose:
        raise VerificationRequired(f'网页要求真人验证：{page.url}')
    for heading in (soup.title, soup.find('h1')):
        label = heading.get_text(' ', strip=True) if heading else ''
        if re.fullmatch(r'(?:(?:HTTP\s*)?403(?:\s*[-:：]?\s*forbidden)?|forbidden|access denied|访问被拒绝|拒绝访问)(?:\s*[|–-]\s*cloudflare)?', label, re.I):
            raise AccessDenied(403, page.url)
        if re.fullmatch(r'(?:404(?:\s*[-:：]?\s*(?:not\s+found|page\s+not\s+found))?|页面不存在|页面未找到|找不到页面)', label, re.I):
            raise PageHTTPError(404, page.url)
        match = re.fullmatch(r'(500|502|503|504)(?:\s*[-:：]?\s*(?:internal server error|bad gateway|service unavailable|gateway timeout))?', label, re.I)
        if match:
            raise PageHTTPError(int(match[1]), page.url)
    inspect_body(page, body)


def check_destination(page, requested):
    """Permit same-origin query/trailing-slash normalization; parsing checks chapter identity."""
    source, target = urlsplit(requested), urlsplit(page.url)
    if (source.scheme, source.netloc.lower(), source.path.rstrip('/')) == (target.scheme, target.netloc.lower(), target.path.rstrip('/')):
        return
    # check_page_response must run first: an actual login/challenge can be interactive.
    raise DownloadError(f'网页跳转离开原章节，待核对目标地址；不是已确认的真人验证：{page.url}')

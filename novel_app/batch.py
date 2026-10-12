"""Batch input and speed presets shared by the desktop interface."""
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SPEEDS = {'稳妥': (8, 15, 60), '均衡': (3, 30, 15), '较快': (1, 50, 10)}


def parse_urls(text):
    text = re.sub(r'[,;](?=https?://)', '\n', text, flags=re.I)
    result = []
    for match in re.finditer(r'https?://[^\s<>"\'，。；、（）【】]+', text, re.I):
        url = match.group().rstrip('.,;!]}')
        while url.endswith(')') and url.count(')') > url.count('('):
            url = url[:-1]
        try:
            parts = urlsplit(url)
            query = parts.query
            if any(key in {'__cf_chl_rt_tk','__cf_chl_tk'} for key,_ in parse_qsl(query)):
                query = urlencode([(k,v) for k,v in parse_qsl(query,keep_blank_values=True)
                                   if k not in {'__cf_chl_rt_tk','__cf_chl_tk'}])
            path = parts.path
            if parts.hostname in {'m.41nr.com','www.41nr.com','41nr.com'} and re.fullmatch(r'/book/\d+/?',path):
                path = path.rstrip('/')+'/'
            url = urlunsplit((parts.scheme.lower(),parts.netloc.lower(),path,query,''))
        except ValueError:
            pass  # Keep malformed URLs so the caller can explain the validation error.
        if url not in result:
            result.append(url)
    return result

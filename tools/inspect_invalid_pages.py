"""Three bounded same-session reads; diagnostic HTML only, no download cache writes."""
import json
import sys
import time
import threading
import tkinter as tk
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT), str(ROOT/'vendor/Novel-Get/src')]
from novel_app.webview2 import NativeSession
from novel_app.browser_parallel import LIGHTWEIGHT_GET
from novel_app.storage import save

home=ROOT/'evidence/invalid-page-diagnostics'; home.mkdir(exist_ok=True)
root=tk.Tk(); root.title('逐页 · 正文异常只读检查'); root.geometry('940x650')
root.update()
profile=ROOT/'evidence/webview2-prototype/profiles/m.41nr.com'
session=NativeSession(root.winfo_id(), profile)
result={'stage':'running', 'pages':[], 'updated':time.time()}; finished=threading.Event()
def run():
    try:
        session.request('new_page', page='main')
        session.request('navigate', page='main', url='https://m.41nr.com/book/28425/')
        seed=session.evaluate('document.title')
        if 'moment' in seed.lower() or '验证' in seed: raise RuntimeError('网站再次要求验证，诊断停止')
        for chapter in ('5824303','5826142','5824712_2'):
            time.sleep(5)
            url=f'https://m.41nr.com/chapter/28425/{chapter}.html'
            data=session.evaluate('('+LIGHTWEIGHT_GET+')('+json.dumps(url)+')')
            if data.get('error'): raise RuntimeError(data['error'])
            html=data['html']; (home/(chapter+'.html')).write_text(html, encoding='utf-8')
            soup=BeautifulSoup(html,'html.parser'); body=soup.select_one('#nr1'); hits=[]
            if body:
                for node in body.find_all(string=True):
                    chars=[f'U+{ord(c):04X}' for c in str(node) if c=='\ufffd' or '\ue000'<=c<='\uf8ff']
                    if chars:
                        hits.append({'chars':chars, 'tag':node.parent.name, 'class':node.parent.get('class'),
                                     'context':str(node)[:140], 'parent':str(node.parent)[:450]})
            result['pages'].append({'url':url, 'http':data['code'], 'challenge':data['challenge'],
                                    'title':soup.title.get_text() if soup.title else '', 'hits':hits})
            result['updated']=time.time(); save(home/'progress.json',result)
            if data['challenge'] or data['code'] in (403,429): raise RuntimeError('网站访问受限，诊断停止')
        result['stage']='complete'
    except Exception as exc: result.update(stage='failed',error=repr(exc))
    finally:
        session.close(); result['updated']=time.time(); save(home/'progress.json',result); finished.set()
threading.Thread(target=run,daemon=True).start()
def poll():
    if finished.is_set(): root.destroy()
    else: root.after(250,poll)
root.after(250,poll); root.mainloop()
print(json.dumps(result, ensure_ascii=True), flush=True)
sys.exit(0 if result['stage']=='complete' else 1)

"""Read-only audit of source caches; persists a compact acceptance status."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
URLS=['https://m.41nr.com/book/28425/','https://www.banshanren.com/novel/cunsemanyuan']
def read(path):
    try: return json.loads(path.read_text('utf-8'))
    except (OSError,ValueError): return None

results=[]
for url in URLS:
    key=hashlib.sha256(url.encode()).hexdigest()[:16]
    candidates=[ROOT/'下载'/'.novel-cache'/key,ROOT/'evidence/live-download/.novel-cache'/key]
    matches=[(p,read(p/'manifest.json')) for p in candidates]
    matches=[(p,s) for p,s in matches if s]
    if not matches:
        results.append(dict(url=url,status='not_started',downloaded=0,total=None,passed=False))
        continue
    folder,state=max(matches,key=lambda pair:pair[1].get('updated_at',''))
    errors=[]
    toc=read(folder/'toc.json') or {}
    toc_urls=[r['url'] for r in toc.get('chapters',[])]
    records=[read(p) for p in (folder/'chapters').glob('*.json')]
    records=[r for r in records if r and r.get('version')==state.get('version')]
    checked=0
    for record in records:
        if hashlib.sha256(record['text'].encode()).hexdigest()!=record['sha256']:
            errors.append('chapter_hash_mismatch:'+record['url'])
        if 'banshanren.com' in url:
            expected=[]
            for page_url in record['pages']:
                raw=read(folder/'pages'/(hashlib.sha256(page_url.encode()).hexdigest()+'.json'))
                if not raw:
                    errors.append('raw_page_missing:'+page_url)
                    continue
                soup=BeautifulSoup(raw['html'],'html.parser')
                body=soup.select_one('.chapter_content_box')
                if not body:
                    errors.append('source_body_missing:'+page_url)
                    continue
                for counter in body.select('span.z,span.nz'):
                    counter.decompose()
                # Independently compare all paragraph characters, ignoring only layout whitespace.
                expected.extend(p.get_text() for p in body.find_all('p'))
            source=re.sub(r'\s+','', ''.join(expected))
            actual=re.sub(r'\s+','', record['text'])
            if source != actual:
                errors.append('source_character_mismatch:'+record['url'])
            elif not record['text'].startswith('\u3000\u3000'):
                errors.append('indentation_missing:'+record['url'])
            else:
                checked+=1
        elif '41nr.com' in url:
            expected=[]
            for page_url in record['pages']:
                raw=read(folder/'pages'/(hashlib.sha256(page_url.encode()).hexdigest()+'.json'))
                if not raw:
                    errors.append('raw_page_missing:'+page_url)
                    continue
                body=BeautifulSoup(raw['html'],'html.parser').select_one('#nr1')
                if body is None:
                    errors.append('source_body_missing:'+page_url)
                    continue
                for node in body.select('script,style,noscript,iframe,[hidden]'):
                    node.decompose()
                for panel in body.find_all('div',recursive=False):
                    label=re.sub(r'\s+','',panel.get_text())
                    if label in {'本章未完，请点击下方下一页继续阅读','本章已完，请点击下方下一章翻阅新篇'}:
                        panel.decompose()
                expected.append(body.get_text())
            if re.sub(r'\s+','',''.join(expected)) != re.sub(r'\s+','',record['text']):
                errors.append('source_character_mismatch:'+record['url'])
            else:
                checked+=1
    output=state.get('output')
    txt=None
    if output:
        path=Path(output)
        if not path.is_absolute(): path=ROOT/path
        if path.exists():
            txt=path.read_text('utf-8')
            for record in records:
                if record['text'].replace('\r\n','\n') not in txt:
                    errors.append('export_content_missing:'+record['url'])
        else: errors.append('output_file_missing')
    covered={r['url'] for r in records}
    missing=[u for u in toc_urls if u not in covered]
    passed=state['status']=='complete' and toc.get('complete') and not missing and not errors and txt is not None and not state.get('issues') and not state.get('failures')
    results.append(dict(url=url,title=state.get('title'),status=state['status'],downloaded=len(records),total=len(toc_urls),
                        snapshot_at=toc.get('created_at'),updated_at=state.get('updated_at'),passed=bool(passed),
                        audited_against_source=checked,missing=len(missing),errors=errors,issues=state.get('issues',[]),output=output,
                        manifest=str(folder/'manifest.json')))
result={'updated_at':datetime.now().astimezone().isoformat(timespec='seconds'),'required':2,'passed':sum(r['passed'] for r in results),'books':results}
(ROOT/'evidence/acceptance-progress.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
lines=['# 下载完整性记录','',f"更新时间：{result['updated_at']}",'',
       '用户于 2026-09-30 调整验收范围：当前阶段不要求下载完整两本，保留可选全本完整性检查。',
       '阶段样本为 41nr 35 条目（70 页）和搬山人 169 章；用户已确认排版并要求保持。',
       f"已通过全本完整性检查的下载：{result['passed']}/2（仅表示文件范围，不作为当前工具阶段验收结果）。",'']
for r in results:
    lines.extend([f"## {r.get('title') or r['url']}",'',f"- 地址：{r['url']}",f"- 状态：{r['status']}",f"- 章节缓存：{r['downloaded']}/{r.get('total') or '未知'}",f"- 全量验收：{'通过' if r['passed'] else '未通过 / 尚未完成'}",f"- 独立原文字符复核：{r.get('audited_against_source',0)} 章",f"- 复核异常：{len(r.get('errors',[]))}",''])
(ROOT/'验收记录.md').write_text('\n'.join(lines),'utf-8')
print(json.dumps(result,ensure_ascii=True))

"""Export complete cached chapters without network access or modifying the cache."""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'vendor/Novel-Get/src')]
from novel_app.core import Runner
from novel_app.network import Control
from novel_app.sites import site_config
from novel_app.storage import digest,load,save,write_text,JobLock
from novel_app.test_control import jobs
from novel_get.parse import Book,Ref
from novel_get.fetch import DownloadError
from novel_get.engine import safe_name

class Offline:
    def get(self,url,referer=None):
        raise DownloadError('Offline export requires missing page: '+url)

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    output=ROOT/'下载/阶段样本'
    output.mkdir(parents=True,exist_ok=True)
    reports=[]
    for job in jobs(ROOT):
        with JobLock(job['folder']):
            toc=load(job['folder']/'toc.json')
            book=Book(**{k:v for k,v in toc['book'].items() if k!='chapters'})
            book.chapters=[Ref(**r) for r in toc['chapters']]
            book.toc_pages=toc['seen']
            config={**toc['config'],**site_config(job['url'])}
            runner=Runner(Offline(),output,Control())
            runner.folder=job['folder']
            records=[]
            for ref in book.chapters:
                cached=load(job['folder']/'chapters'/(digest(ref.url)+'.json'))
                if not cached:
                    break  # Only export the continuous completed prefix.
                if digest(cached['text'])!=cached['sha256']:
                    raise RuntimeError('Cached chapter hash mismatch: '+ref.url)
                records.append(runner.chapter(ref,book,config))
                if len(records)%20==0:
                    print(json.dumps({'title':book.title,'checked_chapters':len(records)},ensure_ascii=False),flush=True)
            stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
            path=output/f'{safe_name(book.title)}_阶段样本_{len(records)}章_{stamp}.txt'
            text=book.title+'\n\n'+'\n\n'.join(r['title']+'\n\n'+r['text'] for r in records)+'\n'
            write_text(path,text.replace('\n','\r\n'))
            report=dict(status='stage_sample',title=book.title,url=job['url'],chapters=len(records),
                        directory_entries=len(book.chapters),pages=sum(len(r['pages']) for r in records),
                        output=str(path),created_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                        sha256=digest(text),scope='已缓存的连续完整章节，非全本；无网络离线重新解析',
                        records=[{k:v for k,v in r.items() if k!='text'} for r in records])
            save(path.with_suffix('.report.json'),report)
            reports.append({k:v for k,v in report.items() if k!='records'})
            print(json.dumps(reports[-1],ensure_ascii=False),flush=True)
    save(ROOT/'evidence/stage-samples.json',reports)

if __name__=='__main__':
    main()

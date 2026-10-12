"""Plan and delete only URL-associated exports inside the chosen output folder."""
from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import stat

from .storage import JobLock, digest, load


def checked_path(path, root):
    path, root = Path(path).absolute(), Path(root).resolve()
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f'删除路径超出保存目录：{path}')
    current = path
    while current != root:
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if current.is_symlink() or getattr(info,'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f'删除范围包含链接或目录联接，请先人工检查：{current}')
        if current.parent == current:
            raise ValueError('无法确认删除范围')
        current = current.parent
    return resolved


def fingerprint(path):
    info = path.stat()
    return (info.st_size, info.st_mtime_ns, info.st_ino)


@dataclass(frozen=True)
class DeletePlan:
    root: Path
    url: str
    files: tuple
    cache: Path
    inventory: tuple


def plan_delete(output, url):
    root = Path(output).resolve()
    cache = root/'.novel-cache'/digest(url)[:16]
    files = set()
    if root.exists():
        for folder, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith('.') and
                       not (Path(folder)/name).is_symlink() and not (Path(folder)/name).is_junction()]
            for name in names:
                if not name.endswith('.report.json'):
                    continue
                report = Path(folder)/name
                # Never follow a report link, even when its target is inside root.
                if report.is_symlink():
                    continue
                data = load(report,{})
                if not isinstance(data,dict) or (data.get('input_url') or data.get('url')) != url:
                    continue
                files.add(checked_path(report,root))
                text = report.with_name(name[:-len('.report.json')]+'.txt')
                if text.exists():
                    files.add(checked_path(text,root))
    inventory=[]
    if cache.exists():
        checked_path(cache,root)
        manifest=load(cache/'manifest.json',{})
        if manifest.get('input_url') not in {None,url}:
            raise ValueError('缓存标记属于其他任务，未删除')
        with JobLock(cache):
            for folder, dirs, names in os.walk(cache,followlinks=False):
                for name in dirs+names:
                    path=checked_path(Path(folder)/name,root)
                    if path.is_file() and path.name!='job.lock':
                        inventory.append((str(path),fingerprint(path)))
    inventory.extend((str(path),fingerprint(path)) for path in sorted(files))
    return DeletePlan(root,url,tuple(sorted(files)),cache,tuple(sorted(inventory)))


def execute_delete(plan):
    fresh=plan_delete(plan.root,plan.url)
    if fresh != plan:
        raise RuntimeError('确认后文件或缓存发生变化，请重新查看删除范围')
    # Validate every target and every cache descendant before any deletion.
    for path in plan.files:
        checked_path(path,plan.root)
    if plan.cache.exists():
        checked_path(plan.cache,plan.root)
        with JobLock(plan.cache):
            # Keep the task lock while removing cached content. Other tasks fail
            # to acquire it rather than writing into a half-deleted directory.
            for child in plan.cache.iterdir():
                if child.name=='job.lock':
                    continue
                checked_path(child,plan.root)
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        # No recursive delete after releasing the task lock; a newly-created
        # file makes rmdir fail safely rather than removing another task's work.
        (plan.cache/'job.lock').unlink(missing_ok=True)
        plan.cache.rmdir()
    for path in plan.files:
        checked_path(path,plan.root).unlink()
    return len(plan.files)

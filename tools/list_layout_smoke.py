"""Check actual restored rows at several DPI/window sizes in isolated homes."""
import hashlib
import json
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'vendor/Novel-Get/src')]
from novel_app.gui import App
from novel_app.storage import load, save

if len(sys.argv) == 1:
    all_cases = []
    for scale in (1.0, 1.25, 1.5, 2.0):
        child = subprocess.run([sys.executable, __file__, str(scale)], capture_output=True, text=True, timeout=30)
        print(child.stdout, end='', flush=True)
        if child.returncode:
            print(child.stderr, file=sys.stderr, flush=True)
            raise SystemExit(child.returncode)
        all_cases.extend(load(ROOT / f'evidence/list-layout-{scale}-result.json')['cases'])
    save(ROOT / 'evidence/list-layout-result.json', dict(passed=True, live_data_unchanged=True, cases=all_cases))
    raise SystemExit(0)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(root, widget):
    x = widget.winfo_rootx() - root.winfo_rootx()
    y = widget.winfo_rooty() - root.winfo_rooty()
    return (widget.winfo_viewable() and x >= 0 and y >= 0
            and x + widget.winfo_width() <= root.winfo_width()
            and y + widget.winfo_height() <= root.winfo_height())


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


queue_path = ROOT / 'data/queue.json'
prefs_path = ROOT / 'data/preferences.json'
jobs = load(queue_path, [])
assert len(jobs) >= 2, 'Need the existing two-book queue as a restoration fixture'
prefs = load(prefs_path, {})
manifests = list((Path(prefs['output']) / '.novel-cache').glob('*/manifest.json'))
watched = [queue_path, prefs_path, *manifests]
before = {str(path): fingerprint(path) for path in watched}
cases = []
scales = (float(sys.argv[1]),) if len(sys.argv) > 1 else (1.0, 1.25, 1.5, 2.0)
for scaling in scales:
    for width, height in ((940, 760), (1100, 760), (1100, 880)):
        home = ROOT / 'evidence/list-layout' / uuid4().hex
        output = home / 'downloads'
        save(home / 'data/queue.json', jobs)
        save(home / 'data/preferences.json', {**prefs, 'output': str(output)})
        for manifest in manifests:
            save(output / '.novel-cache' / manifest.parent.name / 'manifest.json', load(manifest))
        root = tk.Tk()
        root.withdraw()
        root.attributes('-alpha', 0)
        root.tk.call('tk', 'scaling', scaling)
        try:
            app = App(root, home)
            root.geometry(f'{width}x{height}')
            root.deiconify()
            root.update()
            items = app.table.get_children()
            assert len(items) == len(jobs), 'Startup lost queue records'
            assert [app.table.item(i, 'values')[3] for i in items] == [j['url'] for j in jobs]
            expected_progress = {load(p)['input_url']: f"{load(p).get('downloaded', 0)}/{load(p).get('total', 0)}" for p in manifests}
            for item in items:
                values = app.table.item(item, 'values')
                if values[3] in expected_progress:
                    assert values[2] == expected_progress[values[3]], 'Cache progress not restored'
            bounds = [app.table.bbox(i) for i in items[:2]]
            result = dict(scaling=scaling, window=[root.winfo_width(), root.winfo_height()],
                          list_height=app.table.winfo_height(), rows=bounds,
                          restored_books=len(items))
            print(json.dumps(result), flush=True)
            assert app.table.winfo_height() >= 180, result
            assert inside(root, app.table), result
            for box in bounds:
                assert box and box[1] + box[3] <= app.table.winfo_height(), result
            for button in (app.start_button, app.auth_button, app.stop_button,
                           app.task_pause_button, app.task_stop_button, app.delete_button):
                assert inside(root, button), (result, button['text'])
                assert button.winfo_width() >= button.winfo_reqwidth(), (result, button['text'], 'clipped button')
            for widget in descendants(root):
                if widget.winfo_viewable() and widget.winfo_toplevel() == root:
                    assert inside(root, widget), (result, str(widget), widget.winfo_class(), widget.winfo_geometry(),
                                                 [widget.winfo_rootx()-root.winfo_rootx(), widget.winfo_rooty()-root.winfo_rooty()], 'outside main window')
            assert not app.log_window.winfo_viewable()
            # Long live messages must not steal height from the list.
            app.status.set('很长的进度说明' * 100)
            app.detail.set('D:/long/path/' * 100)
            root.update()
            assert app.table.winfo_height() == result['list_height'], result
            # Keep the test's own log window transparent before mapping it.
            app.log_window.attributes('-alpha', 0)
            app.show_log()
            root.update()
            assert app.log_window.winfo_viewable()
            assert app.table.winfo_height() == result['list_height'], result
            app.log_window.withdraw()
            app.persist()
            assert len(load(home / 'data/queue.json')) == len(jobs)
            cases.append(result)
        finally:
            for callback in root.tk.call('after', 'info'):
                root.after_cancel(callback)
            root.destroy()
assert before == {str(path): fingerprint(path) for path in watched}, 'Touched live data'
save(ROOT / f'evidence/list-layout-{scales[0]}-result.json',
     dict(passed=True, live_data_unchanged=True, cases=cases))
print('LIST_LAYOUT_OK', flush=True)

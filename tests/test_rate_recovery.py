import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from novel_app import browser_parallel, network
from novel_app.parallel import SiteGate, TaskControl
from test_browser_parallel import FakeContext, FakePage, fake_broker


def test_retry_after_seconds_and_http_date(monkeypatch):
    monkeypatch.setattr(browser_parallel.time, 'time', lambda: 1000)
    assert browser_parallel.retry_after_seconds('120') == 120
    assert browser_parallel.retry_after_seconds('Thu, 01 Jan 1970 00:18:20 GMT') == 100
    assert browser_parallel.retry_after_seconds('Thu, 01 Jan 1970 00:00:00 GMT') == 0
    for value in (None, 'bad', '-1', 'nan', 'inf'):
        assert browser_parallel.retry_after_seconds(value, 60) == 60


def test_rate_recovery_is_shared_and_stays_serial(monkeypatch):
    now = [100.0]
    monkeypatch.setattr('novel_app.parallel.time.monotonic', lambda: now[0])
    gate = SiteGate('a', network.Control(), limit=3, adaptive=True, delay=5)
    first = gate.rate_limit(60)
    assert gate.rate_limit(30) == first
    assert gate.next_start == 160 and gate.limit == 1
    now[0] = 159
    control = network.Control()
    assert not gate._start_ready(control, recovery=True)
    now[0] = 160
    assert not gate._start_ready(control)
    assert gate._start_ready(control, recovery=True)
    gate.end_request(control)
    gate.finish_rate_recovery()
    for _ in range(80):
        gate.acquire(network.Control()); gate.release(10)
    assert gate.limit == 1 and gate.recovered_generation == first


def test_fast_pages_do_not_raise_concurrency_for_no_gain():
    gate = SiteGate('a', network.Control(), limit=3, delay=5)
    for _ in range(100):
        gate.acquire(network.Control()); gate.release(.1)
    assert gate.limit == 1


def test_verification_gate_retains_other_waiter():
    gate = SiteGate('a', network.Control())
    gate.verification(True); gate.verification(True); gate.verification(False)
    assert gate.verifying
    gate.verification(False)
    assert not gate.verifying


def test_rate_downgrade_limits_workers_that_already_reserved_slots():
    gate=SiteGate('a',network.Control(),limit=3,adaptive=False,delay=0,pause_every=1000)
    gate.rate_floor=0
    controls=[network.Control() for _ in range(3)]
    for control in controls:gate.acquire(control)
    assert gate._start_ready(controls[0]) and gate._start_ready(controls[1])
    gate.rate_limit(0)
    gate.end_request(controls[0])
    assert not gate._start_ready(controls[2],recovery=True)
    gate.end_request(controls[1])
    assert gate._start_ready(controls[2],recovery=True)
    gate.finish_rate_recovery()
    assert not gate._start_ready(controls[0])
    for control in controls:gate.release(.1,control=control)
    assert gate.active==0 and not gate.inflight


class RatePage(FakePage):
    def __init__(self, context):
        super().__init__(context)
        self.loads = 0
        self.code = None

    def response(self):
        self.loads += 1
        self.code = 429 if self.context.always or self.loads == 1 else 200
        response = SimpleNamespace(request=SimpleNamespace(resource_type='document'), frame=self.main_frame,
                                   status=self.code, headers={'retry-after': str(self.context.cooldown)})
        for listener in self.listeners.get('response', []):
            listener(response)

    async def reload(self, **kwargs):
        await asyncio.sleep(0)
        self.response()

    async def content(self):
        return '<title>Too Many Requests</title>' if self.code == 429 else '<title>可读正文</title><p>完成</p>'


class RateContext(FakeContext):
    def __init__(self, always=False, cooldown=0):
        super().__init__(True)
        self.always, self.cooldown = always, cooldown

    async def new_page(self):
        page = RatePage(self); self.pages.append(page)
        return page


def rate_setup(monkeypatch, always=False, cooldown=0):
    events = []
    parent = network.Control(lambda **event: events.append(event))
    broker, contexts = fake_broker(monkeypatch)
    contexts['a'] = RateContext(always, cooldown)
    gate = SiteGate('a', parent, delay=0, limit=2, adaptive=False, pause_every=1000)
    gate.rate_floor = 0  # Fast deterministic fixture; production minimum remains 3 seconds.
    task = TaskControl(parent, 'first', 'https://a.example/book/')
    return broker, contexts, gate, task, parent, events


def test_429_recovers_without_captcha_and_without_fixed_one_second_delay(monkeypatch):
    broker, contexts, gate, task, parent, events = rate_setup(monkeypatch)
    started = time.monotonic()
    try:
        result = broker.get('a', 'unused', task, gate, task.url)
        assert '可读正文' in result.html
        assert contexts['a'].pages[0].loads == 2
        assert not any(e['kind'] == 'manual' for e in events)
        assert not gate.rate_recovering and gate.restricted and gate.limit == 1
        assert time.monotonic() - started < .8
    finally:
        parent.cancel.set(); broker.close()


def test_persistent_429_stops_after_three_retries(monkeypatch):
    broker, contexts, gate, task, parent, events = rate_setup(monkeypatch, always=True)
    try:
        with pytest.raises(network.RateLimited, match='429'):
            broker.get('a', 'unused', task, gate, task.url)
        assert contexts['a'].pages[0].loads == 4
        assert gate.blocked and not gate.rate_recovering
        assert not any(e['kind'] == 'manual' for e in events)
    finally:
        parent.cancel.set(); broker.close()


def test_long_429_cooldown_stops_without_manual_or_waiting_an_hour(monkeypatch):
    broker, contexts, gate, task, parent, events = rate_setup(monkeypatch, cooldown=3600)
    try:
        with pytest.raises(network.RateLimited, match='3600'):
            broker.get('a', 'unused', task, gate, task.url)
        assert contexts['a'].pages[0].loads == 1 and not gate.verifying and not gate.rate_recovering
        assert not any(e['kind']=='manual' for e in events)
        with pytest.raises(network.RateLimited): gate.raise_blocked()
    finally:
        parent.cancel.set(); broker.close()


def test_cooldown_is_cancellable_and_other_site_continues(monkeypatch):
    broker, contexts, gate, task, parent, events = rate_setup(monkeypatch, cooldown=90)
    errors = []
    def blocked():
        try:
            broker.get('a', 'unused', task, gate, task.url)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=blocked)
    try:
        thread.start()
        deadline = time.monotonic() + 2
        while not gate.rate_recovering and time.monotonic() < deadline:
            time.sleep(.01)
        assert gate.rate_recovering
        other = TaskControl(parent, 'other', 'https://b.example/book/')
        result = broker.get('b', 'unused', other, SiteGate('b', parent, delay=0), other.url)
        assert '可读正文' in result.html
        task.cancel.set(); thread.join(1)
        assert not thread.is_alive() and isinstance(errors[0], network.Cancelled)
        assert not gate.rate_recovering
    finally:
        parent.cancel.set(); thread.join(2); broker.close()


def test_selected_cancel_while_waiting_for_verification_lock(monkeypatch):
    broker, contexts, gate, task, parent, events = rate_setup(monkeypatch)
    errors = []
    async def hold():
        session, _ = await broker._session('a', 'unused', task)
        await session.setdefault('rate_lock', asyncio.Lock()).acquire()
    broker.call(hold())
    def blocked():
        try:
            broker.get('a', 'unused', task, gate, task.url)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=blocked)
    try:
        thread.start()
        deadline = time.monotonic() + 2
        while not gate.rate_recovering and time.monotonic() < deadline:
            time.sleep(.01)
        task.cancel.set(); thread.join(1)
        assert not thread.is_alive() and isinstance(errors[0], network.Cancelled)
    finally:
        parent.cancel.set(); thread.join(2)
        async def unlock():
            broker.sessions['a']['rate_lock'].release()
        broker.call(unlock()); broker.close()


def test_verification_owner_can_resolve_429_while_rate_owner_waits(monkeypatch):
    broker,contexts,gate,task,parent,events=rate_setup(monkeypatch)
    async def scenario():
        session,_=await broker._session('a','unused',task)
        gate.verification(True)
        generation=gate.rate_limit(0)
        statuses=[dict(code=429,challenge=False,rate_generation=generation,retry_seconds=0) for _ in range(2)]
        pages=[]
        for status in statuses:
            page=RatePage(contexts['a']);page.loads=1
            page.on('response',lambda response,status=status:status.update(code=response.status,challenge=False))
            pages.append(page)
        other=TaskControl(parent,'other',task.url)
        waiting=asyncio.create_task(broker._recover_rate(session,pages[0],other,gate,statuses[0],task.url,[0]))
        await asyncio.sleep(.03)
        await broker._recover_rate(session,pages[1],task,gate,statuses[1],task.url,[0],verification_owner=True)
        gate.verification(False)
        await asyncio.wait_for(waiting,1)
        assert not gate.rate_recovering and not gate.inflight
    try:broker.call(scenario())
    finally:parent.cancel.set();broker.close()

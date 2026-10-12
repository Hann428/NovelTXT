from novel_app.network import Control


def test_manual_confirmation_keeps_browser_events_alive():
    control = Control()
    calls = []
    def pump():
        calls.append(1)
        control.approved.set()
    control.manual("https://example.org", "确认", pump=pump)
    assert calls == [1]


def test_manual_resumes_on_readable_page_without_extra_click():
    events=[]
    control=Control(lambda **event:events.append(event))
    calls=[]
    control.manual('https://example.org','确认',pump=lambda:calls.append(1),ready=lambda:len(calls)==2)
    assert len(calls)==2
    assert events[-1]['kind']=='manual_done'


def test_continue_click_cannot_release_unreadable_browser():
    events=[]
    control=Control(lambda **event:events.append(event))
    calls=[]
    def pump():
        calls.append(1)
        control.approved.set()
    control.manual('https://example.org', 'verify', pump=pump, ready=lambda:len(calls)==3)
    assert len(calls)==3
    assert sum(e['kind']=='manual_done' for e in events)==1


def test_waiting_for_verification_remains_cancellable():
    import pytest
    from novel_app.network import Cancelled
    control=Control()
    with pytest.raises(Cancelled):
        control.manual('https://example.org', 'verify', pump=control.cancel.set, ready=lambda:False)

"""Debugger continuations/cleanup must not swallow faults or leave a game stopped."""
import ctypes as ct
import json
from types import SimpleNamespace

import pytest
from tools import tt_crash_capture as c


def exception(code, address=0x60000000, first=True):
    e=c.DebugEvent(kind=1,pid=42,tid=43)
    e.data.exception.record.code=code
    e.data.exception.record.address=address
    e.data.exception.first_chance=first
    return e


class API:
    def __init__(self, events=()): self.events=list(events); self.calls=[]
    def DebugActiveProcess(self,pid): self.calls.append(('attach',pid)); return True
    def DebugSetProcessKillOnExit(self,value): self.calls.append(('kill_on_exit',value)); return True
    def DebugActiveProcessStop(self,pid): self.calls.append(('detach',pid)); return True
    def WaitForDebugEventEx(self,target,timeout):
        if not self.events: raise AssertionError('No test debug event left')
        ct.memmove(target,ct.byref(self.events.pop(0)),ct.sizeof(c.DebugEvent))
        return True
    def ContinueDebugEvent(self,pid,tid,status): self.calls.append(('continue',status)); return True
    def CloseHandle(self,handle): self.calls.append(('close',handle)); return True


def fixture(monkeypatch, events):
    a=API(events)
    monkeypatch.setattr(c,'api',lambda:a)
    memory=SimpleNamespace(pid=42,modules=[SimpleNamespace(name='ntdll.dll',base=0x70000000,size=4096)])
    return a,memory


def test_passes_real_exceptions_to_game_and_records_both_chances(monkeypatch,tmp_path):
    exit_event=c.DebugEvent(kind=5,pid=42,tid=43)
    exit_event.data.exit_code=0xC0000005
    a,m=fixture(monkeypatch,[exception(0x80000003,0x70000010), exception(0xC0000005),
                           exception(0xC0000005,first=False),exit_event])
    monkeypatch.setattr(c,'write_dump',lambda memory,path:path.write_bytes(b'MDMP test'))
    c.capture(m,tmp_path/'out',10,get_registers=lambda *_:{'eip':0x60000000})
    assert a.calls[:2] == [('attach',42),('kill_on_exit',False)]
    assert [x[1] for x in a.calls if x[0]=='continue'] == [c.CONTINUE,c.NOT_HANDLED,c.NOT_HANDLED,c.CONTINUE]
    assert len(list((tmp_path/'out').glob('*.dmp'))) == 2
    log=[json.loads(x) for x in (tmp_path/'out/events.jsonl').read_text().splitlines()]
    assert log[-1] == {**log[-1], 'kind':'process_exit','code':'0xc0000005'}
    assert not any(x[0]=='detach' for x in a.calls)  # OS has already released it.


def test_timeout_detaches_without_killing(monkeypatch,tmp_path):
    a,m=fixture(monkeypatch,[])
    c.capture(m,tmp_path/'out',0)
    assert a.calls == [('attach',42),('kill_on_exit',False),('detach',42)]


def test_unexpected_capture_failure_still_continues_event_and_detaches(monkeypatch,tmp_path):
    a,m=fixture(monkeypatch,[exception(0xC0000005)])
    def fail(*args): raise ValueError('unexpected dump error')
    monkeypatch.setattr(c,'write_dump',fail)
    with pytest.raises(ValueError): c.capture(m,tmp_path/'out',10,get_registers=lambda *_:{})
    assert a.calls[-2:] == [('continue',c.NOT_HANDLED),('detach',42)]


def test_user_breakpoint_is_not_swallowed(monkeypatch,tmp_path):
    a,m=fixture(monkeypatch,[exception(0x80000003),c.DebugEvent(kind=5,pid=42,tid=43)])
    c.capture(m,tmp_path/'out',10)
    assert ('continue',c.NOT_HANDLED) in a.calls


def test_dump_failure_and_unreadable_state_do_not_suppress_fault(monkeypatch,tmp_path):
    a,m=fixture(monkeypatch,[exception(0xC0000005),c.DebugEvent(kind=5,pid=42,tid=43)])
    def fail(*args): raise OSError('unreadable')
    monkeypatch.setattr(c,'write_dump',fail)
    c.capture(m,tmp_path/'out',10,state=fail,get_registers=fail)
    assert ('continue',c.NOT_HANDLED) in a.calls
    report=json.loads(next((tmp_path/'out').glob('fault-*.json')).read_text())
    assert report['state']['read_error'] == report['register_error'] == 'unreadable'


def test_debug_event_layout():
    assert ct.sizeof(c.DebugEvent)==176 and c.DebugEvent.data.offset==16
    assert ct.sizeof(c.ExceptionRecord)==152

import logging
from types import SimpleNamespace

import pytest

from renegade.native.secondary_capture import SecondaryCapture
from renegade.native import services as s
from renegade.native.memory import MemoryError
from renegade.data import secondary_id
from test_objective_hook import Memory
from test_services import CONTROL


def runtime():
    memory = Memory()
    for offset in (s.CAPTURED, s.CALLBACK_CAPTURED, s.MISSING_AT_CALL):
        memory.write_u32(CONTROL+offset+4, 16)
    return SimpleNamespace(memory=memory, control=CONTROL)


def client():
    c = SimpleNamespace(ready=True, capture_ready=True, config={'secondary_objectives': True},
                        progress=SimpleNamespace(completed=set()))
    def finish(map_name, oid):
        check = secondary_id(map_name, oid)
        if check in c.progress.completed: return False
        c.progress.completed.add(check)
        return True
    c.objective_finished = finish
    return c


def test_logs_missing_entry_evidence_once_then_reports_later_list_state(caplog):
    captures = SecondaryCapture(); r = runtime()
    with caplog.at_level(logging.INFO, logger='Renegade.Services'):
        captures.collect(r); captures.collect(r)
        assert len(caplog.records) == 1
        assert 'entry absent at a completion call' in caplog.text
        assert 'completed objective list' not in caplog.text
        r.memory.write_u32(CONTROL+s.LIST_CAPTURED+4, 16)
        captures.collect(r)
        assert len(caplog.records) == 2
        assert 'completed objective list (live or loaded save)' in caplog.records[-1].message


def test_persistence_failure_survives_reattach_until_retry_succeeds(caplog):
    captures = SecondaryCapture(); r = runtime(); c = client()
    captures.collect(r)
    original = c.objective_finished
    def fail(*args): raise OSError('Disk unavailable')
    c.objective_finished = fail
    with caplog.at_level(logging.ERROR, logger='Renegade.Services'):
        captures.flush(c); captures.flush(c)
    assert len(caplog.records) == 1
    assert captures.pending == {('M01', 109)} and not c.progress.completed
    # Old native buffer is gone, and the missing objective cannot be rescanned.
    captures.collect(SimpleNamespace(memory=Memory(), control=CONTROL))
    c.objective_finished = original
    captures.flush(c)
    assert secondary_id('M01',109) in c.progress.completed
    assert not captures.pending and not captures.errors


def test_disconnect_defers_flush_and_already_persisted_check_is_not_repeated():
    captures = SecondaryCapture(); c = client(); r = runtime()
    captures.collect(r); c.ready = False; captures.flush(c)
    assert not captures.pending and c.progress.completed
    c.ready = True; captures.flush(c)
    captures.collect(r); captures.flush(c)
    assert c.progress.completed == {secondary_id('M01',109)} and not captures.pending


def test_corrupt_provenance_is_rejected():
    r = runtime(); r.memory.write_u32(CONTROL+s.MISSING_AT_CALL+4, 1 << 30)
    with pytest.raises(MemoryError): SecondaryCapture().collect(r)

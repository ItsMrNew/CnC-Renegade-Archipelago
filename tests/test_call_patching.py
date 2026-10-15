from types import SimpleNamespace
import ctypes as ct
import pytest
from renegade.native.memory import ProcessMemory, MemoryError


@pytest.mark.parametrize('fail_write',[False,True])
def test_call_batch_suspends_and_resumes_even_on_rollback(monkeypatch,fail_write):
    events=[];values={0x400000:b'\xe8aaaa',0x400010:b'\xe8bbbb'}
    originals=values.copy()
    mem=ProcessMemory.__new__(ProcessMemory);mem.writable=True;mem.pid=123;mem.handle=456
    def protect(handle,address,size,new,previous):
        previous._obj.value=0x20;events.append(('protect',new));return True
    mem.api=SimpleNamespace(OpenProcess=lambda *a:789,CloseHandle=lambda h:events.append('close'),
        VirtualProtectEx=protect,FlushInstructionCache=lambda *a:True)
    def suspend(h):events.append('suspend');return 0
    def resume(h):events.append('resume');return 0
    monkeypatch.setattr(ct,'WinDLL',lambda name:SimpleNamespace(NtSuspendProcess=suspend,NtResumeProcess=resume))
    mem.read=lambda address,size:values[address]
    def write(address,data):
        events.append(('write',address))
        values[address]=data
        if fail_write and data==b'\xe8dddd':raise MemoryError('partial write')
    mem.write=write
    changes=[(0x400000,b'\xe8aaaa',b'\xe8cccc'),(0x400010,b'\xe8bbbb',b'\xe8dddd')]
    if fail_write:
        with pytest.raises(MemoryError,match='partial write'):mem.replace_calls(changes)
        assert values==originals
    else:
        mem.replace_calls(changes)
        assert values=={a:new for a,old,new in changes}
    assert events[0]=='suspend' and events[-2:]==['resume','close']
    assert ('protect',0x20) in events


def test_instruction_patcher_rejects_non_call_edits_before_access():
    mem=ProcessMemory.__new__(ProcessMemory);mem.writable=True
    with pytest.raises(MemoryError,match='five-byte'):
        mem.replace_calls([(0x400000,b'\x55\x8b\xec\x90\x90',b'\xe9aaaa')])

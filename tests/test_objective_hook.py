import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
    UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_EFLAGS)

from renegade.data import MISSIONS, SECONDARY_OBJECTIVES
from renegade.native import objective_hook as o, services as s
from renegade.native.memory import MemoryError
from test_services import Engine, CONTROL, OBJECT, STACK, RETURN, BASE

HOOK = 0x13000000


def prepare(mission=MISSIONS[1]):
    e = Engine(mission=mission, test_objective_arguments=False)
    e.machine.mem_map(HOOK, 8192)
    e.machine.mem_write(HOOK+o.CODE_OFFSET, o.build_objective_hook(HOOK+o.CODE_OFFSET, CONTROL))
    e.machine.mem_write(BASE+s.SET_OBJECTIVE_STATUS, b'\xc3')
    return e


def call(e, oid, status=1):
    uc = e.machine
    registers = {reg: 0x15100000+i for i, reg in enumerate((UC_X86_REG_EAX, UC_X86_REG_EBX,
        UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP))}
    registers.update({UC_X86_REG_ESP: STACK, UC_X86_REG_EFLAGS: 0x646})
    for reg, value in registers.items(): uc.reg_write(reg, value)
    uc.mem_write(STACK, s.u32(RETURN)+s.u32(oid)+s.u32(status))
    forwarded = []
    def observe(machine, address, size, data):
        if address == BASE+s.SET_OBJECTIVE_STATUS:
            assert all(machine.reg_read(reg) == value for reg,value in registers.items())
            assert bytes(machine.mem_read(STACK+4, 8)) == s.u32(oid)+s.u32(status)
            forwarded.append(True)
    handle = uc.hook_add(UC_HOOK_CODE, observe)
    uc.emu_start(HOOK+o.CODE_OFFSET, RETURN, count=2000)
    assert forwarded == [True]
    assert uc.reg_read(UC_X86_REG_ESP) == STACK+4  # cdecl: arguments remain for caller.
    uc.hook_del(handle)
    return e.read(s.CAPTURED+(e.mission.number-1)*4)


ALL_SECONDARIES = [(m, oid) for m in MISSIONS for oid in SECONDARY_OBJECTIVES[m.map_name]]


@pytest.mark.parametrize('mission,oid', ALL_SECONDARIES)
def test_every_secondary_completion_call_is_captured_without_list_entry(mission, oid):
    e = prepare(mission)
    e.put(BASE+s.OBJECTIVE_COUNT, 0)
    expected = 1 << SECONDARY_OBJECTIVES[mission.map_name].index(oid)
    assert call(e, oid) == expected
    assert e.read(s.OBJECTIVE_EVENTS+(mission.number-1)*4) == expected


@pytest.mark.parametrize('guard', ['pending','failed','hidden','unknown_id','tertiary','disconnected',
    'expired','locked','multiplayer','no_game','no_map','wrong_map','bad_suffix'])
def test_rejected_calls_still_forward_unchanged(guard):
    e = prepare(); oid, status = 109, 1
    if guard in ('pending','failed','hidden'): status = {'pending':0,'failed':2,'hidden':3}[guard]
    if guard == 'unknown_id': oid = 999999
    if guard == 'tertiary': oid = 110
    if guard == 'disconnected': e.set(s.ENABLED, 0)
    if guard == 'expired': e.set(s.HEARTBEAT, 1)
    if guard == 'locked': e.set(s.ALLOWED, 1)
    if guard == 'multiplayer': e.put(BASE+s.STEAM.game_mode, 2)
    if guard == 'no_game': e.put(BASE+s.STEAM.game_data, 0)
    if guard == 'no_map': e.put(BASE+s.STEAM.loaded_map, 0)
    if guard == 'wrong_map': e.machine.mem_write(OBJECT+128, b'M02.lsd\0')
    if guard == 'bad_suffix': e.machine.mem_write(OBJECT+128, b'M01.lsd.bad\0')
    assert call(e, oid, status) == 0


def test_missing_turret_registration_still_checks_and_checkpoints_once():
    e = prepare(); e.put(BASE+s.OBJECTIVE_COUNT, 0)
    assert call(e, 109) == 16
    e.frame()
    assert e.read(s.CAPTURED+4) == 16 and e.read(s.OBJECTIVE_EVENTS+4) == 0
    assert e.calls.count('save') == 1
    call(e, 109); e.frame()
    assert e.calls.count('save') == 1


def test_event_checkpoint_waits_until_unpaused():
    e = prepare(); e.put(BASE+s.OBJECTIVE_COUNT, 0)
    e.put(OBJECT+4, 1)
    call(e, 109); e.frame()
    assert e.read(s.OBJECTIVE_EVENTS+4) == 16 and 'save' not in e.calls
    e.put(OBJECT+4, 0); e.frame()
    assert e.read(s.OBJECTIVE_EVENTS+4) == 0 and e.calls.count('save') == 1


@pytest.mark.parametrize('state', ['present', 'missing', 'empty', 'invalid_count', 'null_array'])
def test_callback_reports_entry_presence_without_guessing(state):
    e = prepare()
    if state == 'missing':
        e.put(OBJECT+2048+4*16, 9999)  # Replace 109's list entry with an unrelated ID.
    if state == 'empty': e.put(BASE+s.OBJECTIVE_COUNT, 0)
    if state == 'invalid_count': e.put(BASE+s.OBJECTIVE_COUNT, 129)
    if state == 'null_array': e.put(BASE+s.OBJECTIVE_ARRAY, 0)
    assert call(e, 109) == 16
    assert e.read(s.CALLBACK_CAPTURED+4) == 16
    assert e.read(s.MISSING_AT_CALL+4) == (16 if state in ('missing','empty') else 0)
    assert e.read(s.PRESENT_AT_CALL+4) == (16 if state == 'present' else 0)


def test_list_scan_has_separate_provenance_from_callback():
    e = prepare()
    e.objective(4)
    e.frame()
    assert e.read(s.LIST_CAPTURED+4) == 16
    assert e.read(s.CALLBACK_CAPTURED+4) == e.read(s.MISSING_AT_CALL+4) == 0
    call(e, 109)
    assert e.read(s.CALLBACK_CAPTURED+4) == e.read(s.PRESENT_AT_CALL+4) == 16
    assert e.read(s.OBJECTIVE_EVENTS+4) == 0  # Already captured: no duplicate save request.


class Memory:
    def __init__(self):
        self.values = {BASE+s.STEAM.commands+o.COMMAND_OFFSET: BASE+s.SET_OBJECTIVE_STATUS}
        self.blocks = {}
    def allocate(self): return HOOK
    def read(self, address, size):
        if address == BASE+s.SET_OBJECTIVE_STATUS: return o.ANCHOR[:size]
        return self.blocks[address][:size]
    def write(self, address, value): self.blocks[address] = value
    def write_u32(self, address, value): self.values[address] = value
    def u32(self, address): return self.values.get(address, 0)
    def make_executable(self, address): pass


def test_callback_reinstalled_after_level_load_and_restored_on_close():
    mem = Memory(); hook = o.ObjectiveHook(mem, CONTROL)
    hook.update(); assert mem.u32(hook.field) == hook.code
    mem.write_u32(hook.field, 0); hook.update()
    assert mem.u32(hook.field) == 0
    mem.write_u32(hook.field, hook.original); hook.update()
    assert mem.u32(hook.field) == hook.code
    assert mem.u32(CONTROL+s.GENERATION) == 1  # Even when a loaded player reuses its address.
    hook.close(); assert mem.u32(hook.field) == hook.original


def test_never_overwrites_another_callback_owner():
    mem = Memory(); hook = o.ObjectiveHook(mem, CONTROL)
    hook.update(); mem.write_u32(hook.field, 0x12345678)
    with pytest.raises(MemoryError): hook.update()
    hook.close(); assert mem.u32(hook.field) == 0x12345678

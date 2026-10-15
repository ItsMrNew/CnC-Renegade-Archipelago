"""Execute TT-addressed x86, with relocated DLLs and real callback ABIs."""
import faulthandler
from pathlib import Path
from types import SimpleNamespace

import pytest
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
    UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP,
    UC_X86_REG_ESP, UC_X86_REG_EFLAGS)

from renegade.data import MISSIONS, OBJECTIVES, objective_kind
from renegade.native import tt, tt_profile, tt_hud, tt_load, tt_discs, tt_inventory, tt_hotkeys, services as s, gate
from tt_fixture import relocated_ctor
from renegade.native.thunk import build_thunk, CODE_OFFSET, u32
from renegade.native.objective_hook import build_objective_hook
from test_native import FakeMemory, CONTROL, OBJECT, MAP, TICK, RETURN, STACK


class Engine:
    def __init__(self, mission=MISSIONS[1], band=0x60000000):
        self.a = a = tt_profile.TTAddresses(band, band + 0x1000000)
        self.uc = uc = Uc(UC_ARCH_X86, UC_MODE_32)
        enabled = faulthandler.is_enabled()
        faulthandler.disable()
        try:
            for address, size in ((0x400000, 0x500000), (CONTROL, 8192), (OBJECT, 16384),
                                  (TICK, 4096), (0x1000000, 0x10000), (band, 0x600000)):
                uc.mem_map(address, size)
        finally:
            if enabled: faulthandler.enable()
        self.mission = mission
        self.put(a.game_mode, 1); self.put(a.game_data, OBJECT)
        self.put(a.loaded_map, MAP)
        self.put(OBJECT + 0x28, MAP + 128)
        uc.mem_write(MAP, (mission.map_name + '.lsd\0').encode())
        uc.mem_write(MAP + 128, b'ap_start_01_savename_is_not_a_map.sav\0')
        self.put(a.tick_iat, TICK)
        uc.mem_write(TICK, b'\xb8' + u32(10000) + b'\xc3')
        self.put(CONTROL+s.ALLOWED, 0xFFF); self.put(CONTROL+s.HEARTBEAT, 9500)
        self.put(CONTROL+s.ENABLED, 1)
        mem = SimpleNamespace(write=lambda addr, data: uc.mem_write(addr, data))
        tt.write_descriptors(mem, CONTROL)
        self.calls = []
        self.registers = {reg: 0x15100000+i for i, reg in enumerate((UC_X86_REG_EAX,
            UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESI,
            UC_X86_REG_EDI, UC_X86_REG_EBP))}
        self.registers.update({UC_X86_REG_ECX: OBJECT+256, UC_X86_REG_ESP: STACK,
                               UC_X86_REG_EFLAGS: 0x646})
        self.destinations = {a.mission_complete: 'victory', a.objective_status: 'objective',
                             a.combat_think: 'think', a.combat_keyboard: 'keyboard'}
        for addr in self.destinations: uc.mem_write(addr, b'\xc3')
        uc.hook_add(UC_HOOK_CODE, self.observe)

    def put(self, address, value): self.uc.mem_write(address, u32(value))
    def read(self, offset): return int.from_bytes(self.uc.mem_read(CONTROL+offset, 4), 'little')

    def observe(self, uc, address, size, data):
        if address in self.destinations:
            self.calls.append(self.destinations[address])
            assert all(uc.reg_read(reg) == value for reg, value in self.registers.items())

    def run(self, code, *args):
        self.uc.mem_write(CONTROL + CODE_OFFSET, code)
        for reg, value in self.registers.items(): self.uc.reg_write(reg, value)
        self.uc.mem_write(STACK, u32(RETURN) + b''.join(u32(arg) for arg in args))
        before = len(self.calls)
        self.uc.emu_start(CONTROL+CODE_OFFSET, RETURN, count=5000)
        assert len(self.calls) == before + 1
        assert self.uc.reg_read(UC_X86_REG_ESP) == STACK+4
        assert bytes(self.uc.mem_read(STACK+4, len(args)*4)) == b''.join(u32(arg) for arg in args)

    def objective(self, oid, kind=2, state=1):
        self.put(self.a.objective_count, 1)
        self.put(self.a.objective_array, OBJECT+1024)
        self.put(OBJECT+1024, OBJECT+2048)
        self.uc.mem_write(OBJECT+2048, u32(oid)+u32(kind)+u32(state))


@pytest.mark.parametrize('mission', MISSIONS)
@pytest.mark.parametrize('band', [0x60000000, 0x71000000])
def test_tt_victory_resolves_dll_map_after_save_load(mission, band):
    e = Engine(mission, band)
    e.put(CONTROL+8, 0)  # Victory control has COMPLETED at this offset.
    e.run(build_thunk(CONTROL, addresses=e.a), 1)
    assert e.read(8) == 1 << (mission.number-1)
    assert e.calls == ['victory']


@pytest.mark.parametrize('guard', ['failure', 'multiplayer', 'menu', 'no_game', 'locked', 'expired'])
def test_tt_victory_guards(guard):
    e = Engine(); e.put(CONTROL+8, 0)
    if guard == 'multiplayer': e.put(e.a.game_mode, 2)
    if guard == 'menu': e.put(e.a.game_mode, 0)
    if guard == 'no_game': e.put(e.a.game_data, 0)
    if guard == 'locked': e.put(CONTROL, 1)
    if guard == 'expired': e.put(CONTROL+4, 1)
    e.run(build_thunk(CONTROL, addresses=e.a), int(guard != 'failure'))
    assert e.read(8) == 0


ALL_OBJECTIVES = [(m, oid) for m in MISSIONS for oid in OBJECTIVES[m.map_name]]


@pytest.mark.parametrize('mission,oid', ALL_OBJECTIVES)
def test_tt_all_primary_and_secondary_callback_ids(mission, oid):
    e = Engine(mission)
    e.run(build_objective_hook(CONTROL+CODE_OFFSET, CONTROL, addresses=e.a), oid, 1)
    expected = 1 << OBJECTIVES[mission.map_name].index(oid)
    assert e.read(s.CAPTURED+4*(mission.number-1)) == expected
    assert e.read(s.MISSING_AT_CALL+4*(mission.number-1)) == expected


@pytest.mark.parametrize('mission,oid', ALL_OBJECTIVES)
def test_tt_loaded_save_objectives(mission, oid):
    e = Engine(mission)
    kind = 1 if objective_kind(mission.map_name, oid) == 'primary' else 2
    e.objective(oid, kind)
    e.run(tt.build_scan(CONTROL, e.a))
    expected = 1 << OBJECTIVES[mission.map_name].index(oid)
    assert e.read(s.CAPTURED+4*(mission.number-1)) == expected
    assert e.read(s.LIST_CAPTURED+4*(mission.number-1)) == expected
    assert e.calls == ['think']


@pytest.mark.parametrize('guard', ['pending', 'failed', 'type', 'unknown', 'menu', 'multiplayer',
                                  'locked', 'expired', 'disabled', 'count', 'null', 'suffix'])
def test_tt_scanner_guards(guard):
    e = Engine(); e.objective(109)
    if guard in ('pending','failed'): e.put(OBJECT+2056, 0 if guard == 'pending' else 2)
    if guard == 'type': e.put(OBJECT+2052, 1)
    if guard == 'unknown': e.put(OBJECT+2048, 123456)
    if guard in ('menu', 'multiplayer'): e.put(e.a.game_mode, 0 if guard == 'menu' else 2)
    if guard == 'locked': e.put(CONTROL, 1)
    if guard == 'expired': e.put(CONTROL+4, 1)
    if guard == 'disabled': e.put(CONTROL+8, 0)
    if guard == 'count': e.put(e.a.objective_count, 129)
    if guard == 'null': e.put(e.a.objective_array, 0)
    if guard == 'suffix': e.uc.mem_write(MAP, b'M01.lsd.bad\0')
    e.run(tt.build_scan(CONTROL, e.a))
    assert e.read(s.CAPTURED+4) == 0


@pytest.mark.parametrize('mission', MISSIONS)
def test_tt_gate_locks_only_unavailable_missions(mission):
    e = Engine(mission)
    # Gate uses ENABLED's offset as its blocked-map output.
    e.put(CONTROL, 0xFFF ^ (1 << (mission.number-1)))
    code = gate.build_gate(CONTROL, addresses=e.a)
    e.run(code)
    assert e.calls == ['keyboard'] and e.read(8) == 1 << (mission.number-1)
    e.put(CONTROL, 0xFFF)
    e.run(code)
    assert e.calls == ['keyboard', 'think'] and e.read(8) == 0


def test_tt_popup_waits_for_dismissal_and_restores_native_menu_path():
    from test_gate import emulate_gate
    a = tt_profile.TTAddresses(0x60000000, 0x61000000)
    calls, blocked, counters, notices = emulate_gate(menu=True, addresses=a,
        transitions=[(2, 9500)]*3, dialog_counts=[0, 1, 0])
    assert calls == ['dialog', 'suspend', 'end_game', 'menu']
    assert notices == (1, 1)


def test_tt_menu_return_reentry_cannot_resume_combat():
    from test_gate import emulate_gate
    a = tt_profile.TTAddresses(0x60000000, 0x61000000)
    calls, _, _, notices = emulate_gate(menu=True, addresses=a, reenter=True,
        transitions=[(2, 9500)]*2, dialog_counts=[0, 0])
    assert calls == ['dialog', 'suspend', 'end_game', 'menu']
    assert notices == (1, 1)


class Memory(FakeMemory):
    def __init__(self):
        super().__init__()
        self.next_allocation = CONTROL
        self.modules = [SimpleNamespace(base=0x400000, name='game2.exe', path=Path('game2.exe'))]
        self.pid = 123
        self.a = tt_profile.TTAddresses(0x60000000, 0x61000000)
        from renegade.native import mission_evidence
        self.write(mission_evidence.TT_FIND, mission_evidence.TT_ANCHOR)
        from renegade.native import intel
        self.write(intel.TT_REVEAL, intel.TT_ANCHOR)
        self.write_u32(self.a.commands+intel.COMMAND_OFFSET, intel.TT_REVEAL)
        for field, value in ((self.a.commands+0x1AC, self.a.mission_complete),
                             (self.a.commands+0x204, self.a.objective_status),
                             (self.a.combat_think_slot, self.a.combat_think),
                             (tt_hud.bindings(self.a)['slot'], tt_hud.bindings(self.a)['render'])):
            self.write_u32(field, value)
        for address, value in tt_hud.expected_anchors(self.a).items():
            self.write(address, value)
        for address, value in tt_discs.expected_anchors(self.a).items():
            self.write(address, value)
        for address, value in tt_inventory.expected_anchors(self.a).items():
            self.write(address, value)
        from renegade.native.tt_notifications import ANCHORS
        for address, value in ANCHORS.items():
            self.write(address,value)
        from renegade.native import tt_traps
        from renegade.native import tt_effects_hud
        from renegade.native import tt_weapon_effects
        from renegade.native import tt_vampiric
        from renegade.native.bonuses import call_bytes
        from renegade.native import tt_hologram
        for address,value in tt_hologram.expected_anchors(self.a).items():self.write(address,value)
        for site,target,name in tt_hologram.calls(self.a):self.write(site,call_bytes(site,target))
        from renegade.native.tt_campaign import SAVE,SAVE_SYSTEM,expected_anchors as campaign_anchors
        self.write(self.a.band_base+SAVE,campaign_anchors(self.a)[self.a.band_base+SAVE])
        self.write_u32(self.a.band_base+SAVE_SYSTEM,0x81C648)
        from renegade.native import checkpoint_input
        for address,value in checkpoint_input.expected_anchors(checkpoint_input.TT).items():self.write(address,value)
        for address,value in tt_vampiric.expected_anchors(self.a).items():self.write(address,value)
        site=self.a.band_base+tt_vampiric.CALL
        self.write(site,call_bytes(site,self.a.band_base+tt_vampiric.LOOKUP))
        for address,value in tt_weapon_effects.expected_anchors(self.a).items():self.write(address,value)
        for rva,target,name in tt_weapon_effects.CALLS:
            site=self.a.band_base+rva
            self.write(site,call_bytes(site,self.a.band_base+target))
        for address,value in tt_effects_hud.expected_anchors(self.a).items():self.write(address,value)
        for address,value in tt_traps.expected_anchors(self.a).items():self.write(address,value)
        for slot,target in ((tt_traps.CONTROL_SLOT,tt_traps.APPLY_CONTROL),
                            (tt_traps.BALLISTIC_SLOT,tt_traps.BALLISTIC_MOVE)):
            self.write_u32(self.a.band_base+slot,self.a.band_base+target)
        for address, value in tt_hotkeys.ANCHORS.items():
            self.write(address,value)
        site = self.a.band_base+tt_discs.GRANT_CALL
        self.write(site, tt_load.call(site, self.a.band_base+tt_discs.GRANT))
        self.write(self.a.band_base+tt_load.CONSTRUCTOR, relocated_ctor(self.a.band_base))
        for rva in tt_load.CALL_SITES:
            site=self.a.band_base+rva
            self.write(site,tt_load.call(site,self.a.band_base+tt_load.CONSTRUCTOR))
    def allocate(self):
        address = self.next_allocation
        self.next_allocation += 8192
        return address
    def make_executable(self, address): pass
    def replace_readonly_u32(self, address, expected, replacement):
        assert self.u32(address) == expected
        self.write_u32(address, replacement)
    def replace_calls(self, replacements):
        assert all(self.read(address,len(old))==old for address,old,new in replacements)
        for address,old,new in replacements: self.write(address,new)


@pytest.fixture
def memory(monkeypatch):
    mem = Memory()
    monkeypatch.setattr(tt_profile, 'verify', lambda *args, **kwargs: mem.a)
    monkeypatch.setattr(tt, 'verify', lambda *args, **kwargs: mem.a)
    return mem


def test_tt_restores_all_callbacks_and_reclaims_only_original_after_load(memory):
    session = tt.TTSession(memory)
    session.update(('M13', 'M01'))
    memory.write_u32(session.mission.control+8, 2)
    memory.write_u32(session.mission.callback_field, session.mission.original)
    memory.write_u32(session.objective.field, session.objective.original)
    assert session.update(('M13', 'M01'))[0] == ('M01',)
    assert memory.u32(session.objective.field) == session.objective.code
    session.close()
    assert memory.u32(session.mission.callback_field) == session.mission.original
    assert memory.u32(session.objective.field) == session.objective.original
    assert memory.u32(session.gate.field) == session.gate.original
    retained = memory.u32(session.hud_guard.a['slot'])
    assert retained == session.hud_guard.control + CODE_OFFSET
    second = tt.TTSession(memory)
    assert second.hud_guard.control == session.hud_guard.control
    assert second.load_guard.control == session.load_guard.control
    second.close()
    assert memory.u32(session.hud_guard.a['slot']) == retained


def test_tt_third_party_callback_is_preserved(memory):
    session = tt.TTSession(memory)
    memory.write_u32(session.mission.callback_field, 0x12345678)
    with pytest.raises(OSError): session.update(('M13',))
    session.close()
    assert memory.u32(session.mission.callback_field) == 0x12345678


def test_tt_uncertain_install_restores_prior_components(memory, monkeypatch):
    replace = memory.replace_readonly_u32
    def fail(address, expected, replacement):
        replace(address, expected, replacement)
        if address == memory.a.combat_think_slot and expected == memory.a.combat_think:
            raise OSError('uncertain gate install')
    monkeypatch.setattr(memory, 'replace_readonly_u32', fail)
    with pytest.raises(OSError): tt.TTSession(memory)
    assert memory.u32(memory.a.commands+0x1AC) == memory.a.mission_complete
    assert memory.u32(memory.a.commands+0x204) == memory.a.objective_status

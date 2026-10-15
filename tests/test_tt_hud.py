"""Replay the captured TT null-renderer fault through the generated x86 guard."""
import pytest
from unicorn import UcError, UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_ESP

from renegade.native import tt_hud as h
from renegade.native.profile import UnsupportedBuild
from renegade.native.thunk import CODE_OFFSET, u32
from test_native import CONTROL, OBJECT, STACK, RETURN
from test_tt_native import Engine, Memory


class HUD:
    def __init__(self, index=5, pointer=0, hidden=0, band=0x60000000):
        self.e = e = Engine(band=band)
        self.a = a = h.bindings(e.a)
        self.calls = []
        e.put(a['index'], index)
        e.uc.mem_write(a['hidden'], bytes([hidden]))
        if index < 8:
            e.put(a['renderers']+4*index, pointer)
        hud = band+0x71FB5
        # Original Combat Render is represented by its call into the real HUD
        # branch from both crash dumps. The sentence stub dereferences this+130,
        # exactly the failing read; the rest of Render always runs afterwards.
        e.uc.mem_write(a['render'], b'\xe9'+u32(hud-a['render']-5))
        e.uc.mem_write(hud, h.expected_anchors(e.a)[hud]+b'\xc3')
        e.uc.mem_write(band+0xB5070, b'\x8b\x81\x30\x01\x00\x00\xc3')
        e.uc.mem_write(CONTROL+CODE_OFFSET, h.build_guard(CONTROL, e.a))
        def observe(uc, address, size, data):
            if address == a['render']:
                assert uc.reg_read(UC_X86_REG_ECX) == e.registers[UC_X86_REG_ECX]
                self.calls.append('render')
            if address == band+0xB5070: self.calls.append('sentence')
            if address == band+0x71FD0: self.calls.append('rest_of_render')
        e.uc.hook_add(UC_HOOK_CODE, observe)

    def run(self, guarded=True):
        e = self.e
        for reg, value in e.registers.items(): e.uc.reg_write(reg, value)
        e.uc.mem_write(STACK, u32(RETURN))
        e.uc.emu_start(CONTROL+CODE_OFFSET if guarded else self.a['render'], RETURN, count=5000)
        assert e.uc.reg_read(UC_X86_REG_ESP) == STACK+4
        if guarded:
            for reg, value in e.registers.items():
                if reg != UC_X86_REG_ESP: assert e.uc.reg_read(reg) == value


@pytest.mark.parametrize('index', [5, 6])
def test_captured_crash_without_guard(index):
    hud = HUD(index)
    with pytest.raises(UcError, match='Invalid memory read'): hud.run(guarded=False)
    assert hud.calls == ['render', 'sentence']


@pytest.mark.parametrize('index', [*range(8), 8, 0xFFFFFFFF])
@pytest.mark.parametrize('band', [0x60000000, 0x71000000])
def test_missing_renderer_only_skips_sentence_and_preserves_abi(index, band):
    hud = HUD(index, band=band)
    hud.run()
    assert hud.calls == ['render', 'rest_of_render']
    assert hud.e.uc.mem_read(hud.a['hidden'], 1) == b'\0'
    assert hud.e.read(h.GUARDED_FRAMES) == hud.e.read(h.TOTAL_FRAMES) == 1


@pytest.mark.parametrize('index', range(8))
def test_valid_renderer_draws_normally(index):
    hud = HUD(index, OBJECT+2048)
    hud.run()
    assert hud.calls == ['render', 'sentence', 'rest_of_render']
    assert hud.e.read(h.GUARDED_FRAMES) == 0


@pytest.mark.parametrize('hidden', [1, 255])
def test_existing_hidden_preference_is_preserved(hidden):
    hud = HUD(hidden=hidden)
    hud.run()
    assert hud.calls == ['render', 'rest_of_render']
    assert hud.e.uc.mem_read(hud.a['hidden'], 1) == bytes([hidden])
    assert hud.e.read(h.GUARDED_FRAMES) == 0


def test_renderer_recreation_and_clear_on_repeated_loads():
    hud = HUD()
    for pointer in [0, OBJECT+2048, 0, 0, OBJECT+2048]:
        hud.e.put(hud.a['renderers']+5*4, pointer)
        hud.run()
    assert hud.calls.count('render') == hud.calls.count('rest_of_render') == 5
    assert hud.calls.count('sentence') == 2
    assert hud.e.read(h.GUARDED_FRAMES) == 3
    assert hud.e.read(h.TOTAL_FRAMES) == 5


def test_reentrant_render_restores_each_calls_hidden_preference():
    hud = HUD()
    e = hud.e
    render = hud.a['render']
    # Reenter once from Render. The second wrapper sees the temporary hide flag;
    # its restore must leave that flag set until the outer render completes.
    nested = render+0x200
    e.uc.mem_write(render, b'\xff\x05'+u32(OBJECT+512)
        +b'\x83\x3d'+u32(OBJECT+512)+b'\x01\x75\x05'
        +b'\xe8'+u32((CONTROL+CODE_OFFSET)-(render+20))
        +b'\xe9'+u32(nested-(render+25)))
    e.uc.mem_write(nested, b'\xa0'+u32(hud.a['hidden'])+b'\xa2'+u32(OBJECT+516)+b'\xc3')
    hud.run()
    assert e.uc.mem_read(OBJECT+516, 1) == b'\x01'
    assert e.uc.mem_read(hud.a['hidden'], 1) == b'\0'
    assert e.read(h.TOTAL_FRAMES) == 2
    assert e.read(h.GUARDED_FRAMES) == 1


def test_guard_is_reused_without_touching_ap_callbacks():
    mem = Memory()
    before = dict(mem.values)
    guard = h.NativeHUDGuard(mem, mem.a)
    assert guard.install()
    after = dict(mem.values)
    assert not h.NativeHUDGuard(mem, mem.a).install()
    assert mem.values == after
    slot = guard.a['slot']
    assert all(mem.values[k] == value for k, value in before.items() if not slot <= k < slot+4)
    assert guard.snapshot() == dict(installed=True, guarded_frames=0, render_frames=0)


@pytest.mark.parametrize('offset', [0x2A1950, 0x71FB5, 0xAFC3B])
def test_rejects_changed_live_instructions(offset):
    mem = Memory()
    mem.write(mem.a.band_base+offset, b'\xcc')
    before = dict(mem.values)
    with pytest.raises(UnsupportedBuild): h.NativeHUDGuard(mem, mem.a)
    assert mem.values == before


@pytest.mark.parametrize('pointer', [0, 0x12345678])
def test_rejects_other_render_hooks(pointer):
    mem = Memory()
    mem.write_u32(h.bindings(mem.a)['slot'], pointer)
    before = dict(mem.values)
    with pytest.raises(UnsupportedBuild): h.NativeHUDGuard(mem, mem.a)
    assert mem.values == before


def test_rejects_corrupted_retained_code():
    mem = Memory()
    guard = h.NativeHUDGuard(mem, mem.a)
    guard.install()
    mem.write(guard.control+CODE_OFFSET, b'\xcc')
    with pytest.raises(UnsupportedBuild): h.NativeHUDGuard(mem, mem.a)


def test_recognizes_successful_write_after_uncertain_install(monkeypatch):
    mem = Memory()
    original = mem.replace_readonly_u32
    def uncertain(*args):
        original(*args)
        raise OSError('read-back interrupted after write')
    monkeypatch.setattr(mem, 'replace_readonly_u32', uncertain)
    with pytest.raises(OSError): h.NativeHUDGuard(mem, mem.a).install()
    guard = h.NativeHUDGuard(mem, mem.a)
    assert not guard.install()
    assert guard.snapshot()['installed']

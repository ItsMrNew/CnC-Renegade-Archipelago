"""Execute the actual emitted gameplay gate in an x86 emulator."""
import asyncio
import faulthandler
from pathlib import Path
from types import SimpleNamespace

import pytest
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
    UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP,
    UC_X86_REG_ESP, UC_X86_REG_EFLAGS)

from renegade.data import MISSIONS
from renegade.native.gate import (GameplayGate, build_gate, verify_gate,
    BLOCKED_FRAMES, ALLOWED_FRAMES)
from renegade.native.profile import STEAM, UnsupportedBuild
from renegade.native.thunk import CODE_OFFSET
from test_native import FakeMemory, pack, CONTROL, OBJECT, MAP, TICK, RETURN, STACK


def emulate_gate(map_name=b"M13.mix\0", allowed=2, mode=1, active=0,
                 now=10000, heartbeat=9500, null_game=False, null_map=False,
                 map_at_page_end=False, transitions=None, menu=False, dialog_counts=None,
                 dialog_success=True, reenter=False, source_name=None, addresses=None):
    from renegade.native.thunk import steam_addresses
    a = addresses or steam_addresses()
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        for address, size in [(0x400000, 0x500000), (CONTROL, 8192), (OBJECT, 8192),
                              (TICK, 4096), (0x1000000, 0x10000)] + ([(a.band_base, 0x600000)] if addresses else []):
            uc.mem_map(address, size)
    finally:
        if enabled:
            faulthandler.enable()
    map_address = OBJECT + 8192 - len(map_name) if map_at_page_end else MAP
    this = OBJECT + 128
    uc.mem_write(CONTROL + CODE_OFFSET, build_gate(CONTROL, return_to_menu=menu, addresses=a))
    uc.mem_write(a.tick_iat, pack(TICK))
    uc.mem_write(a.game_mode, pack(mode))
    uc.mem_write(a.game_data, pack(0 if null_game else OBJECT))
    uc.mem_write(OBJECT + STEAM.map_offset, pack(0 if null_map else map_address))
    uc.mem_write(this + 4, pack(active))
    uc.mem_write(a.loaded_map, pack(0 if null_map else map_address))
    uc.mem_write(map_address, map_name)
    if source_name:
        uc.mem_write(MAP + 128, source_name)
        uc.mem_write(OBJECT + STEAM.map_offset, pack(MAP + 128))
    uc.mem_write(TICK, b"\xb8" + pack(now) + b"\xc3")
    destinations = {a.combat_think: "think",
                    a.combat_keyboard: "keyboard"}
    if menu:
        from renegade.native.gate import TITLE, MESSAGE, LOCK_TITLE, LOCK_MESSAGE
        uc.mem_write(CONTROL + TITLE, (LOCK_TITLE + "\0").encode("utf-16-le"))
        uc.mem_write(CONTROL + MESSAGE, (LOCK_MESSAGE + "\0").encode("utf-16-le"))
        destinations.update({a.message_dialog: "dialog",
                             a.end_game: "end_game",
                             a.end_game_menu: "menu", TICK + 512: "suspend"})
        uc.mem_write(this, pack(OBJECT + 256))
        uc.mem_write(OBJECT + 256 + 16, pack(TICK + 512))
    for address in destinations:
        uc.mem_write(address, b"\xc3")
    if menu:
        uc.mem_write(a.message_dialog,
                     b"\xc7\x05" + pack(a.message_count) + pack(int(dialog_success)) +
                     b"\xb8" + pack(int(dialog_success)) + b"\xc3")
        uc.mem_write(TICK + 512, b"\xc7\x41\x04" + pack(3) + b"\xc3")
        if reenter:
            address = a.end_game
            # Deliberately invalid this on recursive entry: the exit guard must
            # run before dereferencing it. Vanilla End_Game reenters mode Think.
            uc.mem_write(address, b"\x31\xc9\xe8" + pack(CONTROL + CODE_OFFSET - (address + 7)) + b"\xc3")
    registers = [UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
                 UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP]
    values = {r: 0x12300000 + i for i, r in enumerate(registers)}
    values.update({UC_X86_REG_ECX: this, UC_X86_REG_ESP: STACK, UC_X86_REG_EFLAGS: 0x646})
    calls = []

    def observe(machine, address, size, data):
        if address in destinations:
            calls.append(destinations[address])
            if destinations[address] in {"think", "keyboard"}:
                assert {r: machine.reg_read(r) for r in values} == values
            elif destinations[address] == "dialog":
                from renegade.native.gate import TITLE, MESSAGE
                esp = machine.reg_read(UC_X86_REG_ESP)
                assert bytes(machine.mem_read(esp + 4, 20)) == b"".join(pack(v) for v in
                    [CONTROL + TITLE, CONTROL + MESSAGE, 0, 0, 0])
                assert not machine.reg_read(UC_X86_REG_EFLAGS) & 0x400
            elif destinations[address] == "suspend":
                assert machine.reg_read(UC_X86_REG_ECX) == this

    uc.hook_add(UC_HOOK_CODE, observe)
    states = []
    for frame, (mask, beat) in enumerate(transitions or [(allowed, heartbeat)]):
        if dialog_counts is not None:
            uc.mem_write(a.message_count, pack(dialog_counts[frame]))
        uc.mem_write(CONTROL, pack(mask) + pack(beat))
        uc.mem_write(STACK, pack(RETURN))
        for r, value in values.items():
            uc.reg_write(r, value)
        before = len(calls)
        uc.emu_start(CONTROL + CODE_OFFSET, RETURN, count=2000)
        if not menu:
            assert len(calls) == before + 1
        assert {r: uc.reg_read(r) for r in values if r != UC_X86_REG_ESP} == {
            r: v for r, v in values.items() if r != UC_X86_REG_ESP}
        assert uc.reg_read(UC_X86_REG_ESP) == STACK + 4
        states.append(int.from_bytes(uc.mem_read(CONTROL + 8, 4), "little"))
    counters = tuple(int.from_bytes(uc.mem_read(CONTROL + offset, 4), "little")
                     for offset in (BLOCKED_FRAMES, ALLOWED_FRAMES))
    if menu:
        return calls, states, counters, tuple(int.from_bytes(uc.mem_read(CONTROL + offset, 4), "little")
                                              for offset in (36, 32))
    return calls, states, counters


@pytest.mark.parametrize("index", range(12))
@pytest.mark.parametrize("uppercase", [True, False])
def test_locked_map_runs_keyboard_without_combat(index, uppercase):
    name = MISSIONS[index].map_name + ".mix"
    name = name.upper() if uppercase else name.lower()
    calls, states, counts = emulate_gate(name.encode() + b"\0", allowed=0xFFF ^ (1 << index))
    assert calls == ["keyboard"]
    assert states == [1 << index]
    assert counts == (1, 0)


@pytest.mark.parametrize("index", range(12))
def test_allowed_map_runs_combat(index):
    result = emulate_gate((MISSIONS[index].map_name + ".mix\0").encode(), allowed=1 << index)
    assert result == (["think"], [0], (0, 1))


@pytest.mark.parametrize("kwargs", [
    {"allowed": 0}, {"heartbeat": 1}, {"mode": 0}, {"mode": 2}, {"mode": 3},
    {"active": 1}, {"active": 2}, {"active": 3}, {"null_game": True}, {"null_map": True},
    {"map_name": b"M00_Tutorial.mix\0"}, {"map_name": b"C&C_Field.mix\0"},
    {"map_name": b"M13.mix-extra\0"}, {"map_name": b"M13.lsd-extra\0"},
    {"map_name": b"M12.mix\0"}, {"map_name": b"\0", "map_at_page_end": True},
    {"map_name": b"M1\0", "map_at_page_end": True},
])
def test_gate_leaves_inactive_unknown_and_stale_sessions_alone(kwargs):
    assert emulate_gate(**kwargs) == (["think"], [0], (0, 0))


def test_unlock_disconnect_and_reconnect_take_effect_next_frame():
    assert emulate_gate(transitions=[(2, 9500), (3, 9500), (2, 9500), (0, 9500), (2, 9500)]) == (
        ["keyboard", "think", "keyboard", "think", "keyboard"], [1, 0, 1, 0, 1], (3, 1))


def test_expired_lease_and_tick_wrap():
    assert emulate_gate(now=100, heartbeat=0xFFFFFFF0)[0] == ["keyboard"]
    assert emulate_gate(now=3000, heartbeat=0xFFFFFFF0)[0] == ["think"]
    assert emulate_gate(transitions=[(2, 9500), (2, 1)])[1] == [1, 0]


class GateMemory(FakeMemory):
    def __init__(self):
        super().__init__()
        self.field = 0x400000 + STEAM.combat_think_slot
        self.original = 0x400000 + STEAM.combat_think
        self.write_u32(self.field, self.original)
        self.modules = [SimpleNamespace(base=0x400000, path=Path("Game.dat"))]
        for rva, value in STEAM.gate_anchors.items():
            self.write(0x400000 + rva, value)

    def replace_readonly_u32(self, address, expected, replacement):
        if self.u32(address) != expected:
            raise OSError("Ownership changed")
        self.write_u32(address, replacement)


@pytest.fixture
def memory(monkeypatch):
    monkeypatch.setattr("renegade.native.gate.require_digest", lambda *args: None)
    return GateMemory()


def test_gate_lifecycle_and_snapshot(memory):
    gate = GameplayGate(memory)
    gate.install()
    assert memory.u32(memory.field) == CONTROL + CODE_OFFSET
    assert gate.update(["M13"])["blocked_map"] is None
    assert memory.u32(CONTROL) == 1
    memory.write_u32(CONTROL + 8, 2)
    assert gate.snapshot()["blocked_map"] == "M01"
    gate.update([])
    assert memory.u32(CONTROL) == 0
    gate.close()
    gate.close()
    assert memory.u32(memory.field) == memory.original


def test_gate_restores_uncertain_write(memory):
    gate = GameplayGate(memory)
    memory.fail_replacement = True
    with pytest.raises(OSError, match="uncertain"):
        gate.install()
    assert memory.u32(memory.field) == memory.original
    assert not gate.installed


def test_gate_preserves_another_owner(memory):
    gate = GameplayGate(memory)
    gate.install()
    memory.write_u32(memory.field, 0x60000000)
    with pytest.raises(OSError, match="ownership"):
        gate.update(["M13"])
    gate.close()
    assert memory.u32(memory.field) == 0x60000000
    assert memory.u32(CONTROL) == 0


@pytest.mark.parametrize("change", ["anchor", "pointer", "base"])
def test_gate_rejects_runtime_changes(memory, change):
    if change == "base":
        memory.modules[0].base = 0x500000
    else:
        memory.write_u32(memory.field if change == "pointer" else 0x400000 + STEAM.combat_think, 0)
    with pytest.raises(UnsupportedBuild):
        verify_gate(memory)


def test_gate_rechecks_pointer_after_preparation(memory):
    gate = GameplayGate(memory)
    memory.write_u32(memory.field, 0x60000000)
    with pytest.raises(OSError):
        gate.install()
    assert memory.u32(memory.field) == 0x60000000


def test_room_change_restores_gate_without_reusing_permissions(monkeypatch):
    from renegade.native.gate import watch_gate

    async def exercise():
        client = SimpleNamespace(identity=("a", 0, 1), ready=True, unlocked_maps=["M13"], stop=asyncio.Event())
        calls = []
        class Gate:
            memory = SimpleNamespace(pid=1, close=lambda: calls.append("memory closed"))
            def update(self, maps):
                calls.append(tuple(maps))
                client.identity = ("b", 0, 1)
                return {"blocked_map": None}
            def close(self):
                calls.append("gate closed")
        monkeypatch.setattr("renegade.native.gate.attach_gate", lambda pid, **kwargs: Gate())
        with pytest.raises(ValueError, match="another AP room"):
            await watch_gate(client)
        assert calls == [("M13",), "gate closed", "memory closed"]
    asyncio.run(exercise())


@pytest.mark.parametrize("fail_write", [False, True])
def test_vtable_write_restores_page_protection_even_on_failure(fail_write):
    from renegade.native.memory import ProcessMemory
    memory = ProcessMemory.__new__(ProcessMemory)
    memory.writable = True
    memory.handle = 1
    memory.pid = 1
    calls = []
    pointer = [0x4035D0]

    def protect(handle, address, size, protection, old):
        calls.append(protection)
        old._obj.value = 0x02  # PAGE_READONLY before replacement
        return 1

    def write(address, value):
        pointer[0] = value
        if fail_write:
            raise OSError("uncertain write")

    memory.api = SimpleNamespace(VirtualProtectEx=protect)
    memory.u32 = lambda address: pointer[0]
    memory.write_u32 = write
    if fail_write:
        with pytest.raises(OSError, match="uncertain"):
            memory.replace_readonly_u32(0x7EEB1C, 0x4035D0, CONTROL)
    else:
        memory.replace_readonly_u32(0x7EEB1C, 0x4035D0, CONTROL)
    assert calls == [4, 2]


def test_gate_live_runner_waits_through_tutorial_and_restores(monkeypatch, tmp_path):
    from tools import gate_test
    clock = [0.0]
    closed = []
    state = {"blocked_map": None, "blocked_frames": 0, "allowed_frames": 0}

    class Gate:
        memory = SimpleNamespace(pid=1, close=lambda: closed.append("memory"))
        def update(self, maps):
            if "M13" in maps:
                state["blocked_map"] = None
                state["allowed_frames"] += 10
            else:
                state["blocked_map"] = "M13"
                state["blocked_frames"] += 10
            return state.copy()
        def close(self):
            closed.append("gate")

    monkeypatch.setattr(gate_test, "attach_gate", lambda pid, **kwargs: Gate())
    monkeypatch.setattr(gate_test, "verify_gate", lambda memory: closed.append("verified"))
    monkeypatch.setattr(gate_test, "telemetry", lambda memory: {
        "map_name": "M00_Tutorial.mix" if clock[0] < 1 else "M13.mix", "mode": 1})
    monkeypatch.setattr(gate_test, "save_status", lambda *args, **kwargs: kwargs)
    monkeypatch.setattr(gate_test.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(gate_test.time, "sleep", lambda delta: clock.__setitem__(0, clock[0] + delta))
    report = gate_test.exercise(tmp_path)
    assert report["stage"] == "passed"
    assert report["paused_seconds"] >= 10
    assert report["original_combat_update_restored"]
    assert not report["ap_integration_test"]
    assert closed == ["gate", "verified", "memory"]


def test_disconnected_client_disarms_gameplay_gate(monkeypatch):
    from renegade.native.gate import watch_gate

    async def exercise():
        client = SimpleNamespace(identity=("a", 0, 1), ready=True, unlocked_maps=["M13"], stop=asyncio.Event())
        permissions = []
        class Gate:
            memory = SimpleNamespace(pid=1, close=lambda: None)
            def update(self, maps):
                permissions.append(tuple(maps))
                if client.ready:
                    client.ready = False
                else:
                    client.stop.set()
                return {"blocked_map": None}
            def close(self):
                pass
        monkeypatch.setattr("renegade.native.gate.attach_gate", lambda pid, **kwargs: Gate())
        await watch_gate(client)
        assert permissions == [("M13",), ()]
    asyncio.run(exercise())


def test_received_clearance_packet_releases_native_gate(memory, tmp_path):
    from renegade.client import RenegadeClient
    from test_core import Socket, authenticate, received

    async def exercise():
        client = RenegadeClient("localhost:1", "Havoc", state_dir=tmp_path)
        socket = Socket()
        await authenticate(client, socket)
        gate = GameplayGate(memory)
        gate.install()
        masks = []
        try:
            await client.handle(socket, {"cmd": "ReceivedItems", "index": 0, "items": received(1)})
            gate.update(client.unlocked_maps)
            masks.append(memory.u32(CONTROL))
            # A subsequent AP packet grants the next mission, without a client
            # restart or another level load. This is simulated network input.
            await client.handle(socket, {"cmd": "ReceivedItems", "index": 1, "items": received(1)})
            gate.update(client.unlocked_maps)
            masks.append(memory.u32(CONTROL))
            assert masks == [1, 3]
            assert emulate_gate(b"M01.mix\0", transitions=[(m, 9500) for m in masks]) == (
                ["keyboard", "think"], [2, 0], (1, 1))
        finally:
            gate.close()
    asyncio.run(exercise())


def test_menu_popup_holds_then_exits_once_on_dismissal():
    calls, states, counts, ui = emulate_gate(menu=True, transitions=[(2, 9500)] * 4,
                                            dialog_counts=[0, 1, 1, 0], reenter=True)
    assert calls == ["dialog", "suspend", "end_game", "menu"]
    assert states[-1] == 0  # Inactive combat may never Think again at the menu.
    assert counts == (4, 0)
    assert ui == (1, 1)


def test_existing_popup_is_not_mistaken_for_our_confirmation():
    result = emulate_gate(menu=True, transitions=[(2, 9500)] * 3, dialog_counts=[1, 0, 1])
    assert result[0] == ["dialog"]
    assert result[3] == (1, 0)


def test_unlock_while_popup_open_waits_for_dismissal_then_resumes():
    result = emulate_gate(menu=True, transitions=[(2, 9500), (3, 9500), (3, 9500)], dialog_counts=[0, 1, 0])
    assert result[0] == ["dialog", "think"]
    assert result[3] == (1, 0)


def test_failed_popup_creation_never_exits_the_game():
    result = emulate_gate(menu=True, transitions=[(2, 9500)] * 2, dialog_counts=[0, 0], dialog_success=False)
    assert result[0] == ["dialog", "dialog"]
    assert result[3] == (0, 0)


def test_disarm_cancels_pending_menu_return():
    result = emulate_gate(menu=True, transitions=[(2, 9500), (0, 9500)], dialog_counts=[0, 0])
    assert result[0] == ["dialog", "think"]
    assert result[3] == (1, 0)


def test_menu_install_checks_extra_anchors_and_writes_unicode(memory):
    from renegade.native.gate import TITLE, LOCK_TITLE
    with pytest.raises(UnsupportedBuild):
        GameplayGate(memory, return_to_menu=True)
    for rva, expected in STEAM.menu_anchors.items():
        memory.write(0x400000 + rva, expected)
    gate = GameplayGate(memory, return_to_menu=True)
    gate.install()
    expected = (LOCK_TITLE + "\0").encode("utf-16-le")
    assert memory.read(CONTROL + TITLE, len(expected)) == expected
    gate.close()


def test_menu_live_runner_observes_notice_and_menu_then_restores(monkeypatch, tmp_path):
    from tools import gate_test
    clock = [0.0]
    closed = []
    frame = [0]
    def state():
        return dict(blocked_map="M13", blocked_frames=frame[0], allowed_frames=0,
                    notices_shown=int(frame[0] > 0), menu_returns=int(frame[0] > 1))
    class Gate:
        memory = SimpleNamespace(pid=1, close=lambda: closed.append("memory"))
        def snapshot(self):
            return state()
        def update(self, maps):
            assert "M13" not in maps
            frame[0] += 1
            return state()
        def close(self):
            closed.append("gate")
    monkeypatch.setattr(gate_test, "attach_gate", lambda pid, return_to_menu: Gate())
    monkeypatch.setattr(gate_test, "verify_gate", lambda memory, **kwargs: closed.append("verified"))
    monkeypatch.setattr(gate_test, "telemetry", lambda memory: {"map_name": "M13.mix", "mode": 1})
    monkeypatch.setattr(gate_test, "save_status", lambda *args, **kwargs: kwargs)
    monkeypatch.setattr(gate_test.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(gate_test.time, "sleep", lambda delta: clock.__setitem__(0, clock[0] + delta))
    report = gate_test.exercise_menu(tmp_path)
    assert report["stage"] == "passed"
    assert report["notices_shown"] == report["menu_returns"] == 1
    assert report["original_combat_update_restored"]
    assert closed == ["gate", "verified", "memory"]


@pytest.mark.parametrize("index", range(12))
def test_save_load_gate_uses_level_metadata(index):
    result = emulate_gate((MISSIONS[index].map_name + ".lsd\0").encode(),
                          allowed=0xFFF ^ (1 << index), source_name=b"save\\unrelated_name.sav\0")
    assert result[0] == ["keyboard"]
    assert result[1] == [1 << index]

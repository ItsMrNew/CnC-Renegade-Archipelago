import struct
import faulthandler
import asyncio
from types import SimpleNamespace

import pytest
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
    UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP,
    UC_X86_REG_ESP, UC_X86_REG_EFLAGS)

from renegade.data import MISSIONS
from renegade.native.bridge import MissionHook
from renegade.native.profile import STEAM, UnsupportedBuild
from renegade.native.thunk import build_thunk, CODE_OFFSET

CONTROL = 0x10000000
OBJECT = 0x20000000
MAP = OBJECT + 4096
TICK = 0x30000000
RETURN = TICK + 256
STACK = 0x1008000


def pack(value):
    return struct.pack("<I", value & 0xFFFFFFFF)


def emulate(map_name=b"M13.mix\0", success=1, mode=1, allowed=0xFFF,
            now=10000, heartbeat=9500, initial=0, game_pointer=OBJECT, map_pointer=MAP, source_name=None):
    machine = Uc(UC_ARCH_X86, UC_MODE_32)
    # Unicorn handles Windows mapping exceptions internally. CPython's VEH
    # faulthandler otherwise prints false fatal traces for successful mem_map.
    handler_enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        for address, size in [(0x400000, 0x500000), (CONTROL, 8192), (OBJECT, 8192),
                              (TICK, 4096), (0x1000000, 0x10000)]:
            machine.mem_map(address, size)
    finally:
        if handler_enabled:
            faulthandler.enable()
    machine.mem_write(CONTROL, pack(allowed) + pack(heartbeat) + pack(initial))
    machine.mem_write(CONTROL + CODE_OFFSET, build_thunk(CONTROL))
    machine.mem_write(0x400000 + STEAM.tick_iat, pack(TICK))
    machine.mem_write(0x400000 + STEAM.game_mode, pack(mode))
    machine.mem_write(0x400000 + STEAM.game_data, pack(game_pointer))
    machine.mem_write(OBJECT + STEAM.map_offset, pack(map_pointer))
    machine.mem_write(0x400000 + STEAM.loaded_map, pack(map_pointer))
    machine.mem_write(MAP, map_name)
    if source_name:
        machine.mem_write(MAP + 128, source_name)
        machine.mem_write(OBJECT + STEAM.map_offset, pack(MAP + 128))
    machine.mem_write(TICK, b"\xb8" + pack(now) + b"\xc3")
    original = 0x400000 + STEAM.mission_complete
    machine.mem_write(original, b"\x8b\x44\x24\x04\xc3")  # return the success arg
    machine.mem_write(STACK, pack(RETURN) + pack(success))
    registers = [UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX,
                 UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP]
    values = {reg: 0x12300000 + i for i, reg in enumerate(registers)}
    values[UC_X86_REG_ESP] = STACK
    values[UC_X86_REG_EFLAGS] = 0x646  # Include direction flag to verify flag preservation.
    for reg, value in values.items():
        machine.reg_write(reg, value)
    calls = []

    def observe(uc, address, size, user_data):
        if address == original:
            calls.append(True)
            assert {reg: uc.reg_read(reg) for reg in values} == values
            assert bytes(uc.mem_read(STACK + 4, 4)) == pack(success)

    machine.hook_add(UC_HOOK_CODE, observe)
    machine.emu_start(CONTROL + CODE_OFFSET, RETURN, count=2000)
    assert calls == [True]  # Even rejected captures must forward exactly once.
    assert machine.reg_read(UC_X86_REG_ESP) == STACK + 4  # cdecl return, arg not popped
    assert machine.reg_read(UC_X86_REG_EAX) == success
    return struct.unpack("<I", machine.mem_read(CONTROL + 8, 4))[0]


@pytest.mark.parametrize("index", range(12))
@pytest.mark.parametrize("uppercase", [False, True])
def test_real_x86_thunk_captures_campaign_map(index, uppercase):
    name = MISSIONS[index].map_name + ".mix"
    name = name.upper() if uppercase else name.lower()
    assert emulate(name.encode() + b"\0") == 1 << index


@pytest.mark.parametrize("kwargs", [
    {"success": 0}, {"mode": 0}, {"mode": 2}, {"allowed": 0}, {"allowed": 2},
    {"heartbeat": 1}, {"game_pointer": 0}, {"map_pointer": 0},
    {"map_name": b"M13.mod\0"}, {"map_name": b"M13.mix-extra\0"},
    {"map_name": b"M00_Tutorial.mix\0"}, {"map_name": b"C&C_Field.mix\0"},
    {"map_name": b"M12.mix\0"}, {"map_name": b"M13.lsd-extra\0"},
])
def test_invalid_or_unarmed_events_still_forward_original(kwargs):
    assert emulate(**kwargs) == 0


def test_latched_bits_and_tick_count_wrap():
    assert emulate(initial=2) == 3
    assert emulate(now=100, heartbeat=0xFFFFFFF0) == 1
    assert emulate(now=3000, heartbeat=0xFFFFFFF0) == 0


class FakeMemory:
    def __init__(self):
        self.values = {}
        self.field = 0x400000 + STEAM.commands + STEAM.mission_command_offset
        self.original = 0x400000 + STEAM.mission_complete
        self.write_u32(self.field, self.original)
        self.fail_replacement = False

    def write(self, address, data):
        self.values.update({address + i: value for i, value in enumerate(data)})

    def read(self, address, size):
        return bytes(self.values.get(address + i, 0) for i in range(size))

    def u32(self, address):
        return struct.unpack("<I", self.read(address, 4))[0]

    def write_u32(self, address, value):
        self.write(address, pack(value))
        if address == self.field and value != self.original and self.fail_replacement:
            raise OSError("uncertain callback write")

    def allocate(self):
        return CONTROL

    def make_executable(self, address):
        assert address == CONTROL + CODE_OFFSET

    def tick_count(self):
        return 10000


@pytest.fixture
def memory(monkeypatch):
    monkeypatch.setattr("renegade.native.bridge.verify", lambda _: SimpleNamespace(base=0x400000))
    return FakeMemory()


def test_hook_arms_reads_disarms_and_restores(memory):
    hook = MissionHook(memory)
    hook.install()
    assert memory.u32(memory.field) == CONTROL + CODE_OFFSET
    assert not hook.update(["M13", "M01"])
    assert memory.u32(CONTROL) == 3
    assert memory.u32(CONTROL + 4) == 10000
    memory.write_u32(CONTROL + 8, 3)
    assert hook.update(["M13", "M01"]) == ("M13", "M01")
    hook.update([])
    assert memory.u32(CONTROL) == 0
    hook.close()
    assert memory.u32(memory.field) == memory.original
    hook.close()  # Idempotent cleanup.


def test_hook_restores_after_uncertain_install(memory):
    hook = MissionHook(memory)
    memory.fail_replacement = True
    with pytest.raises(OSError):
        hook.install()
    assert memory.u32(memory.field) == memory.original
    assert not hook.installed


def test_hook_does_not_overwrite_another_owner(memory):
    hook = MissionHook(memory)
    hook.install()
    memory.write_u32(memory.field, 0x60000000)
    hook.close()
    assert memory.u32(memory.field) == 0x60000000
    assert memory.u32(CONTROL) == 0


def test_install_rechecks_original_callback(memory):
    hook = MissionHook(memory)
    memory.write_u32(memory.field, 0x60000000)
    with pytest.raises(UnsupportedBuild):
        hook.install()
    assert memory.u32(memory.field) == 0x60000000


def test_save_failure_keeps_captured_event_until_retry(monkeypatch):
    from renegade.native.bridge import watch

    async def run():
        class Hook:
            memory = SimpleNamespace(pid=1, close=lambda: None)
            closed = False

            def update(self, maps):
                return ("M13",)

            def captured(self):
                return ("M13",)

            def close(self):
                self.closed = True

        class Client:
            ready = True
            capture_ready = True
            capture_maps = ('M13',)
            identity = ("seed", 0, 1)
            unlocked_maps = ("M13",)
            stop = asyncio.Event()
            attempts = 0

            def mission_finished(self, name, success):
                self.attempts += 1
                if self.attempts == 1:
                    raise OSError("temporary disk error")
                self.stop.set()
                return True

        hook = Hook()
        client = Client()
        monkeypatch.setattr("renegade.native.bridge.attach", lambda pid: hook)
        monkeypatch.setattr("renegade.native.bridge.telemetry", lambda memory: {"mode": 1, "map_name": "M13.mix"})
        await asyncio.wait_for(watch(client), 1)
        assert client.attempts == 2
        assert hook.closed

    asyncio.run(run())


@pytest.mark.parametrize("change", ["size", "version", "scripts_pointer", "callback", "anchor"])
def test_live_profile_rejects_changed_runtime(monkeypatch, change):
    from pathlib import Path
    from renegade.native.inspect import verify
    memory = FakeMemory()
    memory.modules = [SimpleNamespace(base=0x400000, name="Game.dat", path=Path("Game.dat")),
                      SimpleNamespace(base=0x10000000, name="Scripts.dll", path=Path("Scripts.dll"))]
    monkeypatch.setattr("renegade.native.inspect.require_digest", lambda path, expected: expected)
    table = 0x400000 + STEAM.commands
    memory.write_u32(table, STEAM.commands_size)
    memory.write_u32(table + 4, STEAM.commands_version)
    memory.write_u32(0x10000000 + STEAM.scripts_commands, table)
    for rva, expected in STEAM.anchors.items():
        memory.write(0x400000 + rva, expected)
    assert verify(memory) is memory.modules[0]
    address = {"size": table, "version": table + 4,
               "scripts_pointer": 0x10000000 + STEAM.scripts_commands,
               "callback": memory.field, "anchor": 0x400000 + STEAM.mission_complete}[change]
    memory.write_u32(address, 0)
    with pytest.raises(UnsupportedBuild):
        verify(memory)


def test_capture_survives_game_resetting_command_table(memory):
    hook = MissionHook(memory)
    hook.install()
    memory.write_u32(CONTROL + 8, 1)
    memory.write_u32(memory.field, memory.original)  # Engine reloads Scripts.dll's table.
    assert hook.captured() == ("M13",)
    with pytest.raises(OSError):
        hook.update(["M13"])
    hook.close()
    assert memory.u32(memory.field) == memory.original


def test_save_load_completion_uses_loaded_level_not_save_filename():
    assert emulate(map_name=b"M01.lsd\0", source_name=b"save\\arbitrary.sav\0") == 2
    assert emulate(map_name=b"C&C_Field.lsd\0", source_name=b"M13.mix\0") == 0

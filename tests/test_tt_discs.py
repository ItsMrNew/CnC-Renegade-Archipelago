"""Execute TT disc programs: physical evidence, current player and native ABI."""
from functools import partial
import asyncio

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_ESP, UC_X86_REG_ECX, UC_X86_REG_EBP,
    UC_X86_REG_EBX, UC_X86_REG_ESI, UC_X86_REG_EDI)

from renegade.data import MISSIONS, DATA_DISCS, disc_id
from renegade.native import tt_discs as td, discs as d, services as s, tt
from renegade.native.thunk import u32, CODE_OFFSET
from renegade.native.profile import UnsupportedBuild
from test_tt_native import Engine, Memory, memory
from test_native import CONTROL, OBJECT, STACK, RETURN, MAP

DCTRL, POWERUP, DEF = 0x12000000, 0x21000000, 0x21001000
PLAYER, CAMERA, MODE = OBJECT+4096, OBJECT+8192, OBJECT+256
CASES = [(m, oid) for m in MISSIONS for oid in DATA_DISCS[m.map_name] if oid>=0]


class DiscEngine(Engine):
    def __init__(self, mission=MISSIONS[1], oid=116992, band=0x60000000):
        super().__init__(mission, band)
        uc = self.uc
        uc.mem_map(DCTRL, 16384); uc.mem_map(POWERUP, 16384)
        self.put(DCTRL+d.ENABLED, 1)
        self.put(CONTROL+td.COMBAT_OBJECT, MODE)
        self.put(td.STAR, OBJECT+512); self.put(OBJECT+516, PLAYER)
        self.put(td.CAMERA, CAMERA)
        uc.mem_write(PLAYER+s.CONTROL_ENABLED, b'\1')
        self.put(DEF+2048, 0x42C80000)
        uc.mem_write(td.GET_HEALTH, b'\xd9\x05'+u32(DEF+2048)+b'\xc3')
        self.put(POWERUP+12, oid or 999999)
        self.put(POWERUP+0x6BC, DEF)
        self.put(DEF, DEF+128); self.put(DEF+128+0x24, DEF+256)
        uc.mem_write(DEF+256, b'\xb8'+u32(d.DISC_DEFINITION)+b'\xc3')
        self.put(POWERUP+0x6F0, DEF+512); self.put(POWERUP+0x6F8, 1)
        self.put(DEF+512, DEF+640); self.put(DEF+640, DEF+768)
        self.put(DEF+768+4, DEF+896)
        uc.mem_write(DEF+896, b'\xb8'+u32(DEF+1024)+b'\xc3')
        uc.mem_write(DEF+1024, d.PRIEST_SCRIPT)
        uc.mem_write(self.a.band_base+td.GRANT,
                     b'\xc7\x81\xb4\x07\x00\x00'+u32(2)+b'\xc2\x04\x00')
        guarded = partial(td.guard, addresses=self.a)
        uc.mem_write(DCTRL+4096, d.build_pickup(DCTRL+4096, DCTRL, CONTROL,
            guard_builder=guarded, grant=self.a.band_base+td.GRANT, checkpoint=False))
        uc.mem_write(DCTRL+8192, d.build_reveal(DCTRL+8192, DCTRL, CONTROL,
            guard_builder=guarded, cloud_vector=td.CLOUD_VECTOR))
        uc.mem_write(td.CLOUD_VECTOR, b'\xff'*52)
        self.grants = 0
        def observe(uc, address, size, data):
            if address == self.a.band_base+td.GRANT:
                self.grants += 1
                assert uc.reg_read(UC_X86_REG_ECX) == POWERUP
        uc.hook_add(UC_HOOK_CODE, observe)

    def execute(self, address, args=()):
        uc = self.uc
        regs = {UC_X86_REG_EBP: 0x12345678, UC_X86_REG_EBX: 0xCAFE,
                UC_X86_REG_ESI: 0x1234, UC_X86_REG_EDI: 0x5678}
        for reg, value in regs.items(): uc.reg_write(reg, value)
        uc.reg_write(UC_X86_REG_ECX, POWERUP)
        uc.reg_write(UC_X86_REG_ESP, STACK)
        uc.mem_write(STACK, u32(RETURN)+b''.join(u32(arg) for arg in args))
        uc.emu_start(address, RETURN, count=20000)
        assert uc.reg_read(UC_X86_REG_ESP) == STACK+4+len(args)*4
        assert all(uc.reg_read(reg) == value for reg, value in regs.items())

    def reveal(self): self.execute(DCTRL+8192)
    def pickup(self, player=PLAYER): self.execute(DCTRL+4096, (player,))
    def captured(self):
        return int.from_bytes(self.uc.mem_read(DCTRL+d.CAPTURED+(self.mission.number-1)*4, 4), 'little')


@pytest.mark.parametrize('mission,oid', CASES)
@pytest.mark.parametrize('band', [0x60000000, 0x71000000])
def test_tt_physical_disc_capture_reveal_and_duplicates(mission, oid, band):
    e = DiscEngine(mission, oid, band)
    e.reveal()
    assert bytes(e.uc.mem_read(td.CLOUD_VECTOR, 52)) == b'\0'*52
    assert e.captured() == 0
    e.pickup(); e.pickup()
    assert e.captured() == 1 << DATA_DISCS[mission.map_name].index(oid)
    assert e.grants == 2
    assert e.read(s.DISC_EVENT) == 0  # TT checkpoint service has not been ported.


@pytest.mark.parametrize('reason', ['disabled', 'stale', 'menu', 'multiplayer', 'no_game',
    'paused', 'no_mode', 'locked', 'no_star', 'no_player', 'no_control', 'no_camera',
    'cinematic', 'dead', 'negative', 'nan', 'infinite', 'victory', 'map', 'suffix'])
def test_tt_reveal_and_capture_reject_unplayable_state(reason):
    e = DiscEngine()
    changes = {
        'disabled': (CONTROL+s.ENABLED, 0), 'stale': (CONTROL+s.HEARTBEAT, 1),
        'menu': (e.a.game_mode, 0), 'multiplayer': (e.a.game_mode, 2),
        'no_game': (e.a.game_data, 0), 'paused': (MODE+4, 1),
        'no_mode': (CONTROL+td.COMBAT_OBJECT, 0), 'locked': (CONTROL+s.ALLOWED, 1),
        'no_star': (td.STAR, 0), 'no_player': (OBJECT+516, 0),
        'no_control': (PLAYER+s.CONTROL_ENABLED, 0), 'no_camera': (td.CAMERA, 0),
        'cinematic': (CAMERA+s.CAMERA_HOST, OBJECT), 'dead': (DEF+2048, 0),
        'negative': (DEF+2048, 0xBF800000), 'nan': (DEF+2048, 0x7FC00000),
        'infinite': (DEF+2048, 0x7F800000), 'victory': (e.a.pending_continue, 1),
    }
    if reason in changes: e.put(*changes[reason])
    if reason == 'map': e.uc.mem_write(MAP, b'C&C_M01.mix\0')
    if reason == 'suffix': e.uc.mem_write(MAP, b'M01.lsd.bad\0')
    e.reveal(); e.pickup()
    assert e.captured() == 0 and e.read(s.PLAYABLE) == 0
    assert e.grants == 1  # Never suppress the native pickup.
    assert bytes(e.uc.mem_read(td.CLOUD_VECTOR, 52)) == b'\xff'*52


@pytest.mark.parametrize('reason', ['yaml_off', 'npc', 'old_player', 'failed', 'wrong_definition', 'unknown'])
def test_tt_only_successful_current_player_pickups_award_checks(reason):
    e = DiscEngine()
    player = PLAYER
    if reason == 'yaml_off': e.put(DCTRL+d.ENABLED, 0)
    if reason == 'npc': player = PLAYER+2048
    if reason == 'old_player':
        e.put(OBJECT+516, PLAYER+2048)
        e.uc.mem_write(PLAYER+2048+s.CONTROL_ENABLED, b'\1')
    if reason == 'failed': e.uc.mem_write(e.a.band_base+td.GRANT, b'\xc2\x04\x00')
    if reason == 'wrong_definition': e.uc.mem_write(DEF+256, b'\x31\xc0\xc3')
    if reason == 'unknown':
        e.put(POWERUP+12, 123); e.put(POWERUP+0x6F8, 0)
    e.reveal(); e.pickup(player)
    assert e.captured() == 0
    assert bytes(e.uc.mem_read(td.CLOUD_VECTOR, 52)) == b'\0'*52


@pytest.mark.parametrize('name', [b'M01_Priest_Datadisc_JDG_fake\0', b'M01_Priest\0', b'\0'])
def test_tt_dynamic_disc_requires_exact_observer(name):
    e = DiscEngine(oid=0); e.uc.mem_write(DEF+1024, name)
    e.pickup(); assert not e.captured()


def test_tt_frame_integration_preserves_combat_this_and_original_call():
    e = DiscEngine()
    e.run(tt.build_scan(CONTROL, e.a, DCTRL+8192))
    assert e.read(td.COMBAT_OBJECT) == MODE
    assert e.calls == ['think'] and e.read(s.PLAYABLE) == 1
    assert bytes(e.uc.mem_read(td.CLOUD_VECTOR, 52)) == b'\0'*52


def test_tt_disc_hook_cleanup_and_rebuilt_call(memory):
    session = tt.TTSession(memory)
    disc = session.discs
    session.update(('M01',), True)
    assert memory.read(disc.site, 5) == disc.replacement
    memory.write(disc.site, disc.original)
    session.update(('M01',), True)
    assert memory.read(disc.site, 5) == disc.replacement
    session.close()
    assert memory.read(disc.site, 5) == disc.original
    assert memory.u32(disc.control+d.ENABLED) == 0


def test_tt_unknown_disc_hook_refused_and_preserved(memory):
    session = tt.TTSession(memory)
    memory.write(session.discs.site, b'\xe8\0\0\0\0')
    with pytest.raises(OSError): session.update(('M01',), True)
    session.close()
    assert memory.read(session.discs.site, 5) == b'\xe8\0\0\0\0'


@pytest.mark.parametrize('address', list(td.expected_anchors(Memory().a)))
def test_tt_altered_disc_layout_refused_before_patching(memory, address):
    original = memory.read(memory.a.combat_think_slot, 4)
    memory.write(address, b'\xcc')
    with pytest.raises(UnsupportedBuild): tt.TTSession(memory)
    assert memory.read(memory.a.combat_think_slot, 4) == original


def test_tt_disconnect_drains_pickup_during_hook_restoration(memory, monkeypatch, tmp_path):
    from renegade.client import RenegadeClient
    from test_core import Socket
    from test_primary_objectives import connect
    from test_discs import config

    async def run():
        client = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path)
        await connect(client, Socket(), config())
        runtime = tt.TTSession(memory)
        close = runtime.close
        def capture_while_closing():
            memory.write_u32(runtime.discs.control+d.CAPTURED+4, 1)
            close()
        runtime.close = capture_while_closing
        memory.close = lambda: None
        monkeypatch.setattr(tt, 'attach', lambda pid, **kwargs: runtime)
        def attached(value):
            client.connected = False  # Earlier authenticated permissions remain valid.
            client.stop.set()
        await tt.watch(client, on_attach=attached)
        assert disc_id('M01', 116992) in client.progress.completed
        assert memory.read(runtime.discs.site, 5) == runtime.discs.original
    asyncio.run(run())


@pytest.mark.parametrize('reason', ['other_room', 'unauthenticated', 'locked', 'yaml_off'])
def test_tt_final_drain_respects_authenticated_room_and_catalogue(reason, tmp_path):
    from types import SimpleNamespace
    from renegade.client import RenegadeClient
    from test_core import Socket
    from test_primary_objectives import connect
    from test_discs import config

    async def run():
        client = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path)
        await connect(client, Socket(), config(reason != 'yaml_off'))
        identity = client.identity
        if reason == 'other_room': identity = ('other', 0, 1)
        if reason == 'unauthenticated': client.capture_permissions = None
        if reason == 'locked': client.capture_permissions = ('M13',)
        client.stop.set()
        tt.persist_final_captures(client, identity, SimpleNamespace(pending=set()),
                                  SimpleNamespace(pending={('M01', 116992)}), set())
        assert not client.progress.completed
    asyncio.run(run())

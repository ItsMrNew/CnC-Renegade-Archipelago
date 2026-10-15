"""Execute the exact x86 service program for checkpoints and DeathLink."""
import faulthandler
import struct
import pytest
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
    UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_EFLAGS)
from renegade.native.services import *
from renegade.data import SECONDARY_OBJECTIVES

CONTROL, OBJECT, STACK, RETURN, TICK = 0x10000000, 0x20000000, 0x30008000, 0x40000100, 0x40000000


class Engine:
    def __init__(self, mission=MISSIONS[1], objectives=None, testing=False, test_objective_arguments=True):
        self.mission = mission
        self.machine = uc = Uc(UC_ARCH_X86, UC_MODE_32)
        enabled = faulthandler.is_enabled()
        faulthandler.disable()
        try:
            for address, size in [(BASE, 0x500000), (CONTROL, 8192), (OBJECT, 16384),
                                  (0x30000000, 65536), (TICK, 4096)]:
                uc.mem_map(address, size)
        finally:
            if enabled:
                faulthandler.enable()
        uc.mem_write(CONTROL + CODE_OFFSET, build_services(CONTROL, testing=testing))
        uc.mem_write(BASE + STEAM.tick_iat, u32(TICK))
        uc.mem_write(TICK, b'\xb8' + u32(10000) + b'\xc3')
        for offset, value in [(ENABLED, 1), (ONLINE, 1), (ALLOWED, 0xFFF), (HEARTBEAT, 9900),
                              (SECONDARIES, 1), (DEATH_ENABLED, 1), (MAP_INDEX, 0xFFFFFFFF)]:
            self.set(offset, value)
        self.put(BASE + STEAM.game_mode, 1)
        self.put(BASE + STEAM.game_data, OBJECT)
        self.set(OBSERVER,0x15001000)
        self.put(BASE+STEAM.commands+0x204,0x15001000)
        self.put(BASE + STEAM.loaded_map, OBJECT + 128)
        uc.mem_write(OBJECT + 128, (mission.map_name + '.lsd\0').encode())
        self.put(BASE + STAR, OBJECT + 512)
        self.put(OBJECT + 516, OBJECT + 768)
        self.put(BASE + COMBAT_CAMERA, OBJECT + 8192)
        self.machine.mem_write(OBJECT + 768 + CONTROL_ENABLED, b'\x01')
        self.put(OBJECT + 4, 0)  # Active mode.
        self.health(100)
        uc.mem_write(BASE + GET_HEALTH, b'\xd9\x05' + u32(OBJECT + 256) + b'\xc3')
        uc.mem_write(BASE + APPLY_DAMAGE, b'\xc7\x05' + u32(OBJECT + 256) + u32(0) + b'\xc3')
        uc.mem_write(BASE + SAVE_GAME, b'\xb8' + u32(1) + b'\xc3')
        uc.mem_write(BASE + STEAM.combat_think, b'\xc3')
        uc.mem_write(BASE + 0x35EE, bytes.fromhex('5f83c41cc3'))
        uc.mem_write(BASE + ADD_OBJECTIVE, b'\xc3')
        uc.mem_write(BASE + SET_OBJECTIVE_STATUS, b'\xc7\x05'+u32(OBJECT+2048+3*16+8)+u32(1)+b'\xc3')
        cursor = CONTROL + RECORDS
        for index, m in enumerate(MISSIONS):
            ids = OBJECTIVES[m.map_name]
            uc.mem_write(CONTROL + DESCRIPTORS + index*8, u32(cursor) + u32(len(ids)))
            for bit, oid in enumerate(ids):
                kind = 1 if objective_kind(m.map_name, oid) == 'primary' else 2
                uc.mem_write(cursor, u32(oid) + u32(1 << bit) + u32(kind))
                cursor += RECORD_SIZE
            uc.mem_write(CONTROL + FILES + index*64, ('save\\test_' + m.map_name + '.apstage\0').encode())
        uc.mem_write(CONTROL + WARHEAD, b'BlamoKiller\0')
        self.objectives = list(objectives or SECONDARY_OBJECTIVES[mission.map_name])
        self.put(BASE + OBJECTIVE_COUNT, len(self.objectives))
        self.put(BASE + OBJECTIVE_ARRAY, OBJECT + 1024)
        for i, oid in enumerate(self.objectives):
            self.put(OBJECT + 1024 + i*4, OBJECT + 2048 + i*16)
            uc.mem_write(OBJECT + 2048 + i*16, u32(oid) + u32(2) + u32(0))
        self.calls = []
        def observe(machine, address, size, data):
            if address == BASE + ADD_OBJECTIVE:
                self.calls.append('add_objective')
                esp=machine.reg_read(UC_X86_REG_ESP)
                assert bytes(machine.mem_read(esp+4,24))==b''.join(u32(n) for n in (104,2,0,6268,0,0))
            if address == BASE + SET_OBJECTIVE_STATUS and test_objective_arguments:
                self.calls.append('complete_objective')
                esp=machine.reg_read(UC_X86_REG_ESP)
                assert bytes(machine.mem_read(esp+4,8))==u32(104)+u32(1)
            if address == BASE + SAVE_GAME:
                self.calls.append('save')
                esp = machine.reg_read(UC_X86_REG_ESP)
                args = bytes(machine.mem_read(esp + 4, 12))
                assert args == u32(CONTROL + FILES + (mission.number-1)*64) + u32(BASE+COMMANDO_SAVE) + u32(0)
            if address == BASE + APPLY_DAMAGE:
                self.calls.append('death')
                esp = machine.reg_read(UC_X86_REG_ESP)
                assert bytes(machine.mem_read(esp+4, 16)) == u32(OBJECT+768) + struct.pack('<f',100000) + u32(CONTROL+WARHEAD) + u32(0)
            if address == BASE + STEAM.combat_think:
                self.calls.append('think')
                assert all(machine.reg_read(r) == val for r,val in self.registers.items())
        uc.hook_add(UC_HOOK_CODE, observe)

    def put(self, address, value): self.machine.mem_write(address, u32(value))
    def set(self, offset, value): self.put(CONTROL + offset, value)
    def read(self, offset): return int.from_bytes(self.machine.mem_read(CONTROL+offset,4), 'little')
    def health(self, value): self.machine.mem_write(OBJECT+256, struct.pack('<f', value))
    def objective(self, index, status=1, kind=2):
        self.put(OBJECT+2048+index*16+4, kind)
        self.put(OBJECT+2048+index*16+8, status)
    def frame(self):
        uc = self.machine
        regs = [UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP]
        self.registers = {r: 0x12300000+i for i,r in enumerate(regs)}
        self.registers.update({UC_X86_REG_ECX: OBJECT, UC_X86_REG_ESP: STACK, UC_X86_REG_EFLAGS: 0x646})
        for reg,value in self.registers.items(): uc.reg_write(reg,value)
        self.put(STACK, RETURN)
        uc.emu_start(CONTROL+CODE_OFFSET, RETURN, count=30000)
        assert uc.reg_read(UC_X86_REG_ESP) == STACK+4
        assert self.read(BUSY) == 0


@pytest.mark.parametrize('mission,oid', [(m,oid) for m in MISSIONS for oid in SECONDARY_OBJECTIVES[m.map_name]])
def test_every_secondary_captures_and_checkpoints_once(mission, oid):
    engine = Engine(mission, [oid])
    engine.frame()
    assert 'save' not in engine.calls
    engine.objective(0)
    engine.frame()
    bit = SECONDARY_OBJECTIVES[mission.map_name].index(oid)
    assert engine.read(CAPTURED+(mission.number-1)*4) == 1 << bit
    assert engine.read(SAVES+(mission.number-1)*4) == 1
    engine.frame()
    assert engine.calls.count('save') == 1


@pytest.mark.parametrize('status,kind', [(0,2),(2,2),(3,2),(1,1),(1,3)])
def test_pending_failed_hidden_primary_tertiary_do_not_grant_checks(status,kind):
    e=Engine();e.frame();e.objective(0,status,kind);e.frame()
    assert e.read(CAPTURED+4) == 0 and 'save' not in e.calls


def test_precompleted_loaded_save_grants_check_without_overwriting_checkpoint():
    e=Engine();e.objective(0);e.frame()
    assert e.read(CAPTURED+4) == 1 and 'save' not in e.calls
    e.objective(1);e.frame()
    assert e.read(CAPTURED+4) == 3 and e.calls.count('save') == 1


def test_natural_death_counted_once_but_loaded_corpse_not_counted():
    e=Engine();e.health(0);e.frame();assert e.read(DEATH_COUNT)==0
    e.health(100);e.frame();e.health(0);e.frame();e.frame()
    assert e.read(DEATH_COUNT)==1


def test_received_death_kills_once_without_echo():
    e=Engine();e.frame()
    e.set(DEATH_GENERATION,e.read(GENERATION))
    e.set(REQUEST_PLAYER,OBJECT+768);e.set(REQUEST_MAP,1);e.set(DEATH_REQUEST,1)
    e.frame();e.frame()
    assert e.calls.count('death')==1 and e.read(DEATH_APPLIED)==1
    assert e.read(DEATH_COUNT)==0 and e.read(ALIVE)==0


@pytest.mark.parametrize('setup', ['stale','disabled','no_player','multiplayer','inactive','locked'])
def test_inactive_services_do_not_save_or_apply_death(setup):
    e=Engine();e.frame();e.objective(0);e.set(DEATH_REQUEST,1)
    if setup=='stale':e.set(HEARTBEAT,1)
    if setup=='disabled':e.set(ENABLED,0)
    if setup=='no_player':e.put(BASE+STAR,0)
    if setup=='multiplayer':e.put(BASE+STEAM.game_mode,2)
    if setup=='inactive':e.put(OBJECT+4,3)
    if setup=='locked':e.set(ALLOWED,1)
    e.frame()
    assert 'save' not in e.calls and 'death' not in e.calls
    assert e.read(DEATH_REQUEST)==int(setup=='inactive')


def test_stale_received_death_cannot_kill_different_player():
    e=Engine();e.frame();e.set(DEATH_REQUEST,1);e.set(REQUEST_PLAYER,123);e.set(REQUEST_MAP,1);e.frame()
    assert 'death' not in e.calls


@pytest.mark.parametrize('reason', ['cinematic', 'no_camera', 'no_control', 'victory', 'paused'])
def test_posted_death_waits_when_control_is_temporarily_removed(reason):
    e = Engine(); e.frame()
    e.set(DEATH_GENERATION,e.read(GENERATION))
    e.set(REQUEST_PLAYER, OBJECT+768); e.set(REQUEST_MAP, 1); e.set(DEATH_REQUEST, 1)
    if reason == 'cinematic': e.put(OBJECT+8192+CAMERA_HOST, OBJECT)
    if reason == 'no_camera': e.put(BASE+COMBAT_CAMERA, 0)
    if reason == 'no_control': e.machine.mem_write(OBJECT+768+CONTROL_ENABLED, b'\x00')
    if reason == 'victory': e.machine.mem_write(BASE+STEAM.pending_continue, b'\x01')
    if reason == 'paused': e.put(OBJECT+4, 1)
    e.frame()
    assert e.read(PLAYABLE) == 0 and e.read(DEATH_REQUEST) == 1
    assert 'death' not in e.calls
    e.put(OBJECT+8192+CAMERA_HOST, 0); e.put(BASE+COMBAT_CAMERA, OBJECT+8192)
    e.machine.mem_write(OBJECT+768+CONTROL_ENABLED, b'\x01')
    e.machine.mem_write(BASE+STEAM.pending_continue, b'\x00')
    e.put(BASE+STEAM.game_mode, 1); e.put(OBJECT+4, 0)
    e.frame()
    assert e.calls.count('death') == 1 and e.read(DEATH_APPLIED) == 1
    e.frame()
    assert e.calls.count('death') == 1 and e.read(DEATH_COUNT) == 0


@pytest.mark.parametrize('reason',['menu','generation','player','map','dead','offline'])
def test_posted_death_cannot_cross_load_identity_or_disconnect(reason):
    e=Engine();e.frame()
    e.set(DEATH_GENERATION,e.read(GENERATION));e.set(REQUEST_PLAYER,OBJECT+768)
    e.set(REQUEST_MAP,1);e.set(DEATH_REQUEST,1)
    if reason=='menu':e.put(BASE+STEAM.game_mode,0)
    elif reason=='generation':e.set(GENERATION,e.read(GENERATION)+1)
    elif reason=='player':e.set(REQUEST_PLAYER,123)
    elif reason=='map':e.set(REQUEST_MAP,2)
    elif reason=='dead':e.health(0)
    else:e.set(ONLINE,0)
    e.frame()
    assert 'death' not in e.calls
    if reason!='offline':assert e.read(DEATH_REQUEST)==0


@pytest.mark.parametrize('state',['menu','pause','cutscene','control','already_dead'])
def test_death_bridge_retains_incoming_until_a_playable_player(state):
    from collections import deque
    from types import SimpleNamespace
    from renegade.native.deathlink import NativeDeathLink
    e=Engine();e.frame();sent=[]
    mem=SimpleNamespace(u32=lambda a:int.from_bytes(e.machine.mem_read(a,4),'little'),write_u32=e.put)
    bridge=NativeDeathLink(mem,CONTROL)
    client=SimpleNamespace(ready=True,deathlink=SimpleNamespace(enabled=True,incoming=deque([{},{}])),player_died=sent.append)
    if state=='menu':e.put(BASE+STEAM.game_mode,0)
    elif state=='pause':e.put(OBJECT+4,3)
    elif state=='cutscene':e.put(OBJECT+8192+CAMERA_HOST,OBJECT)
    elif state=='control':e.machine.mem_write(OBJECT+768+CONTROL_ENABLED,b'\0')
    else:e.health(0)
    e.frame();bridge.update(client)
    assert len(client.deathlink.incoming)==2 and e.read(DEATH_REQUEST)==0
    sent.clear()
    e.put(BASE+STEAM.game_mode,1);e.put(OBJECT+4,0)
    e.put(OBJECT+8192+CAMERA_HOST,0);e.machine.mem_write(OBJECT+768+CONTROL_ENABLED,b'\1');e.health(100)
    e.frame();bridge.update(client);e.frame();bridge.update(client)
    assert not client.deathlink.incoming and e.read(DEATH_APPLIED)==1 and not sent
    client.deathlink.incoming.append({});client.ready=False;bridge.update(client)
    assert not client.deathlink.incoming and not e.read(DEATH_REQUEST) and not e.read(DEATH_ENABLED)


def test_cinematic_objective_captures_check_but_defers_checkpoint():
    e = Engine(); e.frame()
    e.put(OBJECT+8192+CAMERA_HOST, OBJECT); e.objective(0); e.frame()
    assert e.read(CAPTURED+4) == 1 and 'save' not in e.calls
    e.put(OBJECT+8192+CAMERA_HOST, 0); e.frame(); e.frame()
    assert e.calls.count('save') == 1


def test_dead_player_or_pending_victory_does_not_overwrite_checkpoint():
    for dead in (False,True):
        e=Engine();e.frame();e.objective(0)
        if dead:e.health(0)
        else:e.machine.mem_write(BASE+STEAM.pending_continue,b'\x01')
        e.frame();assert 'save' not in e.calls and e.read(CAPTURED+4)==1


def test_test_only_objective_and_death_jobs_use_real_engine_abis():
    e=Engine(testing=True);e.frame()
    e.set(TEST_ACTION,1);e.frame();assert e.read(TEST_RESULT)==1
    e.set(TEST_ACTION,2);e.frame();assert e.read(TEST_RESULT)==2
    assert e.read(CAPTURED+4)==8 and e.calls.count('save')==1
    e.set(TEST_ACTION,3);e.frame();assert e.read(TEST_RESULT)==3
    assert e.read(DEATH_COUNT)==1 and e.read(DEATH_APPLIED)==0
    e.frame();assert e.read(DEATH_COUNT)==1


def test_regular_native_services_ignore_test_jobs():
    e=Engine();e.frame();e.set(TEST_ACTION,3);e.frame()
    assert 'death' not in e.calls and e.read(DEATH_COUNT)==0


def test_death_is_detected_when_death_popup_suspends_combat():
    e=Engine();e.frame();e.health(0);e.put(OBJECT+4,3);e.frame()
    assert e.read(DEATH_COUNT)==1

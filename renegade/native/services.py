"""Game-thread primary/secondary-objective checkpoints and opt-in DeathLink.

Runs behind the gameplay gate, using a separate retained data/code allocation.
Uses combat and objective callbacks; no remote threads are used.
"""
import asyncio
import hashlib
import logging
import struct

from ..data import MISSIONS, OBJECTIVES, WEAPONS, objective_kind
from .gate import GameplayGate, verify_gate
from .memory import ProcessMemory, MemoryError, candidates
from .profile import STEAM, UnsupportedBuild, require_digest
from .savebuilder import BASE, STAR, SAVE_GAME, COMMANDO_SAVE
from .savefiles import inspect_save
from .checkpoints import publish_checkpoint
from .thunk import Code, u32, CODE_OFFSET, LEASE_MS

LOG = logging.getLogger("Renegade.Services")
OBJECTIVE_ARRAY, OBJECTIVE_COUNT = 0x456D74, 0x456D7C
GET_HEALTH, APPLY_DAMAGE = 0x1A9E50, 0x1AB980
ALLOWED, HEARTBEAT, ENABLED, BUSY = 0, 4, 8, 12
PLAYER, HEALTH, ALIVE, MAP_INDEX, FRAME_MASK = 16, 20, 24, 28, 32
DEATH_COUNT, DEATH_REQUEST, DEATH_APPLIED, DEATH_FAILED = 36, 40, 44, 48
SECONDARIES, DEATH_ENABLED, REQUEST_PLAYER, REQUEST_MAP = 52, 56, 60, 64
ACTIVE = 68
PLAYABLE = 72  # Active combat with player control, no cinematic or victory transition.
GENERATION = 76  # Changes on player/map replacement; temporary effects cannot cross loads.
OBSERVER = 88  # Expected completion callback; a rebuilt command table invalidates effects.
ONLINE = 92  # Incoming effects require a synchronized connection, capture does not.
DISC_EVENT, DISC_GENERATION = 96, 100  # Pickup awaiting a safe frame in the same attempt.
DEATH_GENERATION, DEATH_MAP = 112, 116
COMBAT_CAMERA, CAMERA_HOST, CONTROL_ENABLED = 0x456760, 0x380, 0x83C
TEST_ACTION, TEST_RESULT = 80, 84
ADD_OBJECTIVE, SET_OBJECTIVE_STATUS = 0x1AB3C0, 0x1AB400
CAPTURED, SAVES, SAVE_RESULTS = 128, 192, 256
OBJECTIVE_EVENTS = 320  # New completion calls awaiting a safe checkpoint frame.
PUBLISHED = 368  # Per-map save serials validated/published by Python before a queued exit.
DESCRIPTORS, RECORDS, FILES, WARHEAD = 512, 1280, 2688, 3520
RECORD_SIZE = 12  # Objective ID, capture bit, expected native objective type.
CALLBACK_CAPTURED, LIST_CAPTURED, MISSING_AT_CALL, PRESENT_AT_CALL = 1088, 1136, 1184, 1232
SCRIPT_CAPTURED = 1040  # Persistent mission-script completion flags (12 map masks).
ANCHORS = {
    0x1084: bytes.fromhex("a16067850085c074208b888003000085c97516"),
    0x1AA390: bytes.fromhex("8b4c240485c974138b01ff502c85c0740a8a4c240888883c080000c3"),
    0xF3320: bytes.fromhex("8b157c6d8500535533c0565785d20f8ead0200008b35746d8500"),
    GET_HEALTH: bytes.fromhex("8b4c240485c974148b01ff503c85c0740b8d882c070000"),
    APPLY_DAMAGE: bytes.fromhex("8b4c240483ec285333db3bcb560f84d3000000"),
    SAVE_GAME: bytes.fromhex("81ec140c0000538b9c241c0c00005657"),
    0x365F: bytes.fromhex("a1f066850085c074748b480485c9746d"),
    0x35EE: bytes.fromhex("8b178bcfff522084c00f84c7020000"),
}


def build_services(control, testing=False, reward_target=None, notification_target=None, clock_target=None, bonus_target=None, weapon_target=None, disc_target=None, attempt_target=None, evidence_target=None, input_target=None):
    c = Code(control + CODE_OFFSET)
    def store(offset, value):
        c.emit(b"\xc7\x05" + u32(control + offset) + u32(value))
    def test(offset, value=0):
        c.emit(b"\x83\x3d" + u32(control + offset) + bytes([value]))
    def health():
        c.emit(b"\x56")  # cdecl Get_Health(player) -> x87 ST0, consumed immediately.
        c.jump(b"\xe8", BASE + GET_HEALTH)
        c.emit(b"\x83\xc4\x04\xd9\x1d" + u32(control + HEALTH))
        c.emit(b"\xa1" + u32(control + HEALTH) + b"\x85\xc0")

    c.emit(b"\x9c\x60")
    test(BUSY)
    c.jump(b"\x0f\x85", "hold")
    test(ENABLED)
    c.jump(b"\x0f\x84", "inactive")
    c.emit(b"\xff\x15" + u32(BASE + STEAM.tick_iat))
    c.emit(b"\x2b\x05" + u32(control + HEARTBEAT) + b"\x3d" + u32(LEASE_MS))
    c.jump(b"\x0f\x87", "inactive")
    c.emit(b"\x8b\x4c\x24\x18\x83\x79\x04\x00\x0f\x94\xc0\x0f\xb6\xc0\xa3" + u32(control + ACTIVE))
    c.emit(b"\x83\x3d" + u32(BASE + STEAM.game_mode) + b"\x01")
    c.jump(b"\x0f\x85", "inactive")
    c.emit(b"\x83\x3d" + u32(BASE + STEAM.game_data) + b"\x00")
    c.jump(b"\x0f\x84", "inactive")
    c.emit(b"\x8b\x15" + u32(BASE + STEAM.loaded_map) + b"\x85\xd2")
    c.jump(b"\x0f\x84", "inactive")
    # Exact basename matching with bounded byte reads, including the terminator.
    for index, mission in enumerate(MISSIONS):
        for suffix in (".lsd", ".mix"):
            label = f"next{index}{suffix}"
            for offset, char in enumerate((mission.map_name.lower() + suffix).encode() + b"\0"):
                c.emit(b"\x8a\x42" + bytes([offset]))
                if 97 <= char <= 122:
                    c.emit(b"\x0c\x20")
                c.emit(b"\x3c" + bytes([char]))
                c.jump(b"\x0f\x85", label)
            c.emit(b"\xbf" + u32(index))
            c.jump(b"\xe9", "map")
            c.label(label)
    c.jump(b"\xe9", "inactive")
    c.label("map")
    c.emit(b"\x0f\xa3\x3d" + u32(control + ALLOWED))  # bt permissions, map index
    c.jump(b"\x0f\x83", "inactive")
    c.emit(b"\xa1" + u32(BASE + STAR) + b"\x85\xc0")
    c.jump(b"\x0f\x84", "inactive")
    c.emit(b"\x8b\x70\x04\x85\xf6")
    c.jump(b"\x0f\x84", "inactive")
    if testing:
        # The automated M01 test may run while its console owns focus. Match the
        # save builder's tested startup behavior; production never overrides pause.
        c.emit(b"\x83\xff\x01")
        c.jump(b"\x0f\x85", "inactive")
        c.emit(b"\x8b\x4c\x24\x18\xc7\x41\x04" + u32(0))
        store(ACTIVE, 1)
    c.emit(b"\x3b\x35" + u32(control + PLAYER))
    c.jump(b"\x0f\x85", "new_player")
    c.emit(b"\x3b\x3d" + u32(control + MAP_INDEX))
    c.jump(b"\x0f\x84", "sample")
    c.label("new_player")
    c.emit(b'\xff\x05'+u32(control+GENERATION))
    store(ALIVE, 0)
    store(FRAME_MASK, 0xFFFFFFFF)  # Initial completed objectives grant checks, not a redundant save.
    store(DEATH_REQUEST, 0)
    store(DISC_EVENT, 0)
    c.emit(b"\x89\x35" + u32(control + PLAYER) + b"\x89\x3d" + u32(control + MAP_INDEX))
    c.label("sample")
    store(PLAYABLE, 0)
    test(ACTIVE, 1)
    c.jump(b"\x0f\x85", "playability_done")
    c.emit(b'\xa1'+u32(control+OBSERVER)+b'\x85\xc0')
    c.jump(b'\x0f\x84','playability_done')
    c.emit(b'\x3b\x05'+u32(BASE+STEAM.commands+0x204))
    c.jump(b'\x0f\x85','playability_done')
    c.emit(b"\xa1" + u32(BASE + COMBAT_CAMERA) + b"\x85\xc0")
    c.jump(b"\x0f\x84", "playability_done")
    c.emit(b"\x83\xb8" + u32(CAMERA_HOST) + b"\x00")
    c.jump(b"\x0f\x85", "playability_done")
    c.emit(b"\x80\xbe" + u32(CONTROL_ENABLED) + b"\x00")
    c.jump(b"\x0f\x84", "playability_done")
    c.emit(b"\x80\x3d" + u32(BASE + STEAM.pending_continue) + b"\x00")
    c.jump(b"\x0f\x85", "playability_done")
    store(PLAYABLE, 1)
    c.label("playability_done")
    store(BUSY, 1)
    c.emit(b"\xfc")
    health()
    c.jump(b"\x0f\x8e", "dead")
    c.emit(b"\x3d" + u32(0x7F800000))
    c.jump(b"\x0f\x83", "done")  # Reject NaN/infinite health.
    store(ALIVE, 1)
    test(PLAYABLE, 1)
    c.jump(b"\x0f\x85", "unplayable")
    if testing:
        # Only in the dedicated disposable-room test build: M01 objective 104,
        # and a local player death. Normal client code contains none of this.
        c.emit(b"\xa1" + u32(control + TEST_ACTION) + b"\x85\xc0")
        c.jump(b"\x0f\x84", "test_done")
        store(TEST_ACTION, 0)
        c.emit(b"\x83\xff\x01")
        c.jump(b"\x0f\x85", "test_rejected")
        c.emit(b"\x83\xf8\x01")
        c.jump(b"\x0f\x84", "test_add")
        c.emit(b"\x83\xf8\x02")
        c.jump(b"\x0f\x84", "test_complete")
        c.emit(b"\x83\xf8\x03")
        c.jump(b"\x0f\x85", "test_rejected")
        c.emit(b"\x6a\x00\x68" + u32(control + WARHEAD) + b"\x68" + struct.pack('<f', 100000.0) + b"\x56")
        c.jump(b"\xe8", BASE + APPLY_DAMAGE)
        c.emit(b"\x83\xc4\x10")
        store(TEST_RESULT, 3)
        health()
        c.jump(b"\x0f\x8e", "dead")
        c.jump(b"\xe9", "test_rejected")
        c.label("test_add")
        c.emit(b"\x6a\x00\x6a\x00\x68" + u32(6268) + b"\x6a\x00\x6a\x02\x6a\x68")
        c.jump(b"\xe8", BASE + ADD_OBJECTIVE)
        c.emit(b"\x83\xc4\x18")
        store(TEST_RESULT, 1)
        c.jump(b"\xe9", "test_done")
        c.label("test_complete")
        c.emit(b"\x6a\x01\x6a\x68")
        c.jump(b"\xe8", BASE + SET_OBJECTIVE_STATUS)
        c.emit(b"\x83\xc4\x08")
        store(TEST_RESULT, 2)
        c.jump(b"\xe9", "test_done")
        c.label("test_rejected")
        store(TEST_RESULT, 0xFFFFFFFF)
        c.label("test_done")
    test(DEATH_ENABLED)
    c.jump(b"\x0f\x84", "scan")
    test(ONLINE, 1)
    c.jump(b"\x0f\x85", "scan")
    test(DEATH_REQUEST, 1)
    c.jump(b"\x0f\x85", "scan")
    store(DEATH_REQUEST, 0)
    c.emit(b"\x3b\x35" + u32(control + REQUEST_PLAYER))
    c.jump(b"\x0f\x85", "scan")
    c.emit(b"\x3b\x3d" + u32(control + REQUEST_MAP))
    c.jump(b"\x0f\x85", "scan")
    c.emit(b'\xa1'+u32(control+GENERATION)+b'\x3b\x05'+u32(control+DEATH_GENERATION))
    c.jump(b'\x0f\x85', 'scan')
    c.emit(b"\x6a\x00\x68" + u32(control + WARHEAD) + b"\x68" + struct.pack('<f', 100000.0) + b"\x56")
    c.jump(b"\xe8", BASE + APPLY_DAMAGE)
    c.emit(b"\x83\xc4\x10")
    health()
    c.jump(b"\x0f\x8f", "kill_failed")
    store(ALIVE, 0)  # Received death is consumed here; never count it as a local death.
    store(PLAYABLE, 0)
    c.emit(b"\xff\x05" + u32(control + DEATH_APPLIED))
    c.jump(b"\xe9", "scan")
    c.label("kill_failed")
    c.emit(b"\xff\x05" + u32(control + DEATH_FAILED))
    c.jump(b"\xe9", "scan")
    c.label("dead")
    test(ALIVE, 1)
    c.jump(b"\x0f\x85", "not_new_death")
    c.emit(b'\x89\x3d'+u32(control+DEATH_MAP))
    c.emit(b"\xff\x05" + u32(control + DEATH_COUNT))
    c.label("not_new_death")
    store(ALIVE, 0)
    store(DEATH_REQUEST, 0)
    c.label("unplayable")  # Retain a posted death across pause/cutscenes until control returns.
    c.label("scan")
    test(ACTIVE, 1)
    c.jump(b"\x0f\x85", "paused")
    test(PLAYABLE, 1)
    c.jump(b"\x0f\x85", "scan_objectives")
    if notification_target:
        c.jump(b"\xe8", notification_target)
    if reward_target or weapon_target:
        test(ALIVE, 1)
        c.jump(b"\x0f\x85", "skip_rewards")
        c.emit(b"\x80\x3d" + u32(BASE + STEAM.pending_continue) + b"\x00")
        c.jump(b"\x0f\x85", "skip_rewards")
        if weapon_target:
            c.jump(b"\xe8", weapon_target)
        if reward_target:
            c.jump(b"\xe8", reward_target)
        c.label("skip_rewards")
    c.label("scan_objectives")
    if evidence_target:
        c.jump(b'\xe8', evidence_target)
    test(SECONDARIES)
    c.jump(b"\x0f\x84", "done")
    c.emit(b"\x8b\x0d" + u32(BASE + OBJECTIVE_COUNT) + b"\x81\xf9" + u32(128))
    c.jump(b"\x0f\x87", "done")
    c.emit(b"\x8b\x15" + u32(BASE + OBJECTIVE_ARRAY) + b"\x31\xed\x85\xc9")
    c.jump(b"\x0f\x84", "scanned")
    c.emit(b"\x85\xd2")
    c.jump(b"\x0f\x84", "done")
    c.label("objective")
    c.emit(b"\x8b\x02\x85\xc0")
    c.jump(b"\x0f\x84", "next_objective")
    c.emit(b"\x83\x78\x08\x01")
    c.jump(b"\x0f\x85", "next_objective")
    c.emit(b"\x8b\x18\x51\x52\xff\x70\x04")  # ebx=id, preserve iterator and native type
    c.emit(b"\x8b\x34\xfd" + u32(control + DESCRIPTORS))
    c.emit(b"\x8b\x04\xfd" + u32(control + DESCRIPTORS + 4))
    c.label("record")
    c.emit(b"\x85\xc0")
    c.jump(b"\x0f\x84", "record_done")
    c.emit(b"\x3b\x1e")
    c.jump(b"\x0f\x84", "record_match")
    c.emit(b"\x83\xc6\x0c\x48")
    c.jump(b"\xe9", "record")
    c.label("record_match")
    c.emit(b'\x8b\x0c\x24\x3b\x4e\x08')  # Only the correct type for this ID may complete it.
    c.jump(b'\x0f\x85', 'record_done')
    c.emit(b"\x0b\x6e\x04")
    c.label("record_done")
    c.emit(b"\x83\xc4\x04\x5a\x59")
    c.label("next_objective")
    c.emit(b"\x83\xc2\x04\x49")
    c.jump(b"\x0f\x85", "objective")
    c.label("scanned")
    c.emit(b"\x09\x2c\xbd" + u32(control + LIST_CAPTURED))
    c.emit(b"\x09\x2c\xbd" + u32(control + CAPTURED))
    test(PLAYABLE, 1)
    c.jump(b"\x0f\x85", "done")  # Retain checkpoint events until control returns.
    test(ALIVE, 1)
    c.jump(b"\x0f\x85", "done")
    c.emit(b"\x80\x3d" + u32(BASE + STEAM.pending_continue) + b"\x00")
    c.jump(b"\x0f\x85", "done")
    c.emit(b"\xa1" + u32(control + FRAME_MASK) + b"\xf7\xd0\x21\xe8")
    c.emit(b"\x31\xc9\x87\x0c\xbd" + u32(control + OBJECTIVE_EVENTS))
    c.emit(b"\x09\xc8")  # Event capture survives missing/reset objective-list entries.
    c.emit(b'\x31\xc9\x87\x0d'+u32(control+DISC_EVENT))
    c.emit(b'\x8b\x15'+u32(control+DISC_GENERATION)+b'\x3b\x15'+u32(control+GENERATION))
    c.jump(b'\x0f\x85', 'disc_checkpoint_done')
    c.emit(b'\x09\xc8')
    c.label('disc_checkpoint_done')
    c.emit(b"\x89\x2d" + u32(control + FRAME_MASK) + b"\x85\xc0")
    c.jump(b"\x0f\x84", "done")
    test(ALIVE, 1)
    c.jump(b"\x0f\x85", "done")
    test(PLAYABLE, 1)
    c.jump(b"\x0f\x85", "done")
    c.emit(b"\x80\x3d" + u32(BASE + STEAM.pending_continue) + b"\x00")
    c.jump(b"\x0f\x85", "done")
    c.emit(b"\x89\xf8\xc1\xe0\x06\x05" + u32(control + FILES))
    c.emit(b"\x6a\x00\x68" + u32(BASE + COMMANDO_SAVE) + b"\x50")
    c.jump(b"\xe8", BASE + SAVE_GAME)
    c.emit(b"\x83\xc4\x0c\x0f\xb6\xc0")
    if input_target is not None:
        c.jump(b'\xe8',input_target)  # Preserves EAX, including a failed save result.
    c.emit(b"\x89\x04\xbd" + u32(control + SAVE_RESULTS))
    c.emit(b"\xff\x04\xbd" + u32(control + SAVES))
    c.jump(b"\xe9", "done")
    c.label("paused")
    c.label("done")
    store(BUSY, 0)
    c.jump(b"\xe9", "forward")
    c.label("inactive")
    store(ACTIVE, 0)
    store(PLAYABLE, 0)
    store(PLAYER, 0)
    store(ALIVE, 0)
    store(MAP_INDEX, 0xFFFFFFFF)
    store(DEATH_REQUEST, 0)
    c.label("forward")
    if attempt_target:
        c.jump(b'\xe8', attempt_target)
    if disc_target:
        c.jump(b'\xe8', disc_target)
    if clock_target:
        c.jump(b'\xe8', clock_target)
    if bonus_target:
        c.jump(b'\xe8', bonus_target)
    if testing:
        test(ACTIVE, 1)
        c.jump(b"\x0f\x85", "ordinary_forward")
        c.emit(b"\x61\x9d" + bytes.fromhex("83ec1c578bf9897c240c"))
        c.jump(b"\xe9", BASE + 0x35EE)
        c.label("ordinary_forward")
    c.emit(b"\x61\x9d")
    c.jump(b"\xe9", BASE + STEAM.combat_think)
    c.label("hold")
    c.emit(b"\x61\x9d\xc3")
    result = c.finish()
    if len(result) > 4096:
        raise ValueError("Native services exceed code page")
    return result


class NativeServices:
    def __init__(self, memory, identity, testing=False):
        self.memory = memory
        self.control = None
        self.discs = self.weapons = self.launcher = self.bonuses = None
        self.objective_hook = self.traps = self.gate = None
        try:
            self._initialize(memory, identity, testing)
        except BaseException:
            try:
                self.close()
            except OSError as error:
                LOG.error('Native setup cleanup could not be verified: %s', error)
            raise

    def _initialize(self, memory, identity, testing):
        self.memory = memory
        verify_gate(memory, return_to_menu=True)
        scripts = next((m.path for m in memory.modules if m.name.lower() == 'scripts.dll'), None)
        if scripts is None:
            raise UnsupportedBuild("Scripts.dll not loaded")
        require_digest(scripts, STEAM.scripts_sha256)
        for rva, expected in ANCHORS.items():
            if memory.read(BASE + rva, len(expected)) != expected:
                raise UnsupportedBuild(f"Native services anchor differs at {rva:#x}")
        self.token = hashlib.sha256(repr(identity).encode()).hexdigest()[:12]
        self.save_dir = memory.modules[0].path.parent / 'Data' / 'save'
        self.control = memory.allocate()
        from .rewards import NativeRewards
        self.rewards = NativeRewards(memory, testing=testing, service=self.control)
        from .notifications import NativeNotifications
        from ..hotkeys import SupplyHotkeys
        self.notifications = NativeNotifications(memory)
        self.hotkeys = None if testing else SupplyHotkeys(memory.pid, memory.modules[0].path.parent / 'Data' / 'config' / 'input01.cfg')
        memory.write(self.control, b'\0' * 4096)
        memory.write_u32(self.control + MAP_INDEX, 0xFFFFFFFF)
        from .traps import NativeTraps
        self.traps = NativeTraps(memory, self.rewards.control, self.control)
        from .bonuses import NativeBonuses
        self.bonuses = NativeBonuses(memory, self.rewards.control, self.control)
        from .weapons import NativeWeapons
        self.weapons = NativeWeapons(memory, self.control)
        from .discs import NativeDiscs
        self.discs = NativeDiscs(memory, self.control)
        from .attempts import NativeAttempts
        self.attempts = NativeAttempts(memory, self.control)
        memory.write(self.control + WARHEAD, b'BlamoKiller\0')
        cursor = self.control + RECORDS
        for index, mission in enumerate(MISSIONS):
            ids = OBJECTIVES[mission.map_name]
            if len(ids) > 32:
                raise ValueError('Native objective mask capacity exceeded')
            memory.write(self.control + DESCRIPTORS + index * 8, u32(cursor) + u32(len(ids)))
            for bit, oid in enumerate(ids):
                if cursor + RECORD_SIZE > self.control + FILES:
                    raise ValueError('Native objective records exceed data buffer')
                kind = 1 if objective_kind(mission.map_name, oid) == 'primary' else 2
                memory.write(cursor, u32(oid) + u32(1 << bit) + u32(kind))
                cursor += RECORD_SIZE
            filename = 'save\\' + self.filename(mission.map_name, pending=True)
            if len(filename.encode()) >= 64:
                raise ValueError("Checkpoint name exceeds native buffer")
            memory.write(self.control + FILES + index * 64, filename.encode() + b'\0')
        from .mission_evidence import MissionEvidence
        self.mission_evidence = MissionEvidence(memory, self.control)
        from .checkpoint_input import CheckpointInput
        self.checkpoint_input = CheckpointInput(memory)
        program = build_services(self.control, testing=testing, reward_target=self.rewards.control + CODE_OFFSET,
                                 notification_target=self.notifications.control + CODE_OFFSET, clock_target=self.traps.clock,
                                 bonus_target=self.bonuses.update_code, weapon_target=self.weapons.code, disc_target=self.discs.reveal,
                                 attempt_target=self.attempts.code, evidence_target=self.mission_evidence.code,
                                 input_target=self.checkpoint_input.code)
        memory.write(self.control + CODE_OFFSET, program)
        memory.make_executable(self.control + CODE_OFFSET)
        if memory.read(self.control + CODE_OFFSET, len(program)) != program:
            raise MemoryError("Native services read-back failed")
        from .objective_hook import ObjectiveHook
        from .mission_launch import MissionLauncher
        self.launcher = MissionLauncher(memory, self.save_dir, self.token, self.control)
        self.objective_hook = ObjectiveHook(memory, self.control)
        memory.write_u32(self.control+OBSERVER,self.objective_hook.code)
        self.gate = GameplayGate(memory, return_to_menu=True, forward_target=self.control + CODE_OFFSET,
                                 transition_target=self.launcher.transition)
        self.gate.install()
        self.saved = [0] * 12
        from .deathlink import NativeDeathLink
        self.deathlink = NativeDeathLink(memory, self.control)
        self.save_errors = {}

    def filename(self, map_name, pending=False):
        return f'ap_checkpoint_{self.token}_{map_name}' + ('.apstage' if pending else '.sav')

    def update(self, client, gate_enabled=True):
        maps = client.capture_maps
        if not client.capture_ready:
            self.memory.write_u32(self.control + ENABLED, 0)
        state = self.gate.update(maps if gate_enabled else ())
        self.memory.write_u32(self.control + ALLOWED, sum(1 << i for i,m in enumerate(MISSIONS) if m.map_name in maps))
        self.memory.write_u32(self.control + SECONDARIES, 1)  # Checkpoints also work in legacy rooms.
        self.memory.write_u32(self.control + DEATH_ENABLED, int(client.ready and client.deathlink.enabled))
        self.memory.write_u32(self.control + ONLINE, int(client.ready))
        if not client.ready:
            self.memory.write_u32(self.control + DEATH_REQUEST, 0)
        self.memory.write_u32(self.control + HEARTBEAT, self.memory.tick_count())
        self.weapons.update(client)
        self.discs.update(client)
        self.memory.write_u32(self.control + ENABLED, int(client.capture_ready))
        self.objective_hook.update()
        self.traps.update()
        self.bonuses.update()
        self.launcher.update(client)
        return state

    def close(self):
        errors = []
        if self.control is not None:
            for offset in (ENABLED, ONLINE, DEATH_REQUEST):
                try:
                    self.memory.write_u32(self.control + offset, 0)
                except OSError as error:
                    errors.append(str(error))
        for component in (self.discs, self.weapons, self.launcher):
            if component:
                try:
                    component.close()
                except OSError as error:
                    errors.append(str(error))
        # Keep the combat cleanup callback alive if the bodyguard has not reached
        # a deletion frame yet; restoring it early would strand the temporary NPC.
        keep_combat = False
        if self.bonuses:
            try:
                self.bonuses.close()
            except OSError as error:
                keep_combat = True
                errors.append(str(error))
        components = (self.objective_hook,) if keep_combat else (self.objective_hook, self.traps, self.gate)
        for component in components:
            if component:
                try:
                    component.close()
                except OSError as error:
                    errors.append(str(error))
        if errors:
            raise MemoryError('; '.join(errors))


def attach_services(identity, pid=None, testing=False):
    errors = []
    for candidate in [pid] if pid is not None else candidates():
        memory = None
        try:
            memory = ProcessMemory(candidate, writable=True)
            return NativeServices(memory, identity, testing=testing)
        except (OSError, ValueError) as error:
            errors.append(str(error))
            if memory:
                memory.close()
    if errors:
        raise MemoryError('; '.join(errors))
    return None


async def watch_services(client, pid=None, gate_enabled=True, status=None, testing=False, on_attach=None):
    from .secondary_capture import SecondaryCapture
    captures = SecondaryCapture()
    from .discs import DiscCapture
    discs = DiscCapture()
    runtime = None
    identity = None
    last_error = None
    try:
        while not client.stop.is_set():
            try:
                if identity is not None and client.identity != identity:
                    raise ValueError("Cannot reuse captured secondary checks for another room")
                captures.flush(client)
                discs.flush(client)
                if runtime is None and client.capture_ready:
                    from .recovery import recover_activations
                    recover_activations(client)
                    runtime = attach_services(client.identity, pid, testing=testing)
                    if runtime:
                        identity = client.identity
                        LOG.info("Secondary/checkpoint/DeathLink services attached to process %d", runtime.memory.pid)
                        if on_attach:
                            on_attach(runtime)
                if runtime:
                    if client.identity != identity:
                        raise ValueError("Cannot reuse native services for another room")
                    # Drain before checking callback ownership: a level/load or
                    # reattachment must not erase transient completion evidence.
                    discs.collect(runtime)
                    captures.collect(runtime)
                    captures.flush(client)
                    discs.flush(client)
                    state = runtime.update(client, gate_enabled)
                    mem, control = runtime.memory, runtime.control
                    client.attempt = runtime.attempts.snapshot()
                    client.tracker_sample = runtime.attempts.tracker_snapshot()
                    client.tracker_game_state = ('live' if client.tracker_sample else
                        'menu' if mem.u32(BASE+STEAM.game_mode)==0 else 'waiting_frame')
                    active = bool(mem.u32(control + PLAYABLE) and mem.u32(control + ALIVE)
                                  and mem.u32(BASE + STEAM.game_mode) == 1 and not state['blocked_map']
                                  and not mem.read(BASE + STEAM.pending_continue, 1)[0])
                    if runtime.hotkeys:
                        runtime.hotkeys.update(client, active)
                    runtime.rewards.update(client, mem.u32(control + PLAYER), mem.u32(control + MAP_INDEX), active)
                    runtime.notifications.update(client)
                    for index, mission in enumerate(MISSIONS):
                        serial = mem.u32(control + SAVES + index * 4)
                        if serial != runtime.saved[index]:
                            if mem.u32(control + BUSY):
                                continue
                            try:
                                if not mem.u32(control + SAVE_RESULTS + index * 4):
                                    raise ValueError("Engine rejected checkpoint save")
                                staging = runtime.save_dir / runtime.filename(mission.map_name, pending=True)
                                raw = staging.read_bytes()
                                info = inspect_save(raw)
                                if info['map_name'] != mission.map_name:
                                    raise ValueError("Checkpoint saved the wrong map")
                                if mem.u32(control + BUSY) or serial != mem.u32(control + SAVES + index * 4):
                                    continue
                                destination = publish_checkpoint(runtime.save_dir, runtime.token, mission.map_name, raw)
                                runtime.saved[index] = serial
                                mem.write_u32(control + PUBLISHED + index * 4, serial)
                                runtime.save_errors.pop(index, None)
                                LOG.info("Checkpoint updated: %s", destination.name)
                            except (OSError, ValueError) as error:
                                if runtime.save_errors.get(index) != str(error):
                                    LOG.error("Checkpoint %s: %s; keeping previous checkpoint", mission.map_name, error)
                                    runtime.save_errors[index] = str(error)
                    runtime.deathlink.update(client, active=active)
                    if status:
                        status('attached' if client.ready else 'idle',
                               f"Locks {'on' if gate_enabled else 'off'}; checkpoints {'error (see log)' if runtime.save_errors else 'active'}; "
                               f"DeathLink {'on' if client.ready and client.deathlink.enabled else 'off'}" +
                               (f'; weapon unlocks {client.weapon_mask.bit_count()-1}/{len(WEAPONS)}' if client.weapon_mask else '') +
                               ('' if client.ready else '; reconnecting, checks saved locally'))
                last_error = None
                await asyncio.sleep(0.1 if runtime else 1)
            except OSError as error:
                if str(error) != last_error:
                    LOG.warning("%s", error)
                    last_error = str(error)
                if status:
                    status('waiting', str(error))
                if runtime:
                    try:
                        runtime.close()
                    finally:
                        runtime.memory.close()
                        runtime = None
                        client.mission_launch = None
                        client.mission_confirmation = None
                        client.attempt = None
                        client.tracker_sample = None
                        client.tracker_game_state = 'waiting'
                await asyncio.sleep(3)
    finally:
        try:
            if runtime:
                try:
                    runtime.close()
                    runtime.rewards.update(client, 0, -1, False)
                    LOG.info("Native services and combat callback restored")
                finally:
                    try:
                        captures.collect(runtime)
                    finally:
                        try:
                            discs.collect(runtime)
                        finally:
                            runtime.memory.close()
        finally:
            client.mission_launch = None
            client.mission_confirmation = None
            client.attempt = None
            client.tracker_sample = None
            client.tracker_game_state = 'waiting'
            from .final_capture import persist_final_captures
            persist_final_captures(client, identity, captures.pending, discs.pending)

"""TT campaign backend with verified build-specific bindings."""
import asyncio
import logging

from ..data import MISSIONS, OBJECTIVES, DATA_DISCS, mission_for_map, objective_kind
from .bridge import MissionHook
from .gate import GameplayGate
from .memory import ProcessMemory, MemoryError, candidates
from .objective_hook import ObjectiveHook
from .secondary_capture import SecondaryCapture
from .services import (ALLOWED, HEARTBEAT, ENABLED, CAPTURED, LIST_CAPTURED,
                       DESCRIPTORS, RECORDS, RECORD_SIZE, FILES)
from .thunk import Code, u32, CODE_OFFSET, LEASE_MS
from .tt_profile import verify, telemetry
from .tt_hud import NativeHUDGuard
from .tt_load import NativeLoadGuard
from .tt_discs import NativeDiscs, COMBAT_OBJECT, CLOUD_VECTOR
from .discs import DiscCapture, CAPTURED as DISC_CAPTURED
from .tt_inventory import NativeInventory
from . import services as s
from .recovery import recover_activations
from .tt_hotkeys import NativeHotkeys
from .tt_campaign import NativeCampaign
from .tt_deathlink import NativeDeathLink

LOG = logging.getLogger('Renegade.TT')
FRAMES = 104


def build_scan(control, addresses, frame_target=None, checkpoint_target=None, evidence_target=None):
    """Read the objective list on the game thread, including loaded-save state."""
    a = addresses
    c = Code(control + CODE_OFFSET)
    c.emit(b'\x9c\x60')
    c.emit(b'\xff\x05' + u32(control + FRAMES))
    if frame_target is not None:
        c.emit(b'\x89\x0d' + u32(control + COMBAT_OBJECT))
        c.jump(b'\xe8', frame_target)
    c.emit(b'\x83\x3d' + u32(control + ENABLED) + b'\x01')
    c.jump(b'\x0f\x85', 'exit')
    c.emit(b'\xff\x15' + u32(a.tick_iat))
    c.emit(b'\x2b\x05' + u32(control + HEARTBEAT) + b'\x3d' + u32(LEASE_MS))
    c.jump(b'\x0f\x87', 'exit')
    c.emit(b'\x83\x3d' + u32(a.game_mode) + b'\x01')
    c.jump(b'\x0f\x85', 'exit')
    c.emit(b'\x83\x3d' + u32(a.game_data) + b'\x00')
    c.jump(b'\x0f\x84', 'exit')
    c.emit(b'\x8b\x15' + u32(a.loaded_map) + b'\x85\xd2')
    c.jump(b'\x0f\x84', 'exit')
    for index, mission in enumerate(MISSIONS):
        for suffix in ('.lsd', '.mix'):
            label = f'next{index}{suffix}'
            for offset, char in enumerate((mission.map_name.lower() + suffix).encode() + b'\0'):
                c.emit(b'\x8a\x42' + bytes([offset]))
                if 97 <= char <= 122:
                    c.emit(b'\x0c\x20')
                c.emit(b'\x3c' + bytes([char]))
                c.jump(b'\x0f\x85', label)
            c.emit(b'\xbf' + u32(index))
            c.jump(b'\xe9', 'map')
            c.label(label)
    c.jump(b'\xe9', 'exit')
    c.label('map')
    c.emit(b'\x0f\xa3\x3d' + u32(control + ALLOWED))
    c.jump(b'\x0f\x83', 'exit')
    if evidence_target:
        c.jump(b'\xe8', evidence_target)
    c.emit(b'\x8b\x0d' + u32(a.objective_count) + b'\x81\xf9' + u32(128))
    c.jump(b'\x0f\x87', 'exit')
    c.emit(b'\x8b\x15' + u32(a.objective_array) + b'\x31\xed\x85\xc9')
    c.jump(b'\x0f\x84', 'scanned')
    c.emit(b'\x85\xd2')
    c.jump(b'\x0f\x84', 'exit')
    c.label('objective')
    c.emit(b'\x8b\x02\x85\xc0')
    c.jump(b'\x0f\x84', 'next_objective')
    c.emit(b'\x83\x78\x08\x01')
    c.jump(b'\x0f\x85', 'next_objective')
    c.emit(b'\x8b\x18\x51\x52\xff\x70\x04')
    c.emit(b'\x8b\x34\xfd' + u32(control + DESCRIPTORS))
    c.emit(b'\x8b\x04\xfd' + u32(control + DESCRIPTORS + 4))
    c.label('record')
    c.emit(b'\x85\xc0'); c.jump(b'\x0f\x84', 'record_done')
    c.emit(b'\x3b\x1e'); c.jump(b'\x0f\x84', 'record_match')
    c.emit(b'\x83\xc6\x0c\x48'); c.jump(b'\xe9', 'record')
    c.label('record_match')
    c.emit(b'\x8b\x0c\x24\x3b\x4e\x08')
    c.jump(b'\x0f\x85', 'record_done')
    c.emit(b'\x0b\x6e\x04')
    c.label('record_done')
    c.emit(b'\x83\xc4\x04\x5a\x59')
    c.label('next_objective')
    c.emit(b'\x83\xc2\x04\x49'); c.jump(b'\x0f\x85', 'objective')
    c.label('scanned')
    c.emit(b'\xf0\x09\x2c\xbd' + u32(control + LIST_CAPTURED))
    c.emit(b'\xf0\x09\x2c\xbd' + u32(control + CAPTURED))
    if checkpoint_target is not None:
        c.jump(b'\xe8', checkpoint_target)
    c.label('exit')
    c.emit(b'\x61\x9d'); c.jump(b'\xe9', a.combat_think)
    result = c.finish()
    if len(result) > 4096:
        raise ValueError('TT objective scanner exceeds code page')
    return result


def write_descriptors(memory, control):
    cursor = control + RECORDS
    for index, mission in enumerate(MISSIONS):
        ids = OBJECTIVES[mission.map_name]
        if len(ids) > 32:
            raise ValueError('TT objective mask capacity exceeded')
        memory.write(control + DESCRIPTORS + index * 8, u32(cursor) + u32(len(ids)))
        for bit, oid in enumerate(ids):
            if cursor + RECORD_SIZE > control + FILES:
                raise ValueError('TT objective descriptors exceed data page')
            kind = 1 if objective_kind(mission.map_name, oid) == 'primary' else 2
            memory.write(cursor, u32(oid) + u32(1 << bit) + u32(kind))
            cursor += RECORD_SIZE


class TTSession:
    def __init__(self, memory, *, testing=False, identity=None):
        self.memory = memory
        self.addresses = verify(memory, gate=True)
        self.mission = self.objective = self.gate = self.discs = self.inventory = None
        self.campaign = None
        self.control = None
        try:
            self.control = memory.allocate()
            memory.write(self.control, b'\0' * 4096)
            write_descriptors(memory, self.control)
            self.discs = NativeDiscs(memory, self.control, self.addresses, checkpoint=identity is not None)
            self.inventory = NativeInventory(memory, self.control, self.addresses,
                                             self.discs.reveal, testing)
            self.deathlink = NativeDeathLink(memory,self.control)
            if identity is not None:
                self.campaign = NativeCampaign(memory,self.control,self.addresses,identity)
            from .mission_evidence import MissionEvidence
            self.mission_evidence = MissionEvidence(memory,self.control,tt=True)
            program = build_scan(self.control, self.addresses, self.inventory.code,
                                 self.campaign.code if self.campaign else None,
                                 evidence_target=self.mission_evidence.code)
            memory.write(self.control + CODE_OFFSET, program)
            if memory.read(self.control + CODE_OFFSET, len(program)) != program:
                raise MemoryError('TT scanner read-back failed')
            memory.make_executable(self.control + CODE_OFFSET)
            # Construct and validate every component before changing callbacks.
            self.mission = MissionHook(memory, tt=True)
            self.objective = ObjectiveHook(memory, self.control, tt=True, replay_events=identity is not None)
            memory.write_u32(self.control+s.OBSERVER,self.objective.code)
            self.gate = GameplayGate(memory, return_to_menu=True,
                                     forward_target=self.control + CODE_OFFSET, tt=True,
                                     transition_target=self.campaign.launcher.transition if self.campaign else None)
            self.hud_guard = NativeHUDGuard(memory, self.addresses)
            self.load_guard = NativeLoadGuard(memory, self.addresses)
            installed = self.load_guard.install()
            LOG.info('TT campaign save-load guard %s; remains active until the game exits',
                     'installed' if installed else 'already active')
            installed = self.hud_guard.install()
            LOG.info('TT HUD load-crash guard %s; remains active until the game exits',
                     'installed' if installed else 'already active')
            self.inventory.traps.add_render(self.hud_guard)
            self.mission.install()
            self.objective.update()
            self.gate.install()
        except BaseException:
            self.close()
            raise

    def update(self, maps, discs_enabled=False, client=None):
        self.memory.write_u32(self.control + HEARTBEAT, self.memory.tick_count())
        self.memory.write_u32(self.control + ALLOWED,
                              sum(1 << i for i, m in enumerate(MISSIONS) if m.map_name in maps))
        self.memory.write_u32(self.control + ENABLED, int(bool(maps)))
        # A map load can rebuild ScriptCommands. Drain the latch first and only
        # reclaim an original pointer, never a callback installed by someone else.
        victories = self.mission.captured()
        owner = self.memory.u32(self.mission.callback_field)
        if owner == self.mission.original:
            self.memory.write_u32(self.mission.callback_field, self.mission.code)
        if owner != 0:
            victories += self.mission.update(maps)
        if self.memory.u32(self.objective.field) == self.objective.original:
            self.inventory.invalidate()
        self.objective.update()
        from ..data import BLACK_HAND_INTEL, disc_id
        self.discs.update(bool(maps) and discs_enabled,
                          intel_enabled=client is not None and disc_id('M13', BLACK_HAND_INTEL) in client.locations)
        if client is not None:
            self.inventory.update(client)
            self.deathlink.update(client)
            self.inventory.notifications.update(client)
            client.attempt = self.inventory.attempts.snapshot()
            client.tracker_sample = self.inventory.attempts.tracker_snapshot()
            client.tracker_game_state = ('live' if client.tracker_sample else
                'menu' if self.memory.u32(self.addresses.game_mode)==0 else 'waiting_frame')
            if self.campaign:
                self.campaign.update(client)
        return tuple(set(victories)), self.gate.update(maps)

    def snapshot(self):
        state = telemetry(self.memory, self.addresses)
        return {**state, **self.gate.snapshot(),
                'hud_guard': self.hud_guard.snapshot(),
                'load_guard': self.load_guard.snapshot(),
                'scan_frames': self.memory.u32(self.control + FRAMES),
                'map_revealed': (self.memory.read(CLOUD_VECTOR, 52) == b'\0'*52
                                 if state['mode'] == 1 and state['map_name'] else None),
                'discs': {m.map_name: [oid for bit, oid in enumerate(DATA_DISCS[m.map_name])
                    if self.memory.u32(self.discs.control + DISC_CAPTURED + index * 4) & (1 << bit)]
                    for index, m in enumerate(MISSIONS)},
                'victories': list(self.mission.captured()),
                'objectives': {m.map_name: [oid for bit, oid in enumerate(OBJECTIVES[m.map_name])
                    if self.memory.u32(self.control + CAPTURED + index * 4) & (1 << bit)]
                    for index, m in enumerate(MISSIONS)}}

    def close(self):
        errors = []
        if self.control is not None:
            try:
                self.memory.write_u32(self.control + ENABLED, 0)
            except OSError as error:
                errors.append(str(error))
        for component in (self.campaign, self.inventory, self.gate, self.discs, self.objective, self.mission):
            if component:
                try:
                    component.close()
                except OSError as error:
                    errors.append(str(error))
        if self.inventory and self.inventory.hologram.pending_cleanup:
            try:self.inventory.hologram.defer_cleanup()
            except OSError as error:errors.append(str(error))
        if errors:
            raise MemoryError('; '.join(errors))
        # Compatibility guards have no AP state. Keep them active until game
        # exit so disconnecting cannot reintroduce the two load failures.


def attach(pid=None, *, testing=False, identity=None):
    errors = []
    valid = []
    for candidate in [pid] if pid is not None else candidates():
        memory = None
        try:
            memory = ProcessMemory(candidate)
            if memory.modules[0].name.lower() != 'game2.exe' and pid is None:
                continue
            verify(memory, gate=True)
            valid.append(candidate)
        except (OSError, ValueError) as error:
            errors.append(str(error))
        finally:
            if memory:
                memory.close()
    if len(valid) > 1:
        raise MemoryError('Multiple TT games are running; select a process with --pid')
    if valid:
        memory = ProcessMemory(valid[0], writable=True)
        try:
            return TTSession(memory, testing=testing, identity=identity)
        except BaseException:
            memory.close()
            raise
    if errors:
        raise MemoryError('; '.join(errors))
    return None


def persist_final_captures(client, identity, objectives, discs, victories):
    """Persist already latched evidence after hooks stop, without rearming capture.

    Normal client entry points deliberately reject new events after stop is set.
    This final drain uses only this session's authenticated permissions and exact
    location catalogue. It cannot grant permissions or transfer another room's data.
    """
    from .final_capture import persist_final_captures as persist
    persist(client, identity, objectives.pending, discs.pending, victories)


async def watch(client, pid=None, status=None, on_attach=None):
    def report(state, detail):
        if state=='waiting':client.tracker_game_state='waiting'
        if status:
            status(state, detail)
    runtime = None
    identity = None
    captures = SecondaryCapture()
    disc_captures = DiscCapture()
    pending = set()
    last_error = None
    try:
        while not client.stop.is_set():
            if identity is not None and client.identity != identity:
                raise ValueError('Refusing to transfer TT captures to another AP room')
            captures.flush(client)
            disc_captures.flush(client)
            if client.capture_ready:
                for map_name in tuple(pending):
                    try:
                        if (client.mission_finished(map_name, True)
                                or mission_for_map(map_name).check_id() in client.progress.completed):
                            pending.remove(map_name)
                    except OSError as error:
                        LOG.error('Retaining TT victory for retry: %s', error)
            try:
                if runtime is None and client.capture_ready:
                    runtime = attach(pid,identity=client.identity)
                    if runtime:
                        identity = client.identity
                        recover_activations(client)
                        hotkeys = NativeHotkeys(runtime.memory)
                        LOG.info('TT adapter attached: checks, maps, inventory, checkpoints, '
                                    'mission buttons, notifications, effect HUD, Armour Loss, Concrete Boots, '
                                    'Disco Havoc, Moon Boots, Butterfingers, Bullet Hose, Vampiric Rounds, '
                                    'Hologram Havoc and DeathLink.')
                        if on_attach:
                            on_attach(runtime)
                if runtime:
                    pending.update(runtime.mission.captured())
                    captures.collect(runtime)
                    disc_captures.collect(runtime)
                    victories, state = runtime.update(client.capture_maps,
                        client.capture_ready and client.config.get('data_discs', False), client)
                    hotkeys.update(client, runtime.memory.u32(runtime.control+s.PLAYABLE) == 1
                                   and runtime.memory.u32(runtime.control+s.ALIVE) == 1)
                    pending.update(victories)
                    report('attached', f'TT b9000 - checks, locks, inventory and checkpoints; process {runtime.memory.pid}'
                           + ('; checkpoint error (see log)' if runtime.campaign and runtime.campaign.error else '')
                           + (f"; {state['blocked_map']} locked" if state['blocked_map'] else ''))
                else:
                    report('waiting', 'Waiting for a connected room and TT b9000')
                last_error = None
                await asyncio.sleep(.1 if runtime else 1)
            except OSError as error:
                report('waiting', str(error))
                if str(error) != last_error:
                    LOG.warning('%s', error)
                    last_error = str(error)
                if runtime:
                    try:
                        runtime.close()
                    except OSError as cleanup_error:
                        LOG.warning('TT process exited or cleanup could not be verified: %s. '
                                    'A fresh attachment still requires original callbacks.', cleanup_error)
                    finally:
                        runtime.memory.close()
                        runtime = None
                        client.tracker_sample = None
                        client.mission_launch = client.mission_confirmation = None
                await asyncio.sleep(3)
    finally:
        client.attempt = None
        client.tracker_sample = None
        client.tracker_game_state = 'waiting'
        client.mission_launch = client.mission_confirmation = None
        if runtime:
            try:
                # Disable/restore first, then drain the final latches. A pickup
                # during callback restoration must survive cancellation too.
                runtime.close()
                runtime.inventory.rewards.update(client,0,-1,False)
            finally:
                try:
                    try:
                        captures.collect(runtime)
                    finally:
                        try:
                            disc_captures.collect(runtime)
                        finally:
                            pending.update(runtime.mission.captured())
                finally:
                    try:
                        persist_final_captures(client, identity, captures, disc_captures, pending)
                    finally:
                        runtime.memory.close()
        else:
            persist_final_captures(client, identity, captures, disc_captures, pending)
        deferred=bool(runtime and runtime.inventory.hologram.pending_cleanup)
        report('stopped', 'Return to TT to finish bodyguard cleanup' if deferred else
               'TT AP callbacks restored; compatibility guards remain until game exit')

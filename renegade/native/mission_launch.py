"""Validated AP save selection, combat exit and menu-thread loading."""
import logging
import time
import uuid

from ..data import MISSIONS, mission_for_map
from .savebuilder import BASE, STAR, MENU_SLOT, MENU_THINK, INIT_SP, START_GAME, DIFFICULTIES, ANCHORS as SAVE_ANCHORS
from .savefiles import inspect_save, saved_difficulty
from .thunk import Code, CODE_OFFSET, LEASE_MS, u32, steam_addresses
from .profile import STEAM, UnsupportedBuild
from .memory import MemoryError
from .checkpoints import checkpoint_history

LOG = logging.getLogger('Renegade.Launch')
REQUEST, RESULT, BUSY, HEARTBEAT, ALLOWED, MAP = range(0, 24, 4)
FILENAME = 128
PHASE = 24
QUEUED, RETURNING, LOADING, WAIT_CUTSCENE, WAIT_DIALOG, WAIT_SAVE, WAIT_TRANSITION = range(7)
LOAD_STATUS = {
    QUEUED: 'Load queued. Return to Renegade to let it run.',
    RETURNING: 'Returning to the menu to load the selected save…',
    LOADING: 'Loading the selected save…',
    WAIT_CUTSCENE: 'Load queued; waiting for the cutscene to finish.',
    WAIT_DIALOG: 'Load queued; close the in-game message to continue.',
    WAIT_SAVE: 'Load queued; finishing the current checkpoint.',
    WAIT_TRANSITION: 'Load queued; waiting for the current game transition.',
}
MODE_LIST, COMBAT_VTABLE = 0x44511C, STEAM.combat_think_slot - 13 * 4
ANCHORS = {
    **{rva: SAVE_ANCHORS[rva] for rva in (MENU_THINK, INIT_SP)},
    START_GAME: bytes.fromhex('a1c86d850081ec280c000083f803535657bb010000000f84'),
    # GameModeManager::Find traverses nodes: next +0, GameMode pointer +4.
    0x385F0: bytes.fromhex('53568b351c5184005785f674228b5c24108b7e048bcf8b07'),
    STEAM.end_game: STEAM.menu_anchors[STEAM.end_game],
    STEAM.end_game_menu: STEAM.menu_anchors[STEAM.end_game_menu],
}


def combat_instance(memory):
    node, seen, found = memory.u32(BASE + MODE_LIST), set(), []
    for _ in range(32):
        if not node:
            break
        if node in seen:
            raise UnsupportedBuild('Steam game-mode list is cyclic')
        seen.add(node)
        obj = memory.u32(node + 4)
        if obj and memory.u32(obj) == BASE + COMBAT_VTABLE:
            found.append(obj)
        node = memory.u32(node)
    if node or len(found) != 1:
        raise UnsupportedBuild('Steam mission loader cannot identify the combat mode')
    return found[0]


class CheckpointDifficultyMismatch(ValueError):
    def __init__(self, difficulty, fingerprint):
        self.difficulty, self.fingerprint = difficulty, fingerprint
        super().__init__(f'This checkpoint is {DIFFICULTIES[difficulty]}; changing difficulty requires a mission restart')


def build_loader(control, *, addresses=None, combat_object=None):
    if addresses is None:
        addresses = steam_addresses(BASE)
        addresses.init_sp = BASE+INIT_SP
        addresses.start_game = BASE+START_GAME
        addresses.menu_think = BASE+MENU_THINK
    c = Code(control+CODE_OFFSET)
    def test(off, value): c.emit(b'\x83\x3d'+u32(control+off)+bytes([value]))
    def store(off, value): c.emit(b'\xc7\x05'+u32(control+off)+u32(value))
    c.emit(b'\x9c\x60')
    test(BUSY, 0); c.jump(b'\x0f\x85', 'hold')
    test(REQUEST, 1); c.jump(b'\x0f\x85', 'forward')
    c.emit(b'\xff\x15'+u32(addresses.tick_iat)+b'\x2b\x05'+u32(control+HEARTBEAT)+b'\x3d'+u32(LEASE_MS))
    c.jump(b'\x0f\x87', 'reject')
    # Menu callbacks also run over paused combat. A queued loader must wait for
    # Combat to become INACTIVE, not mistake that pause menu for the main menu.
    c.emit(b'\x8b\x4c\x24\x18\x83\x79\x04\x00')
    c.jump(b'\x0f\x85', 'forward' if combat_object else 'reject')
    if combat_object:
        c.emit(b'\x83\x3d'+u32(addresses.game_mode)+b'\x01'); c.jump(b'\x0f\x87', 'reject')
        c.emit(b'\x83\x3d'+u32(combat_object+4)+b'\x01'); c.jump(b'\x0f\x85', 'forward')
        c.emit(b'\x83\x3d'+u32(addresses.message_count)+b'\x00'); c.jump(b'\x0f\x84', 'menu_ready')
        store(PHASE, WAIT_DIALOG); c.jump(b'\xe9', 'forward')
        c.label('menu_ready')
    else:
        c.emit(b'\x83\x3d'+u32(addresses.game_mode)+b'\x00'); c.jump(b'\x0f\x85', 'reject')
    c.emit(b'\x8b\x15'+u32(control+MAP)+b'\x83\xfa\x0c'); c.jump(b'\x0f\x83', 'reject')
    c.emit(b'\x0f\xa3\x15'+u32(control+ALLOWED)); c.jump(b'\x0f\x83', 'reject')
    store(BUSY, 1); store(REQUEST, 0); store(PHASE, LOADING)
    c.emit(b'\xfc'); c.jump(b'\xe8', addresses.init_sp)
    c.emit(b'\x6a\x00\x6a\xff\x68'+u32(control+FILENAME))
    c.jump(b'\xe8', addresses.start_game); c.emit(b'\x83\xc4\x0c')
    store(RESULT, 1); store(BUSY, 0)
    c.jump(b'\xe9', 'hold')  # Do not run the old menu after its mode was destroyed.
    c.label('reject'); store(REQUEST, 0); store(RESULT, 2)
    c.label('forward'); c.emit(b'\x61\x9d'); c.jump(b'\xe9', addresses.menu_think)
    c.label('hold'); c.emit(b'\x61\x9d\xc3')
    return c.finish()


def build_transition(address, control, a, combat, *, star, camera,
                     checkpoint_busy=(), checkpoint_serials=()):
    """Combat-thread quit for a queued load; EAX=1 means consume this frame.

    Called before the gameplay gate's ACTIVE-only guard so paused (SUSPENDED)
    combat works too. End_Game reenters mode Think: both wrappers share BUSY.
    Menu Think performs the eventual load only once combat is fully INACTIVE.
    """
    from .services import CONTROL_ENABLED, CAMERA_HOST
    c = Code(address)
    def test(address, value=0): c.emit(b'\x83\x3d'+u32(address)+bytes([value]))
    def store(off, value): c.emit(b'\xc7\x05'+u32(control+off)+u32(value))
    c.emit(b'\x9c\x60')
    test(control+BUSY); c.jump(b'\x0f\x85','hold')
    test(control+REQUEST,1); c.jump(b'\x0f\x85','forward')
    c.emit(b'\xff\x15'+u32(a.tick_iat)+b'\x2b\x05'+u32(control+HEARTBEAT)+b'\x3d'+u32(LEASE_MS))
    c.jump(b'\x0f\x87','reject')
    c.emit(b'\x8b\x15'+u32(control+MAP)+b'\x83\xfa\x0c'); c.jump(b'\x0f\x83','reject')
    c.emit(b'\x0f\xa3\x15'+u32(control+ALLOWED)); c.jump(b'\x0f\x83','reject')
    test(a.game_mode,1); c.jump(b'\x0f\x85','forward')
    c.emit(b'\x8b\x4c\x24\x18\x81\xf9'+u32(combat)); c.jump(b'\x0f\x85','forward')
    c.emit(b'\x83\x79\x04\x00'); c.jump(b'\x0f\x84','state_ok')
    c.emit(b'\x83\x79\x04\x03'); c.jump(b'\x0f\x85','wait_transition')
    c.label('state_ok')
    test(a.game_data); c.jump(b'\x0f\x84','wait_transition')
    c.emit(b'\x80\x3d'+u32(a.pending_continue)+b'\x00'); c.jump(b'\x0f\x85','wait_transition')
    test(a.message_count); c.jump(b'\x0f\x85','wait_dialog')
    for field in checkpoint_busy:
        test(field); c.jump(b'\x0f\x85', 'wait_save')
    for saved, published in checkpoint_serials:
        c.emit(b'\xa1'+u32(saved)+b'\x3b\x05'+u32(published))
        c.jump(b'\x0f\x85', 'wait_save')
    # Read current references, never the inventory service's cached player.
    c.emit(b'\xa1'+u32(star)+b'\x85\xc0'); c.jump(b'\x0f\x84','wait_transition')
    c.emit(b'\x8b\x40\x04\x85\xc0'); c.jump(b'\x0f\x84','wait_transition')
    c.emit(b'\x80\xb8'+u32(CONTROL_ENABLED)+b'\x00'); c.jump(b'\x0f\x84','wait_cutscene')
    c.emit(b'\xa1'+u32(camera)+b'\x85\xc0'); c.jump(b'\x0f\x84','wait_transition')
    c.emit(b'\x83\xb8'+u32(CAMERA_HOST)+b'\x00'); c.jump(b'\x0f\x85','wait_cutscene')
    store(BUSY,1); store(PHASE,RETURNING)
    c.emit(b'\xfc\x8b\x4c\x24\x18\x8b\x01\xff\x50\x10')  # Suspend(this)
    c.emit(b'\xc6\x05'+u32(a.pending_continue)+b'\x00')
    c.jump(b'\xe8',a.end_game); c.jump(b'\xe8',a.end_game_menu)
    store(BUSY,0)
    c.jump(b'\xe9','hold')  # The old combat mode has been torn down.
    for label, phase in (('wait_transition',WAIT_TRANSITION),
                         ('wait_dialog',WAIT_DIALOG),('wait_save',WAIT_SAVE),
                         ('wait_cutscene',WAIT_CUTSCENE)):
        c.label(label); store(PHASE,phase); c.jump(b'\xe9','forward')
    c.label('reject'); store(REQUEST,0); store(RESULT,2)
    c.label('forward'); c.emit(b'\x61\x9d\xb8'+u32(0)+b'\xc3')
    c.label('hold'); c.emit(b'\x61\x9d\xb8'+u32(1)+b'\xc3')
    return c.finish()



def select_save(save_dir, token, map_name, difficulty, continuing=False, checkpoint=None):
    mission = mission_for_map(map_name)
    if type(difficulty) is not int or difficulty not in DIFFICULTIES:
        raise ValueError('Choose Recruit, Soldier or Commando')
    if continuing:
        paths = [save_dir / f'ap_checkpoint_{token}_{map_name}.sav']
        if checkpoint is not None:
            choices = checkpoint_history(save_dir, token, map_name)
            choice = next((c for c in choices if checkpoint == dict(name=c['name'], fingerprint=c['fingerprint'])), None)
            if choice is None:
                raise ValueError('Selected checkpoint changed or expired; select a checkpoint again')
            paths = [save_dir / choice['name']]
    else:
        paths = sorted(save_dir.glob(f'ap_start_*_{DIFFICULTIES[difficulty]}_{mission.number:02d}_{map_name}.sav'),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True)
        if not paths:
            from ..start_saves import install_start_save
            bundled = install_start_save(save_dir, map_name, difficulty)
            if bundled is not None:
                paths = [bundled]
    for path in paths:
        if not path.is_file():
            continue
        if path.resolve().parent != save_dir.resolve():
            raise ValueError('Save must be inside Renegade Data/save')
        raw = path.read_bytes()
        info = inspect_save(raw)
        if checkpoint is not None and info['sha256'] != checkpoint['fingerprint']:
            raise ValueError('Selected checkpoint changed; select a checkpoint again')
        if info['map_name'] != map_name:
            raise ValueError('Save contains a different mission')
        actual = saved_difficulty(raw)
        if actual != difficulty:
            if continuing:
                raise CheckpointDifficultyMismatch(actual, info['sha256'])
            raise ValueError(f'Starting save contains {DIFFICULTIES[actual]} difficulty, not {DIFFICULTIES[difficulty]}')
        return path
    raise ValueError('No checkpoint for this room and mission yet' if continuing else
                     f'No {DIFFICULTIES[difficulty]} starting save installed for {mission.title}')


class MissionLauncher:
    menu_slot, menu_think, game_mode = BASE+MENU_SLOT, BASE+MENU_THINK, BASE+STEAM.game_mode
    can_queue = True

    def at_menu(self):
        return self.memory.u32(self.combat + 4) == 1

    def prepare_load(self, path, map_name, difficulty, fingerprint=None):
        # Pin the selected file across delayed loads and checkpoint rotation.
        raw = path.read_bytes()
        info = inspect_save(raw)
        if (info['map_name'] != map_name or saved_difficulty(raw) != difficulty
                or (fingerprint and info['sha256'] != fingerprint)):
            raise ValueError('Mission save changed; select it again')
        destination = self.save_dir / f'ap_load_{self.token}.sav'
        if destination.resolve().parent != self.save_dir.resolve():
            raise ValueError('Queued save must remain inside the save directory')
        temp = destination.with_suffix('.tmp')
        temp.write_bytes(raw)
        temp.replace(destination)
        return destination

    def request_focus(self):
        from ..game_window import focus_game
        if not focus_game(getattr(self.memory, 'pid', None)):
            LOG.info('Load queued; return focus to Renegade so its game thread can process it')

    def __init__(self, memory, save_dir, token, service=None):
        self.memory, self.save_dir, self.token = memory, save_dir, token
        self.combat = combat_instance(memory)
        self.control = memory.allocate()
        memory.write(self.control, b'\0'*4096)
        for rva, expected in ANCHORS.items():
            if memory.read(BASE+rva,len(expected)) != expected:
                raise UnsupportedBuild('Mission loader anchor differs')
        # Stock save builder already exercises this exact normal save-loading call.
        if memory.u32(BASE+MENU_SLOT) != BASE+MENU_THINK:
            raise UnsupportedBuild('Another client owns the menu callback')
        addresses = steam_addresses(BASE)
        code = build_loader(self.control, combat_object=self.combat)
        if len(code) > 1024:
            raise ValueError('Steam menu loader overlaps transition code')
        from . import services as s
        self.transition = self.control + CODE_OFFSET + 1024
        code = code.ljust(1024, b'\xcc') + build_transition(
            self.transition, self.control, addresses, self.combat,
            star=BASE + STAR, camera=BASE + s.COMBAT_CAMERA,
            checkpoint_busy=(service + s.BUSY,) if service is not None else (),
            checkpoint_serials=tuple((service + s.SAVES + i*4, service + s.PUBLISHED + i*4)
                                     for i in range(12)) if service is not None else ())
        if len(code) > 4096:
            raise ValueError('Steam mission loader exceeds code page')
        memory.write(self.control+CODE_OFFSET, code)
        if memory.read(self.control+CODE_OFFSET,len(code)) != code:
            raise MemoryError('Mission loader read-back failed')
        memory.make_executable(self.control+CODE_OFFSET)
        self.pending = None
        self.confirmation = None
        self.submitted = 0

    def update(self, client):
        mem, ctrl = self.memory, self.control
        if mem.u32(self.menu_slot) == self.menu_think:
            mem.replace_readonly_u32(self.menu_slot,self.menu_think,ctrl+CODE_OFFSET)
        if mem.u32(self.menu_slot) != ctrl+CODE_OFFSET:
            raise MemoryError('Mission loader lost callback ownership')
        maps = client.unlocked_maps if client.ready else ()
        mem.write_u32(ctrl+ALLOWED, sum(1 << i for i,m in enumerate(MISSIONS) if m.map_name in maps))
        mem.write_u32(ctrl+HEARTBEAT, mem.tick_count())
        menu = self.at_menu()
        if self.confirmation and (not client.ready or (not menu and not self.can_queue) or not client.mission_confirmation):
            self.confirmation = None
            client.mission_confirmation = None
            LOG.info('Mission restart cancelled')
        if self.pending:
            result = mem.u32(ctrl+RESULT)
            if result:
                LOG.log(logging.INFO if result == 1 else logging.WARNING,
                        '%s', 'Mission save load requested' if result == 1 else 'Mission load cancelled; check the connection and mission unlock')
                self.pending = None
            elif not client.ready or self.pending[0] not in maps:
                mem.write_u32(ctrl+REQUEST,0)
                self.pending = None
                LOG.warning('Mission load cancelled: room disconnected or mission no longer unlocked')
            elif time.monotonic()-self.submitted > (120 if self.can_queue else 15) and not mem.u32(ctrl+BUSY):
                mem.write_u32(ctrl+REQUEST,0)
                self.pending = None
                LOG.warning('Mission loader timed out; return focus to Renegade and try again')
        history = {m.map_name: checkpoint_history(self.save_dir, self.token, m.map_name) for m in MISSIONS}
        client.mission_launch = dict(menu=menu, can_queue=self.can_queue,
                                     status=LOAD_STATUS.get(mem.u32(ctrl+PHASE), LOAD_STATUS[QUEUED]) if self.pending else '',
                                     busy=bool(self.pending or self.confirmation or mem.u32(ctrl+BUSY)), unlocked=list(maps),
                                     checkpoints=[name for name, choices in history.items() if choices], history=history)
        if not client.mission_request:
            return
        request = client.mission_request
        client.mission_request = None
        try:
            if len(request) not in (3, 4, 5):
                raise ValueError('Invalid mission request')
            map_name, difficulty, continuing = request[:3]
            confirmation_id = request[3] if len(request)>=4 else None
            checkpoint = request[4] if len(request)==5 else None
            if confirmation_id:
                if not self.confirmation or self.confirmation['id'] != confirmation_id:
                    raise ValueError('Restart confirmation expired; click Continue Mission again')
            else:
                self.confirmation = client.mission_confirmation = None
            if self.pending or mem.u32(ctrl+BUSY) or (not menu and not self.can_queue) or not client.ready:
                raise ValueError('Return to the main menu with a connected room before loading a mission')
            if map_name not in maps:
                raise ValueError('This mission is locked')
            restarting = False
            try:
                path = select_save(self.save_dir, self.token, map_name, difficulty, continuing, checkpoint)
                if confirmation_id:
                    raise ValueError('Checkpoint changed; click Continue Mission again')
            except CheckpointDifficultyMismatch as mismatch:
                # Validate a real starting save BEFORE offering to discard this
                # mission's progress. Never edit the checkpoint's difficulty.
                path = select_save(self.save_dir, self.token, map_name, difficulty)
                signature = (map_name, difficulty, mismatch.difficulty, mismatch.fingerprint)
                if not confirmation_id or self.confirmation['signature'] != signature:
                    offer = dict(id=uuid.uuid4().hex, map=map_name, difficulty=difficulty,
                                 saved_difficulty=mismatch.difficulty, title=mission_for_map(map_name).title)
                    if checkpoint is not None:
                        offer['checkpoint'] = checkpoint
                    self.confirmation = dict(offer, signature=signature)
                    client.mission_confirmation = offer
                    client.mission_launch['busy'] = True
                    LOG.warning('Changing %s from %s to %s requires a restart; awaiting confirmation',
                                offer['title'], DIFFICULTIES[mismatch.difficulty], DIFFICULTIES[difficulty])
                    return
                restarting = True
            self.confirmation = client.mission_confirmation = None
            path = self.prepare_load(path, map_name, difficulty,
                                     checkpoint['fingerprint'] if checkpoint and continuing and not restarting else None)
            filename = ('save\\'+path.name+'\0').encode('ascii')
            if len(filename)>128 or any(c in path.name for c in '/\\:'):
                raise ValueError('Invalid mission save filename')
            mem.write(ctrl+FILENAME, filename)
            mem.write_u32(ctrl+MAP, mission_for_map(map_name).number-1)
            mem.write_u32(ctrl+RESULT,0)
            mem.write_u32(ctrl+PHASE,QUEUED)
            self.pending = (map_name,difficulty,continuing)
            client.mission_launch['busy'] = True
            client.mission_launch['status'] = LOAD_STATUS[QUEUED]
            self.submitted = time.monotonic()
            mem.write_u32(ctrl+REQUEST,1)
            LOG.info('%s %s (%s)', 'Restarting' if restarting else 'Continuing' if continuing else 'Starting',
                     mission_for_map(map_name).title, DIFFICULTIES[difficulty])
            self.request_focus()
        except (OSError, ValueError) as error:
            self.confirmation = client.mission_confirmation = None
            client.mission_launch['busy'] = bool(self.pending)
            LOG.error('Cannot load mission: %s', error)

    def close(self):
        self.confirmation = None
        self.memory.write_u32(self.control+ALLOWED,0)
        self.memory.write_u32(self.control+REQUEST,0)
        if self.memory.u32(self.menu_slot) == self.control+CODE_OFFSET:
            self.memory.replace_readonly_u32(self.menu_slot,self.control+CODE_OFFSET,self.menu_think)

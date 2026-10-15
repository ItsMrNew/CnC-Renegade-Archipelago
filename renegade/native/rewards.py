"""Stock Steam item effects, called only from the guarded services game-thread callback."""
import struct
import uuid
from ..data import BUFFS, ITEM_FLAGS, UPGRADE_AMOUNT, UPGRADE_LIMIT
from ..data import ITEM_IDS, SUPPLIES, UPGRADES, TRAPS, BUTTERFINGERS, BUTTERFINGERS_RELOADS, CONCRETE_BOOTS, DISCO_HAVOC, MOON_BOOTS
from .thunk import Code, u32, CODE_OFFSET
from .savebuilder import BASE, GAME_DIFFICULTY
from .profile import UnsupportedBuild

REQUEST, RESULT, PLAYER, MAP = 0, 4, 8, 12
HEALTH_COUNT, ARMOUR_COUNT, TEMP, TARGET = 16, 20, 24, 28
COOKIE, RECEIPT_INDEX = 32, 48
DURATION, AMMO_CHANGED = 132, 136
BASES, TEN = 64, 76
UPGRADE_STEP = 140  # Separate from Vampiric Rounds' ten-point heal.
TEST_ACTION, TEST_RESULT, STATS, WEAPONS, NOT_FULL, SAMPLES = 80, 84, 100, 116, 120, 124
GET_HEALTH, AMMO_MAXED = 0x1A9E50, 0x181750
GET_MAX_HEALTH, SET_HEALTH = 0x1A9E80, 0x1A9EB0
GET_ARMOUR, GET_MAX_ARMOUR, SET_ARMOUR = 0x1A9EE0, 0x1A9F10, 0x1A9F40
SET_MAX_HEALTH, SET_MAX_ARMOUR, SET_TOTAL_ROUNDS = 0x1100B0, 0x111970, 0x1813F0
FORCE_RELOAD, SET_WEAPON_STATE = 0x1873A0, 0x186850
ANCHORS = {
    SET_WEAPON_STATE: bytes.fromhex('83ec088b44240c535556578bf933ed83f806897c241089473c'),
    0x1493D7: bytes.fromhex('8bcee8c27505008b108b421c85c07507c6868009000001'),
    FORCE_RELOAD: bytes.fromhex('83ec4c53558be95733ff8b4528897c240c83f8ff'),
    0x187410: bytes.fromhex('84db0f84170200008b453cbb010000003bc30f8f070200006a058bcd'),
    0x1A09A0: bytes.fromhex('8b81b00700008b481c8b50088b048ac3'),
    GET_MAX_HEALTH: bytes.fromhex('8b4c240485c974148b01ff503c85c0740b8d882c070000e93465f6ff'),
    SET_HEALTH: bytes.fromhex('8b4c240485c974198b01ff503c85c074108b4c2408518d882c070000'),
    GET_MAX_ARMOUR: bytes.fromhex('8b4c240485c974148b01ff503c85c0740b8d882c070000'),
    SET_ARMOUR: bytes.fromhex('8b4c240485c974198b01ff503c85c074108b4c2408518d882c070000'),
    SET_MAX_HEALTH: bytes.fromhex('83ec08d944240cd81dbcd77e00558be9896c2408'),
    SET_MAX_ARMOUR: bytes.fromhex('83ec08d944240cd81dbcd77e00558be9896c2408'),
    SET_TOTAL_ROUNDS: bytes.fromhex('83ec0853558be933db8b0d6ccf8500563bcb0f848a000000'),
    0x176ADE: bytes.fromhex('8bb5b007000033ed8974241c896c2420397e14'),
    0x179EFB: bytes.fromhex('8a4b388b33884c241c8bcb8b54241c52e8'),
    0x179D67: bytes.fromhex('85db740c8b5424188bcb52e879760000'),
}


def build_rewards(control, testing=False, service=None):
    """ESI = living player, EDI = unlocked map index; preserve all registers/flags."""
    c = Code(control + CODE_OFFSET)
    def store(offset, value): c.emit(b'\xc7\x05' + u32(control+offset) + u32(value))
    def get(rva, offset):
        c.emit(b'\x56'); c.jump(b'\xe8', BASE+rva)
        c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(control+offset))
    from . import traps as t, services as s
    from .profile import STEAM
    def held_weapon(reject):
        c.emit(b'\x8b\x9e\xb0\x07\x00\x00\x85\xdb'); c.jump(b'\x0f\x84',reject)
        c.emit(b'\x83\x7b\x14\x40'); c.jump(b'\x0f\x87',reject)
        c.emit(b'\x8b\x43\x1c\x3b\x43\x14'); c.jump(b'\x0f\x83',reject)
        c.emit(b'\x8b\x53\x08\x85\xd2'); c.jump(b'\x0f\x84',reject)
        c.emit(b'\x8b\x1c\x82\x85\xdb'); c.jump(b'\x0f\x84',reject)
        c.emit(b'\x80\x7b\x38\x00'); c.jump(b'\x0f\x84',reject)
    c.emit(b'\x9c\x60')
    # Reassert capacity floors, not additive bonuses: reconnects, old saves and
    # campaign-carried inventory cannot stack upgrades or erase larger native caps.
    for count, getter, setter, label in ((HEALTH_COUNT, GET_MAX_HEALTH, SET_MAX_HEALTH, 'hp'),
                                         (ARMOUR_COUNT, GET_MAX_ARMOUR, SET_MAX_ARMOUR, 'ap')):
        c.emit(b'\x83\x3d'+u32(control+count)+b'\x00')
        c.jump(b'\x0f\x84', label+'_done')
        c.emit(b'\x83\x3d'+u32(control+count)+bytes([UPGRADE_LIMIT]))
        c.jump(b'\x0f\x87', label+'_done')
        c.emit(b'\xa1'+u32(BASE+GAME_DIFFICULTY)+b'\x83\xf8\x02')
        c.jump(b'\x0f\x87', label+'_done')
        c.emit(b'\xdb\x05'+u32(control+count)+b'\xd8\x0d'+u32(control+UPGRADE_STEP))
        c.emit(b'\xd8\x04\x85'+u32(control+BASES)+b'\xd9\x1d'+u32(control+TARGET))
        get(getter, TEMP)
        c.emit(b'\xa1'+u32(control+TEMP)+b'\x3b\x05'+u32(control+TARGET))
        c.jump(b'\x0f\x83', label+'_done')
        c.emit(b'\xff\x35'+u32(control+TARGET)+b'\x8d\x8e\x2c\x07\x00\x00')
        c.jump(b'\xe8', BASE+setter)  # thiscall, callee pops float argument
        c.label(label+'_done')
    if service is not None:
        # Count completed cycles, keeping the firing lock through the final reload.
        c.emit(b'\xa1'+u32(service+s.OBSERVER)+b'\x85\xc0'); c.jump(b'\x0f\x84','cancel_reload')
        c.emit(b'\x3b\x05'+u32(BASE+STEAM.commands+0x204)); c.jump(b'\x0f\x85','cancel_reload')
        c.emit(b'\x3b\x35'+u32(control+t.OWNER)); c.jump(b'\x0f\x85','cancel_reload')
        c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\x3b\x05'+u32(control+t.GENERATION))
        c.jump(b'\x0f\x85','cancel_reload')
        c.emit(b'\x83\x3d'+u32(control+t.RELOADS)+b'\x00'); c.jump(b'\x0f\x84','reload_done')
        held_weapon('reload_done')
        c.emit(b'\x3b\x1d'+u32(control+t.RELOAD_WEAPON)); c.jump(b'\x0f\x84','same_reload_weapon')
        store(t.RELOAD_WEAPON,0)  # Switching cannot complete the previous gun's cycle.
        c.label('same_reload_weapon')
        c.emit(b'\x8b\x03\x85\xc0'); c.jump(b'\x0f\x84','reload_done')
        c.emit(b'\x8b\x40\x1c\x83\xf8\x02'); c.jump(b'\x0f\x82','reload_done')
        c.emit(b'\x83\xf8\x05'); c.jump(b'\x0f\x87','reload_done')  # Guns only; exclude C4/beacons.
        # Avoid vehicles and non-gun equipment; only restart idle/ready weapons.
        c.emit(b'\x83\xbe'+u32(t.HUMAN_STATE)+b'\x09'); c.jump(b'\x0f\x84','reload_done')
        c.emit(b'\x83\x7b\x3c\x01'); c.jump(b'\x0f\x87','reload_done')
        c.emit(b'\x83\x3d'+u32(control+t.RELOAD_WEAPON)+b'\x00'); c.jump(b'\x0f\x84','start_reload')
        store(t.RELOAD_WEAPON,0)
        c.emit(b'\xff\x0d'+u32(control+t.RELOADS)); c.jump(b'\x0f\x84','reload_done')
        c.label('start_reload')
        c.emit(b'\x89\xd9'); c.jump(b'\xe8',BASE+FORCE_RELOAD)
        # With no reserve the normal command can refuse; the native reload state
        # still provides the animation/delay without inventing or deleting ammo.
        c.emit(b'\x83\x7b\x3c\x05'); c.jump(b'\x0f\x84','reload_started')
        c.emit(b'\x89\xd9\x6a\x05'); c.jump(b'\xe8',BASE+SET_WEAPON_STATE)
        c.label('reload_started'); c.emit(b'\x89\x1d'+u32(control+t.RELOAD_WEAPON))
        c.jump(b'\xe9','reload_done')
        c.label('cancel_reload')
        for offset in (t.RELOADS,t.RELOAD_WEAPON,*t.TIMERS): store(offset,0)
        c.label('reload_done')
    if service is not None:
        c.emit(b'\x83\x3d'+u32(service+s.ONLINE)+b'\x01')
        c.jump(b'\x0f\x85', 'done')
    c.emit(b'\xa1'+u32(control+REQUEST)+b'\x85\xc0')
    c.jump(b'\x0f\x84', 'done')
    store(REQUEST, 0)
    c.emit(b'\x3b\x35'+u32(control+PLAYER)); c.jump(b'\x0f\x85','rejected')
    c.emit(b'\x3b\x3d'+u32(control+MAP)); c.jump(b'\x0f\x85','rejected')
    for kind, label in ((1,'health'),(2,'armour'),(3,'ammo'),(6,'trap'),(7,'butter'),(8,'concrete'),(9,'disco'),(10,'moon')):
        c.emit(b'\x83\xf8'+bytes([kind])); c.jump(b'\x0f\x84',label)
    for kind, name in enumerate(BUFFS,11):
        c.emit(b'\x83\xf8'+bytes([kind])); c.jump(b'\x0f\x84',name)
    c.jump(b'\xe9','rejected')
    for label, getter, current, setter in (('health',GET_MAX_HEALTH,GET_HEALTH,SET_HEALTH),('armour',GET_MAX_ARMOUR,GET_ARMOUR,SET_ARMOUR)):
        c.label(label); get(getter,TEMP)
        get(current, TARGET)
        c.emit(b'\xa1'+u32(control+TARGET)+b'\x3b\x05'+u32(control+TEMP))
        c.jump(b'\x0f\x83', 'already_full')
        c.emit(b'\xff\x35'+u32(control+TEMP)+b'\x56'); c.jump(b'\xe8',BASE+setter)
        c.emit(b'\x83\xc4\x08'); c.jump(b'\xe9','applied')
    c.label('trap')
    c.emit(b'\x6a\x00\x56'); c.jump(b'\xe8',BASE+SET_ARMOUR)
    c.emit(b'\x83\xc4\x08'); c.jump(b'\xe9','applied')
    for label, timer, name in (('butter',None,BUTTERFINGERS),('concrete',t.CONCRETE_END,CONCRETE_BOOTS),
                              ('disco',t.DISCO_END,DISCO_HAVOC),('moon',t.MOON_END,MOON_BOOTS),
                              *((name, offset, name) for offset,name in t.TIMERS.items() if name in BUFFS)):
        c.label(label)
        if service is None:
            c.jump(b'\xe9','rejected'); continue
        c.emit(b'\x89\x35'+u32(control+t.OWNER))
        c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\xa3'+u32(control+t.GENERATION))
        if timer is None:
            c.emit(b'\x81\x3d'+u32(control+t.RELOADS)+u32(1000000))
            c.jump(b'\x0f\x87','rejected')
            c.emit(b'\x83\x05'+u32(control+t.RELOADS)+bytes([BUTTERFINGERS_RELOADS]))
        else:
            # Extend remaining playable time; do not multiply effect strength.
            c.emit(b'\xc7\x05'+u32(control+DURATION)+struct.pack('<f',t.DURATIONS[name]))
            c.emit(b'\xd9\x05'+u32(control+timer)+b'\xd8\x05'+u32(control+DURATION)+b'\xd9\x1d'+u32(control+TARGET))
            c.emit(b'\xa1'+u32(control+TARGET)+b'\x3d'+struct.pack('<f',t.MAX_EFFECT_SECONDS))
            c.jump(b'\x0f\x87','rejected')
            c.emit(b'\xa3'+u32(control+timer))
        if timer == t.HOLOGRAM_END:
            from .bonuses import HOLO_ACTIVATION
            store(HOLO_ACTIVATION,1)
            c.jump(b'\xe9','done')  # Bodyguard helper acknowledges actual spawn/refresh.
            continue
        c.jump(b'\xe9','applied')
    c.label('ammo')
    store(AMMO_CHANGED,0)
    c.emit(b'\x8b\xae\xb0\x07\x00\x00\x85\xed'); c.jump(b'\x0f\x84','rejected')
    c.emit(b'\x83\x7d\x14\x40'); c.jump(b'\x0f\x87','rejected')
    c.emit(b'\x8b\x5d\x08\x85\xdb'); c.jump(b'\x0f\x84','rejected')
    c.emit(b'\x31\xff')
    c.label('weapon')
    c.emit(b'\x3b\x7d\x14'); c.jump(b'\x0f\x83','ammo_done')
    c.emit(b'\x8b\x0c\xbb\x85\xc9'); c.jump(b'\x0f\x84','next_weapon')
    c.emit(b'\x80\x79\x38\x00'); c.jump(b'\x0f\x84','next_weapon')
    c.emit(b'\x51'); c.jump(b'\xe8',BASE+AMMO_MAXED); c.emit(b'\x59\x84\xc0')
    c.jump(b'\x0f\x85','next_weapon')
    store(AMMO_CHANGED,1)
    # Native setter clamps magazine and reserve to their definition maxima,
    # preserving definitions with infinite reserve. No weapon ownership changes.
    c.emit(b'\x68'+u32(1000000)); c.jump(b'\xe8',BASE+SET_TOTAL_ROUNDS)
    c.label('next_weapon'); c.emit(b'\x47'); c.jump(b'\xe9','weapon')
    c.label('ammo_done')
    c.emit(b'\x83\x3d'+u32(control+AMMO_CHANGED)+b'\x00'); c.jump(b'\x0f\x84','already_full')
    c.label('applied'); store(RESULT,1); c.jump(b'\xe9','done')
    c.label('already_full'); store(RESULT,3); c.jump(b'\xe9','done')
    c.label('rejected'); store(RESULT,2)
    c.label('done')
    if testing:
        # Dedicated disposable-room test build only: weaken Havoc/drain ammo and
        # sample native getters. Never included in the production reward program.
        c.emit(b'\xa1'+u32(control+TEST_ACTION)+b'\x85\xc0'); c.jump(b'\x0f\x84','sample_stats')
        store(TEST_ACTION,0)
        c.emit(b'\x83\xf8\x01'); c.jump(b'\x0f\x85','sample_stats')
        for setter in (SET_HEALTH,SET_ARMOUR):
            c.emit(b'\x68'+struct.pack('<f',25)+b'\x56');c.jump(b'\xe8',BASE+setter);c.emit(b'\x83\xc4\x08')
        c.emit(b'\x8b\xae\xb0\x07\x00\x00\x8b\x5d\x08\x31\xff')
        c.label('test_drain')
        c.emit(b'\x3b\x7d\x14');c.jump(b'\x0f\x83','drained')
        c.emit(b'\x8b\x0c\xbb\x85\xc9');c.jump(b'\x0f\x84','drain_next')
        c.emit(b'\x80\x79\x38\x00');c.jump(b'\x0f\x84','drain_next')
        c.emit(b'\x6a\x00');c.jump(b'\xe8',BASE+SET_TOTAL_ROUNDS)
        c.label('drain_next');c.emit(b'\x47');c.jump(b'\xe9','test_drain')
        c.label('drained');store(TEST_RESULT,1)
        c.label('sample_stats')
        for i,getter in enumerate((GET_HEALTH,GET_ARMOUR,GET_MAX_HEALTH,GET_MAX_ARMOUR)):
            get(getter,STATS+i*4)
        store(WEAPONS,0);store(NOT_FULL,0)
        c.emit(b'\x8b\xae\xb0\x07\x00\x00\x8b\x5d\x08\x31\xff')
        c.label('sample_weapon')
        c.emit(b'\x3b\x7d\x14');c.jump(b'\x0f\x83','sampled')
        c.emit(b'\x8b\x0c\xbb\x85\xc9');c.jump(b'\x0f\x84','sample_next')
        c.emit(b'\x80\x79\x38\x00');c.jump(b'\x0f\x84','sample_next')
        c.emit(b'\xff\x05'+u32(control+WEAPONS));c.jump(b'\xe8',BASE+AMMO_MAXED)
        c.emit(b'\x84\xc0');c.jump(b'\x0f\x85','sample_next')
        c.emit(b'\xff\x05'+u32(control+NOT_FULL))
        c.label('sample_next');c.emit(b'\x47');c.jump(b'\xe9','sample_weapon')
        c.label('sampled');c.emit(b'\xff\x05'+u32(control+SAMPLES))
    c.emit(b'\x61\x9d\xc3')
    return c.finish()


class NativeRewards:
    automatic_items = (*TRAPS, *BUFFS)

    def __init__(self, memory, testing=False, service=None):
        self.memory = memory
        for rva, expected in ANCHORS.items():
            if memory.read(BASE+rva,len(expected)) != expected:
                raise UnsupportedBuild(f'Reward anchor differs at {rva:#x}')
        self.control = memory.allocate()
        memory.write(self.control, b'\0'*4096)
        self.cookie = uuid.uuid4().hex
        memory.write(self.control+COOKIE, bytes.fromhex(self.cookie))
        memory.write(self.control+BASES, struct.pack('<4f',200,100,75,10))
        memory.write(self.control+UPGRADE_STEP, struct.pack('<f',UPGRADE_AMOUNT))
        program = build_rewards(self.control, testing=testing, service=service)
        if len(program) > 4096:
            raise ValueError('Reward code exceeds its page')
        memory.write(self.control+CODE_OFFSET, program)
        if memory.read(self.control+CODE_OFFSET,len(program)) != program:
            raise OSError('Reward program read-back failed')
        memory.make_executable(self.control+CODE_OFFSET)
        self.pending = None

    def update(self, client, player, map_index, active):
        mem, ctrl = self.memory, self.control
        enabled = client.capture_ready and client.config.get('supply_items',False)
        if not enabled and self.automatic_items:
            from .traps import RELOADS, RELOAD_WEAPON, TIMERS
            for offset in (RELOADS,RELOAD_WEAPON,*TIMERS):
                mem.write_u32(ctrl+offset,0)
        for offset, name in zip((HEALTH_COUNT, ARMOUR_COUNT), UPGRADES):
            mem.write_u32(ctrl+offset, min(UPGRADE_LIMIT,client.inventory.count(ITEM_IDS[name])) if enabled else 0)
        if self.pending is not None:
            result = mem.u32(ctrl+RESULT)
            if result:
                index, name = self.pending
                if result not in (1, 2, 3):
                    raise ValueError('Invalid native item receipt')
                client.progress.resolve_activation(index, 'applied' if result == 1 else 'refunded')
                import logging
                detail = 'applied' if result == 1 else 'already full; kept in inventory' if result == 3 else 'not applied; returned to inventory'
                logging.getLogger('Renegade').info('%s: %s', name, detail)
                if hasattr(client, 'notifications'):
                    client.notifications.notice(f"{name}: " + detail,
                                                ITEM_FLAGS[name])
                self.pending = None
                if client.supply_request == name:
                    client.supply_request = None
            return
        if not enabled or not client.ready or not active or not player or not 0 <= map_index < 12:
            return
        from .traps import RELOADS
        name = next((n for n in self.automatic_items if client.effect_indexes(n)), client.supply_request)
        indexes = client.effect_indexes(name)
        if not indexes:
            return
        index = indexes[0]
        mem.write_u32(ctrl+RESULT,0)
        mem.write_u32(ctrl+PLAYER,player)
        mem.write_u32(ctrl+MAP,map_index)
        mem.write_u32(ctrl+RECEIPT_INDEX,index)
        client.progress.reserve_activation(index, dict(pid=mem.pid, control=ctrl, cookie=self.cookie))
        self.pending = (index,name)
        kind = 6+TRAPS.index(name) if name in TRAPS else 11+BUFFS.index(name) if name in BUFFS else SUPPLIES.index(name)+1
        mem.write_u32(ctrl+REQUEST,kind)

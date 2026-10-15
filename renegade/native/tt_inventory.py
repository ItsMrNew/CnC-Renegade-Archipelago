"""TT b9000 inventory and banked supplies, executed on the combat thread.

All addresses are TT bindings. The receipt layout is shared with Steam so interrupted activations use the same
durable recovery journal.
"""
import struct
import uuid

from ..data import UPGRADE_AMOUNT, UPGRADE_LIMIT, ARMOUR_TRAP, CONCRETE_BOOTS, MOON_BOOTS, DISCO_HAVOC
from ..data import BUTTERFINGERS, BUTTERFINGERS_RELOADS, BULLET_HOSE, VAMPIRIC_ROUNDS, HOLOGRAM_HAVOC
from . import services as s, weapons as w, rewards as r, traps as t
from .profile import UnsupportedBuild
from .thunk import Code, CODE_OFFSET, u32
from .tt_discs import guard

GET_HEALTH, GET_MAX_HEALTH, SET_HEALTH = 0x729190, 0x7291C0, 0x7291F0
GET_ARMOUR, GET_MAX_ARMOUR, SET_ARMOUR = 0x729220, 0x729250, 0x729280
DIFFICULTY = 0x80F458
SET_CLIP, SET_RESERVE = 0x285DE0, 0x285EA0  # BandTest WeaponClass setters.
REQUEST_GENERATION = 144
# Keep scratch space beyond the shared effect/timer data (160..1884).
CLIP, RESERVE, CLIP_MAX, RESERVE_MAX = 2000, 2004, 2008, 2012
APPLY_DAMAGE = 0x72ACC0
DEATH_GENERATION, DEATH_MAP = s.DEATH_GENERATION, s.DEATH_MAP


def weapon_bindings(a):
    return {w.ADD_WEAPON: 0x6F3C00, w.REMOVE_WEAPON: a.band_base+0x27F500,
            w.SELECT_INDEX: a.band_base+0x27FFA0, w.SELECT_NAME: 0x6F3F60}


def expected_anchors(a):
    b = a.band_base
    return {
        0x6F3C00: bytes.fromhex('8b44240c8b542404568bf18b4c240c505152e8c9f6030083c4048bce50e81efeffff5ec20c00'),
        0x7332E0: bytes.fromhex('8b4424046a016801b0000050e8ef35dfff83c40cc3'),
        0x6F3F60: bytes.fromhex('538b5c24085556578bf985db0f84bf000000803b00'),
        b+0x27F500: bytes.fromhex('5355568b742410578bf985f60f84bc0000008b57143bf20f8db10000008b4f1c'),
        b+0x27FFA0: bytes.fromhex('568bf1578b4e1c8b7c240c3bf9743f3b7e147d3a8b46088b0c8885c97405'),
        b+0x3D20: bytes.fromhex('f30f104c240453560f57c00f2fc1578bf9760ac744241000000000eb19'),
        b+0x3ED0: bytes.fromhex('f30f104c240453560f57c00f2fc1578bf9760ac744241000000000eb19'),
        b+0x5E80: bytes.fromhex('518339ffc7042400000000741aff318d4c2404e888d0ffff8b44240483c40485c074048b0059c3'),
        b+SET_CLIP: bytes.fromhex('568bf1837e24ff740fff76248d4c240ce8cbd3d7ff83c404'),
        b+SET_RESERVE: bytes.fromhex('568bf1837e28ff740fff76288d4c240ce80bd3d7ff83c404'),
        b+0x285E98: bytes.fromhex('5ec20400'),
        b+0x285F58: bytes.fromhex('5ec20400'),
        GET_HEALTH: bytes.fromhex('8b4c240485c974148b01ff503c85c0740b8d882c070000e99483f6ff'),
        GET_MAX_HEALTH: bytes.fromhex('8b4c240485c974148b01ff503c85c0740b8d882c070000e96489f6ff'),
        GET_ARMOUR: bytes.fromhex('8b4c240485c974148b01ff503c85c0740b8d882c070000e9c49bf6ff'),
        GET_MAX_ARMOUR: bytes.fromhex('8b4c240485c974148b01ff503c85c0740b8d882c070000e994a1f6ff'),
        SET_HEALTH: bytes.fromhex('8b4c240485c974198b01ff503c85c074108b4c2408518d882c070000e84f73f6ff'),
        SET_ARMOUR: bytes.fromhex('8b4c240485c974198b01ff503c85c074108b4c2408518d882c070000e87f8bf6ff'),
        APPLY_DAMAGE: bytes.fromhex('8b4c240483ec285333db3bcb560f84d30000008b01ff503c8bf03bf30f84c4000000'),
        **{address: b'\xe9'+u32((target-address-5) & 0xFFFFFFFF) for address, target in (
            (0x6F3A40, b+0x280360), (0x6F3930, b+0x27F500),
            (0x6F4100, b+0x27FFA0), (0x691820, b+0x3D20), (0x6930E0, b+0x3ED0))},
    }


def install_code(memory, address, program):
    if len(program) > 4096:
        raise ValueError('TT inventory program exceeds code page')
    memory.write(address, program)
    if memory.read(address, len(program)) != program:
        raise OSError('TT inventory code read-back failed')
    memory.make_executable(address)


class NativeWeapons(w.NativeWeapons):
    def __init__(self, memory, service, addresses):
        self.memory, self.last_errors = memory, 0
        self.control = memory.allocate()
        w.initialize(memory, self.control)
        self.code = self.control+CODE_OFFSET
        install_code(memory, self.code, w.build_weapons(self.control, service,
            bindings=weapon_bindings(addresses), native_repairs_selection=True))


def build_rewards(control, service, addresses, testing=False):
    c = Code(control+CODE_OFFSET)
    b = addresses.band_base
    def store(offset, value): c.emit(b'\xc7\x05'+u32(control+offset)+u32(value))
    def get(address, offset):
        c.emit(b'\x56'); c.jump(b'\xe8', address)
        c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(control+offset))
    def put_stat(address, offset):
        c.emit(b'\xff\x35'+u32(control+offset)+b'\x56')
        c.jump(b'\xe8', address); c.emit(b'\x83\xc4\x08')
    def bag_loop(prefix, action):
        c.emit(b'\x8b\xae'+u32(0x7B0)+b'\x85\xed'); c.jump(b'\x0f\x84',prefix+'_end')
        c.emit(b'\x83\x7d\x08\x00'); c.jump(b'\x0f\x84',prefix+'_end')
        c.emit(b'\x8b\x7d\x14\x81\xff'+u32(128)); c.jump(b'\x0f\x87',prefix+'_end')
        c.label(prefix+'_next')
        c.emit(b'\x4f'); c.jump(b'\x0f\x8e',prefix+'_end')
        c.emit(b'\x8b\x45\x08\x8b\x1c\xb8\x85\xdb'); c.jump(b'\x0f\x84',prefix+'_next')
        c.emit(b'\x80\x7b\x38\x00'); c.jump(b'\x0f\x84',prefix+'_next')
        c.emit(b'\x83\x3b\x00'); c.jump(b'\x0f\x84',prefix+'_next')
        action()
        c.jump(b'\xe9',prefix+'_next'); c.label(prefix+'_end')
    def ammo_values():
        # TT SafeData<int>::operator int: ECX points to a handle, EAX is its value.
        # Reading reserve alone misses a partially empty magazine at full reserve.
        for field, output, definition in ((0x24,CLIP,False),(0x28,RESERVE,False),
                                          (0x74,CLIP_MAX,True),(0x84,RESERVE_MAX,True)):
            if definition: c.emit(b'\x8b\x0b\x81\xc1'+u32(field))
            else: c.emit(b'\x8d\x4b'+bytes([field]))
            c.jump(b'\xe8', b+0x5E80); c.emit(b'\xa3'+u32(control+output))
    def ammo_missing(full_label):
        ammo_values()
        c.emit(b'\xa1'+u32(control+CLIP)+b'\x3b\x05'+u32(control+CLIP_MAX))
        c.jump(b'\x0f\x8c', full_label+'_missing')
        c.emit(b'\xa1'+u32(control+RESERVE)+b'\x83\xf8\xff')
        c.jump(b'\x0f\x84', full_label)
        c.emit(b'\x3b\x05'+u32(control+RESERVE_MAX)); c.jump(b'\x0f\x8d', full_label)
        c.label(full_label+'_missing')
    c.emit(b'\x9c\x60')
    for count, getter, setter, label in ((r.HEALTH_COUNT,GET_MAX_HEALTH,b+0x3D20,'hp'),
                                        (r.ARMOUR_COUNT,GET_MAX_ARMOUR,b+0x3ED0,'ap')):
        c.emit(b'\x83\x3d'+u32(control+count)+b'\x00'); c.jump(b'\x0f\x84',label)
        c.emit(b'\x83\x3d'+u32(control+count)+bytes([UPGRADE_LIMIT])); c.jump(b'\x0f\x87',label)
        c.emit(b'\xa1'+u32(DIFFICULTY)+b'\x83\xf8\x02'); c.jump(b'\x0f\x87',label)
        c.emit(b'\xdb\x05'+u32(control+count)+b'\xd8\x0d'+u32(control+r.UPGRADE_STEP))
        c.emit(b'\xd8\x04\x85'+u32(control+r.BASES)+b'\xd9\x1d'+u32(control+r.TARGET))
        get(getter,r.TEMP)
        c.emit(b'\xa1'+u32(control+r.TEMP)+b'\x3b\x05'+u32(control+r.TARGET))
        c.jump(b'\x0f\x83',label)
        c.emit(b'\xff\x35'+u32(control+r.TARGET)+b'\x8d\x8e'+u32(0x72C))
        c.jump(b'\xe8',setter); c.label(label)
    c.emit(b'\x3b\x35'+u32(control+t.OWNER));c.jump(b'\x0f\x85','clear_effects')
    c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\x3b\x05'+u32(control+t.GENERATION))
    c.jump(b'\x0f\x84','effects_current')
    c.label('clear_effects')
    for offset in (*t.TIMERS,t.RELOADS,t.RELOAD_WEAPON):store(offset,0)
    c.label('effects_current')
    c.emit(b'\x83\x3d'+u32(service+s.ONLINE)+b'\x01'); c.jump(b'\x0f\x85','done')
    c.emit(b'\xa1'+u32(control+r.REQUEST)+b'\x85\xc0'); c.jump(b'\x0f\x84','done')
    store(r.REQUEST,0)
    c.emit(b'\x3b\x35'+u32(control+r.PLAYER)); c.jump(b'\x0f\x85','rejected')
    c.emit(b'\x3b\x3d'+u32(control+r.MAP)); c.jump(b'\x0f\x85','rejected')
    c.emit(b'\x8b\x15'+u32(service+s.GENERATION)+b'\x3b\x15'+u32(control+REQUEST_GENERATION))
    c.jump(b'\x0f\x85','rejected')
    for kind,label in ((1,'health'),(2,'armour'),(3,'ammo'),(6,'armour_loss'),(7,'butter'),
                       (8,'concrete'),(9,'disco'),(10,'moon'),(11,'bullet'),(12,'vampire'),(13,'hologram')):
        c.emit(b'\x83\xf8'+bytes([kind])); c.jump(b'\x0f\x84',label)
    c.jump(b'\xe9','rejected')
    for label, maximum, current, setter in (('health',GET_MAX_HEALTH,GET_HEALTH,SET_HEALTH),
                                            ('armour',GET_MAX_ARMOUR,GET_ARMOUR,SET_ARMOUR)):
        c.label(label); get(maximum,r.TEMP); get(current,r.TARGET)
        c.emit(b'\xa1'+u32(control+r.TARGET)+b'\x3b\x05'+u32(control+r.TEMP))
        c.jump(b'\x0f\x83','full')
        put_stat(setter,r.TEMP); c.jump(b'\xe9','applied')
    c.label('armour_loss')
    c.emit(b'\x6a\x00\x56'); c.jump(b'\xe8',SET_ARMOUR)
    c.emit(b'\x83\xc4\x08'); c.jump(b'\xe9','applied')
    c.label('butter')
    c.emit(b'\x81\x3d'+u32(control+t.RELOADS)+u32(1000000));c.jump(b'\x0f\x87','rejected')
    c.emit(b'\x83\x05'+u32(control+t.RELOADS)+bytes([BUTTERFINGERS_RELOADS]))
    c.emit(b'\x89\x35'+u32(control+t.OWNER))
    c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\xa3'+u32(control+t.GENERATION))
    c.jump(b'\xe9','applied')
    for label,offset,name in (('concrete',t.CONCRETE_END,CONCRETE_BOOTS),
                              ('disco',t.DISCO_END,DISCO_HAVOC),('moon',t.MOON_END,MOON_BOOTS),
                              ('bullet',t.BULLET_END,BULLET_HOSE),('vampire',t.VAMPIRE_END,VAMPIRIC_ROUNDS),
                              ('hologram',t.HOLOGRAM_END,HOLOGRAM_HAVOC)):
        c.label(label)
        store(r.DURATION,struct.unpack('<I',struct.pack('<f',t.DURATIONS[name]))[0])
        c.emit(b'\xd9\x05'+u32(control+offset)+b'\xd8\x05'+u32(control+r.DURATION)+b'\xd9\x1d'+u32(control+r.TARGET))
        c.emit(b'\xa1'+u32(control+r.TARGET)+b'\x3d'+struct.pack('<f',t.MAX_EFFECT_SECONDS))
        c.jump(b'\x0f\x87','rejected');c.emit(b'\xa3'+u32(control+offset))
        c.emit(b'\x89\x35'+u32(control+t.OWNER))
        c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\xa3'+u32(control+t.GENERATION))
        if offset==t.HOLOGRAM_END:
            from .bonuses import HOLO_ACTIVATION
            store(HOLO_ACTIVATION,1)
            c.jump(b'\xe9','done') # Acknowledge only after a real spawn/refresh.
            continue
        c.jump(b'\xe9','applied')
    c.label('ammo'); store(r.AMMO_CHANGED,0)
    def refill():
        ammo_missing('refill_next')
        # The dormant game2 Set_Total_Rounds calls a SafeData writer TT turns
        # into a no-op. Use the active TT setters behind its exported ammo APIs.
        c.emit(b'\xff\x35'+u32(control+CLIP_MAX)+b'\x8b\xcb'); c.jump(b'\xe8',b+SET_CLIP)
        c.emit(b'\xa1'+u32(control+RESERVE)+b'\x83\xf8\xff')
        c.jump(b'\x0f\x84','keep_unlimited')
        c.emit(b'\xa1'+u32(control+RESERVE_MAX))
        c.label('keep_unlimited')
        c.emit(b'\x50\x8b\xcb'); c.jump(b'\xe8',b+SET_RESERVE)
        c.emit(b'\xff\x05'+u32(control+r.AMMO_CHANGED))
    bag_loop('refill',refill)
    c.emit(b'\x83\x3d'+u32(control+r.AMMO_CHANGED)+b'\x00'); c.jump(b'\x0f\x84','full')
    c.label('applied'); store(r.RESULT,1); c.jump(b'\xe9','done')
    c.label('full'); store(r.RESULT,3); c.jump(b'\xe9','done')
    c.label('rejected'); store(r.RESULT,2)
    c.label('done')
    if testing:
        # Test-only commands. Production never damages the player or drains ammo.
        c.emit(b'\x83\x3d'+u32(control+r.TEST_ACTION)+b'\x02'); c.jump(b'\x0f\x85','sample')
        store(r.TEMP,struct.unpack('<I',struct.pack('<f',25.0))[0])
        for setter in (SET_HEALTH,SET_ARMOUR): put_stat(setter,r.TEMP)
        def drain():
            ammo_values()
            c.emit(b'\x6a\x00\x8b\xcb'); c.jump(b'\xe8',b+SET_CLIP)
            c.emit(b'\x83\x3d'+u32(control+RESERVE)+b'\xff'); c.jump(b'\x0f\x84','drain_next')
            c.emit(b'\x6a\x00\x8b\xcb'); c.jump(b'\xe8',b+SET_RESERVE)
        bag_loop('drain',drain)
        c.label('sample'); store(r.TEST_ACTION,0)
        for index,getter in enumerate((GET_HEALTH,GET_MAX_HEALTH,GET_ARMOUR,GET_MAX_ARMOUR)):
            get(getter,r.STATS+4*index)
        store(r.WEAPONS,0); store(r.NOT_FULL,0)
        def sample():
            c.emit(b'\xff\x05'+u32(control+r.WEAPONS))
            ammo_missing('sample_next'); c.emit(b'\xff\x05'+u32(control+r.NOT_FULL))
        bag_loop('sample',sample)
        c.emit(b'\xff\x05'+u32(control+r.SAMPLES))
    c.emit(b'\x61\x9d\xc3')
    return c.finish()


class NativeRewards(r.NativeRewards):
    automatic_items = (ARMOUR_TRAP,BUTTERFINGERS,CONCRETE_BOOTS,DISCO_HAVOC,MOON_BOOTS,BULLET_HOSE,VAMPIRIC_ROUNDS,HOLOGRAM_HAVOC)

    def __init__(self, memory, service, addresses, testing=False):
        self.memory, self.service = memory, service
        self.control = memory.allocate()
        self.cookie = uuid.uuid4().hex
        memory.write(self.control,b'\0'*4096)
        memory.write(self.control+r.COOKIE,bytes.fromhex(self.cookie))
        memory.write(self.control+r.BASES,struct.pack('<4f',200,100,75,10))
        memory.write(self.control+r.UPGRADE_STEP,struct.pack('<f',UPGRADE_AMOUNT))
        self.code = self.control+CODE_OFFSET
        install_code(memory,self.code,build_rewards(self.control,service,addresses,testing))
        self.pending = None

    def update(self, client, player, map_index, active):
        if self.pending is None:
            self.memory.write_u32(self.control+REQUEST_GENERATION,
                                  self.memory.u32(self.service+s.GENERATION))
        super().update(client,player,map_index,active)


def build_frame(code, control, service, addresses, weapons, rewards, reveal,
                notification_target=None, attempt_target=None, *, testing=False, clock_target=None,
                reload_target=None, hologram_target=None):
    c = Code(code)
    def store(offset, value): c.emit(b'\xc7\x05'+u32(service+offset)+u32(value))
    c.emit(b'\x9c\x60')
    # Reveal has a stricter guard and clears its transient sample. Resolve the
    # authoritative frame sample afterwards, retaining identity while paused.
    c.jump(b'\xe8',reveal)
    guard(c,control,service,'invalid',pickup=False,addresses=addresses,sample=True)
    # A rebuilt command table marks a load; Python must reclaim it first.
    c.emit(b'\xa1'+u32(service+s.OBSERVER)+b'\x85\xc0'); c.jump(b'\x0f\x84','invalid')
    c.emit(b'\x3b\x05'+u32(addresses.commands+0x204)); c.jump(b'\x0f\x85','invalid')
    c.emit(b'\x3b\x35'+u32(control)); c.jump(b'\x0f\x85','new')
    c.emit(b'\x3b\x3d'+u32(control+4)); c.jump(b'\x0f\x84','same')
    c.label('new'); c.emit(b'\xff\x05'+u32(service+s.GENERATION))
    store(s.ALIVE,0); store(s.DEATH_REQUEST,0)
    c.emit(b'\x89\x35'+u32(control)+b'\x89\x3d'+u32(control+4))
    c.label('same')
    if attempt_target: c.jump(b'\xe8',attempt_target)
    c.emit(b'\xa1'+u32(service+s.HEALTH)+b'\x25'+u32(0x7FFFFFFF))
    c.emit(b'\x3d'+u32(0x7F800000)); c.jump(b'\x0f\x83','invalid')
    c.emit(b'\xa1'+u32(service+s.HEALTH)+b'\x85\xc0'); c.jump(b'\x0f\x8e','dead')
    c.emit(b'\x83\x3d'+u32(service+s.PLAYABLE)+b'\x01'); c.jump(b'\x0f\x85','done')
    store(s.ALIVE,1)
    if testing:
        # Disposable diagnostic only. Production has no local-death command.
        c.emit(b'\x83\x3d'+u32(service+s.TEST_ACTION)+b'\x01'); c.jump(b'\x0f\x85','test_done')
        store(s.TEST_ACTION,0)
        c.emit(b'\x6a\x00\x68'+u32(service+s.WARHEAD)+b'\x68'+struct.pack('<f',100000.0)+b'\x56')
        c.jump(b'\xe8',APPLY_DAMAGE); c.emit(b'\x83\xc4\x10\x56')
        c.jump(b'\xe8',GET_HEALTH); c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(service+s.HEALTH))
        store(s.TEST_RESULT,1)
        c.emit(b'\x83\x3d'+u32(service+s.HEALTH)+b'\x00'); c.jump(b'\x0f\x8e','dead')
        store(s.TEST_RESULT,0xFFFFFFFF)
        c.label('test_done')
    for offset in (s.ONLINE,s.DEATH_ENABLED,s.DEATH_REQUEST):
        c.emit(b'\x83\x3d'+u32(service+offset)+b'\x01'); c.jump(b'\x0f\x85','rewards')
    store(s.DEATH_REQUEST,0)
    c.emit(b'\x3b\x35'+u32(service+s.REQUEST_PLAYER)); c.jump(b'\x0f\x85','rewards')
    c.emit(b'\x3b\x3d'+u32(service+s.REQUEST_MAP)); c.jump(b'\x0f\x85','rewards')
    c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\x3b\x05'+u32(service+DEATH_GENERATION))
    c.jump(b'\x0f\x85','rewards')
    c.emit(b'\x6a\x00\x68'+u32(service+s.WARHEAD)+b'\x68'+struct.pack('<f',100000.0)+b'\x56')
    c.jump(b'\xe8',APPLY_DAMAGE); c.emit(b'\x83\xc4\x10\x56')
    c.jump(b'\xe8',GET_HEALTH); c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(service+s.HEALTH))
    c.emit(b'\xa1'+u32(service+s.HEALTH)+b'\x85\xc0'); c.jump(b'\x0f\x8f','kill_failed')
    store(s.ALIVE,0); store(s.PLAYABLE,0)  # Never echo a received death.
    c.emit(b'\xff\x05'+u32(service+s.DEATH_APPLIED)); c.jump(b'\xe9','done')
    c.label('kill_failed'); c.emit(b'\xff\x05'+u32(service+s.DEATH_FAILED))
    c.label('rewards')
    for target in (weapons,rewards): c.jump(b'\xe8',target)
    if reload_target:c.jump(b'\xe8',reload_target)
    if clock_target: c.jump(b'\xe8',clock_target)
    if notification_target: c.jump(b'\xe8',notification_target)
    c.jump(b'\xe9','done')
    c.label('dead')
    c.emit(b'\x83\x3d'+u32(service+s.ALIVE)+b'\x01'); c.jump(b'\x0f\x85','not_new_death')
    c.emit(b'\x89\x3d'+u32(service+DEATH_MAP)+b'\xff\x05'+u32(service+s.DEATH_COUNT))
    c.label('not_new_death'); store(s.ALIVE,0); store(s.DEATH_REQUEST,0)
    c.jump(b'\xe9','done')
    c.label('invalid')
    for off in (s.ALIVE,s.PLAYABLE,s.PLAYER,s.DEATH_REQUEST): store(off,0)
    c.emit(b'\xc7\x05'+u32(control)+u32(0))
    c.label('done')
    # Also run while paused/dead/offline so the ally freezes or is cleaned up.
    if hologram_target:c.jump(b'\xe8',hologram_target)
    c.emit(b'\x61\x9d\xc3')
    return c.finish()


class NativeInventory:
    def __init__(self, memory, service, addresses, reveal, testing=False):
        for address, expected in expected_anchors(addresses).items():
            if memory.read(address,len(expected)) != expected:
                raise UnsupportedBuild(f'TT inventory anchor differs at {address:#x}')
        self.memory, self.service = memory, service
        self.control = memory.allocate()
        memory.write(self.control,b'\0'*4096)
        self.weapons = NativeWeapons(memory,service,addresses)
        self.rewards = NativeRewards(memory,service,addresses,testing)
        from .tt_notifications import NativeNotifications
        from .attempts import NativeAttempts
        self.notifications = NativeNotifications(memory)
        self.attempts = NativeAttempts(memory,service,addresses=addresses)
        from .tt_traps import NativeTraps
        self.traps = NativeTraps(memory,self.rewards.control,service,addresses)
        from .tt_weapon_effects import NativeWeaponEffects
        self.weapon_effects = NativeWeaponEffects(memory,self.rewards.control,service,addresses,testing=testing)
        from .tt_vampiric import NativeVampiric
        self.vampiric = NativeVampiric(memory,self.rewards.control,service,addresses,testing=testing)
        from .tt_hologram import NativeHologram
        self.hologram = NativeHologram(memory,self.rewards.control,service,addresses,testing=testing)
        memory.write(service+s.WARHEAD,b'BlamoKiller\0')
        self.code = self.control+CODE_OFFSET
        install_code(memory,self.code,build_frame(self.code,self.control,service,addresses,
            self.weapons.code,self.rewards.code,reveal,
            self.notifications.control+CODE_OFFSET,self.attempts.code,testing=testing,
            clock_target=self.traps.clock,reload_target=self.weapon_effects.code,
            hologram_target=self.hologram.update_code))

    def invalidate(self):
        self.memory.write_u32(self.control,0)
        self.memory.write_u32(self.service+s.GENERATION,self.memory.u32(self.service+s.GENERATION)+1)

    def update(self, client):
        mem, ctrl = self.memory, self.service
        mem.write_u32(ctrl+s.ONLINE,int(client.ready))
        self.weapon_effects.update()
        self.vampiric.update()
        self.hologram.update()
        self.traps.update()
        self.weapons.update(client)
        active = mem.u32(ctrl+s.PLAYABLE) == 1 and mem.u32(ctrl+s.ALIVE) == 1
        self.rewards.update(client,mem.u32(ctrl+s.PLAYER),mem.u32(ctrl+s.MAP_INDEX),active)
        return active

    def close(self):
        self.memory.write_u32(self.service+s.ONLINE,0)
        self.hologram.close()
        errors=[]
        for component in (self.vampiric,self.weapon_effects,self.traps,self.weapons):
            try:component.close()
            except OSError as error:errors.append(str(error))
        if errors:raise OSError('; '.join(errors))

"""Temporary helpful items for the fingerprinted Steam executable.

Weapon hooks affect only Havoc; shared definitions and ammo counts are never
modified. Native damage events distinguish enemy hits from allies and repairs.
"""
import struct
import time
from . import traps as t, services as s, rewards as r
from .thunk import Code, CODE_OFFSET, u32
from .savebuilder import BASE
from .profile import UnsupportedBuild
from .memory import MemoryError

HALF, ONE = 1300, 1304
HOLO_ID, HOLO_PTR, HOLO_GENERATION, CLEANUP, HOLO_PAUSED = 1308, 1312, 1316, 1320, 1324
POSITION, SPAWN_CANDIDATE, SPAWN_POSITION = 1344, 1360, 1376
ACTION, PRESET, MODEL = 1408, 1664, 1728
FOLLOW_TIMER, HEAL_VALUE, HEAL_MAX = 1760, 1768, 1772
HOLO_ACTIVATION = 1776
SPAWN_DISTANCE = 1780
HOLO_NAME, HOLO_WEAPON, EFFECT_VTABLE, HOLO_COLOUR = 1792, 1824, 1856, 1872
SAFE_INT_GET, SAFE_INT_SET, SAFE_SUBTRACT = 0x7B70, 0x14350, 0x18BF00
UPDATE_STATE = 0x186E70
CREATE, DESTROY, FIND, GET_POSITION = 0x1A90B0, 0x1A8FF0, 0x1A9010, 0x1A8D50
SET_TEAM, SET_MODEL, INNATE_ON, INNATE_OFF = 0x1A9FC0, 0x1A9600, 0x1AB0A0, 0x1AB070
ACTION_GOTO, ACTION_RESET, HOME, AGGRESSION = 0x1A8B60, 0x1A8B30, 0x1AB0D0, 0x1AB120
DELETE_PENDING, SAVE_OBJECTS, OBJECT_LIST = 0x31FCC0, 0xFE4A0, 0x4578AC
DEFENSE_HEALTH, IS_ENEMY = 0x10FDD0, 0x159730
FIND_TELEPORT, SET_COLLISION_GROUP = 0x359810, 0x124B60
HOLO_COLLISION_GROUP = 4  # Terrain and bullets, never soldiers/vehicles.
PHYS_DONT_SAVE = 0x4000  # PhysClass::DONT_SAVE, independent of GameObjManager.
DEFENSE_DAMAGE, WIDE_ASSIGN = 0x108770, 0x9430
CLEAR_WEAPONS, ADD_WEAPON, SELECT_WEAPON = 0x1AC920, 0x179720, 0x1A9350
SPAWN_EFFECT, EFFECT_ADD = 0x1A5580, 0x2F52C0
EFFECT_RENDER, EMISSIVE = 0x35FD00, 0x289C30
NATIVE_EFFECT_VTABLE = (0x35FAD0, 0x35FB90, EFFECT_RENDER, 0x35FDF0)
CALLS = ((0x187845,UPDATE_STATE,'weapon'), (0x1841CA,SAFE_SUBTRACT,'ammo'),
         (0x186F6B,SAFE_INT_GET,'loaded'), (0x187298,r.FORCE_RELOAD,'reload'),
         (0x1A7D82,SAVE_OBJECTS,'save'), (0x1594E4,DEFENSE_HEALTH,'damage'),
         (0x15947C,DEFENSE_DAMAGE,'holo_damage'), (0x130C36,WIDE_ASSIGN,'holo_name'))
ANCHORS = {
    # Hologram-only HUD name, damage interception, loadout and material ABI.
    0x108770: bytes.fromhex('a1c86d850053555683f803578be9750da03c5a82'),
    0x9430: bytes.fromhex('53568b74240c8bd985f67444555756e8213e3a00'),
    0x1AC920: bytes.fromhex('568b74240885f6741d8b068bceff502c85c07412'),
    0x179720: bytes.fromhex('8b44240c8b542404568bf18b4c240c505152e8c9a8030083c4048bce50e81efeffff5ec20c00'),
    0x1A9350: bytes.fromhex('8b4c240485c974278b01ff502485c0741e8b108b'),
    0x1A5580: bytes.fromhex('56576a58e887bcf4ff83c40485c0740b8bc8e839a31b008bf0eb0233f6c7461400'),
    0x2F52C0: bytes.fromhex('8a4424085333db558b6c240c573ac38bf974138b'),
    0x35FD00: bytes.fromhex('83ec4c568bf1578b7c24588a464284c00f84ba000000d946448b4e548d442408d9542408d954240cd95c24108b491450e8cb9ef2ff'),
    0x289C30: bytes.fromhex('8b41088b542404c6413c018950308b41088b5424088950348b41088b4c240c894838c20c0090'),
    0x4083D0: bytes.fromhex('d0fa750090fb750000fd7500f0fd7500'),
    0x130BBA: bytes.fromhex('8b4d94e8fe8602008b7870'),
    0x130C30: bytes.fromhex('8b168d4dc052e8f587edff'),
    0x159467: bytes.fromhex('8b4424248b4c24208b6c241c50d95c241451558bcbe8eff2faffddd88bcbe8'),
    0x120A11: bytes.fromhex('6a01568d4b54e8a4481d003c017503ff460c85f674108b460c8d4e084889410475048b11ff12'),

    0x7B70: bytes.fromhex('8b4424045650e8756d050033f683c4043bc67504'),
    0x14350: bytes.fromhex('8b44240450e896a5040083c40485c0750332c0c3'),
    0x18BF00: bytes.fromhex('83ec145355568bf157897424208b0683f8ff0f84'),
    0x186E70: bytes.fromhex('d9442404d81dbcd77e0083ec4c5355568bf1dfe0'),
    0x1A90B0: bytes.fromhex('8b4c240483ec3085c9750633c083c430c38b4424'),
    0x1A8FF0: bytes.fromhex('8b44240485c074098d48088b4008ff602cc39090'),
    0x1A9010: bytes.fromhex('8b44240450e8765af5ff83c404c390908b442404'),
    0x1A8D50: bytes.fromhex('8b4c240833d283ec0c3bca75108b442410891089'),
    0x1A9FC0: bytes.fromhex('8b4c240485c974158b01ff502485c0740c8b4c24'),
    0x1A9600: bytes.fromhex('56578b7c240c85ff0f84b20000008b078bcfff50'),
    0x1AB0A0: bytes.fromhex('8b4c240485c9741e8b01ff502485c074158b108b'),
    0x1AB070: bytes.fromhex('8b4c240485c9741e8b01ff502485c074158b108b'),
    0x1A8B60: bytes.fromhex('8b4c240485c974198b01ff502c85c074108b4c24'),
    0x1A8B30: bytes.fromhex('8b4c240485c974198b01ff502c85c074108b4c24'),
    0x1AB0D0: bytes.fromhex('8b4c240485c9743b8b01ff502c85c074328b108b'),
    0x1AB120: bytes.fromhex('8b4c240485c974268b01ff502c85c0741d8b108b'),
    0x31FCC0: bytes.fromhex('a078508a0084c0753ea168508a005633f685c07e'),
    0xFE4A0: bytes.fromhex('5153568b74241068a52ea8368bcee8dd641e008b'),
    0xFE4B3: bytes.fromhex('8b1dac78850085db7438578b7b04'),
    DEFENSE_HEALTH: bytes.fromhex('8b410483ec0883f8ff56570f84b4020000'),
    IS_ENEMY: bytes.fromhex('568bf18b4c2408573bce743f8b01ff5068'),
    # DamageableGameObj::Apply_Damage: diff is [caller ESP+0x24], victim ESI,
    # offense EBP. This call occurs once, outside the observer notification loop.
    0x1594A1: bytes.fromhex('8b86f806000033ffd86c242485c0d95c2424'),
    0x1594E2: bytes.fromhex('8bcbe8e768fbffd81dbcd77e00'),
    FIND_TELEPORT: bytes.fromhex('558bec81ecbc00000056578b7d088bf18d4db0'),
    SET_COLLISION_GROUP: bytes.fromhex('8b81740700008a4c240483e10f8b503883e2f00bca894838c20400'),
}


def call_bytes(address, target):
    return b'\xe8'+u32(target-address-5)


def owned_weapon(c,control,service,reject,source=b'\x8b\x5c\x24\x18'):
    """Return Havoc's weapon EBX; source is evaluated after pushad."""
    t.guard(c,control,service,reject)
    c.emit(source+b'\x85\xdb');c.jump(b'\x0f\x84',reject)
    c.emit(b'\x8b\x43\x0c\x85\xc0');c.jump(b'\x0f\x84',reject)
    c.emit(b'\x3b\x70\x04');c.jump(b'\x0f\x85',reject)
    c.emit(b'\x80\x7b\x38\x00');c.jump(b'\x0f\x84',reject)


def bullet_firearm(c,control,reject):
    t.deadline(c,control,t.BULLET_END,reject)
    c.emit(b'\x83\xbe'+u32(t.HUMAN_STATE)+b'\x09');c.jump(b'\x0f\x84',reject)
    c.emit(b'\x8b\x03\x85\xc0');c.jump(b'\x0f\x84',reject)
    c.emit(b'\x8b\x40\x1c\x83\xf8\x02');c.jump(b'\x0f\x82',reject)
    c.emit(b'\x83\xf8\x05');c.jump(b'\x0f\x87',reject)


def firearm(c,control,service,reject,source=b'\x8b\x5c\x24\x18'):
    owned_weapon(c,control,service,reject,source)
    bullet_firearm(c,control,reject)


def build_weapon(address,control,service):
    c=Code(address);c.emit(b'\x9c\x60\xfc')
    owned_weapon(c,control,service,'normal')
    c.emit(b'\x83\x3d'+u32(control+t.RELOADS)+b'\x00');c.jump(b'\x0f\x84','bullet')
    # Input reasserts these every frame. Clear both triggers immediately before
    # the native state machine, including gaps and the tail of the fifth reload.
    # Keep the state machine running so reloads finish at their ordinary speed.
    c.emit(b'\x66\xc7\x43\x47\x00\x00');c.jump(b'\xe9','normal')
    c.label('bullet');bullet_firearm(c,control,'normal')
    # Reset the instance burst counter, not the shared ammo definition. Holding
    # the trigger can fire continuously; no ammunition is created or consumed.
    c.emit(b'\x6a\xff\x89\xe0\x50\xff\x73\x30');c.jump(b'\xe8',BASE+SAFE_INT_SET)
    c.emit(b'\x83\xc4\x0c')
    # Accelerate charge/fire time only, preserving reload/switch delays.
    c.emit(b'\x83\x7b\x3c\x04');c.jump(b'\x0f\x87','normal')
    c.emit(b'\xd9\x44\x24\x28\xd8\xc0\xd9\x5c\x24\x28')
    c.label('normal');c.emit(b'\x61\x9d');c.jump(b'\xe9',BASE+UPDATE_STATE)
    return c.finish()


def build_ammo(address,control,service):
    c=Code(address);c.emit(b'\x9c\x60\xfc')
    firearm(c,control,service,'normal',b'\x8b\x5c\x24\x18\x83\xeb\x24')
    c.emit(b'\x61\x9d\xc2\x04\x00')  # Skip this shot's protected-clip subtraction.
    c.label('normal');c.emit(b'\x61\x9d');c.jump(b'\xe9',BASE+SAFE_SUBTRACT)
    return c.finish()


def build_loaded(address,control,service):
    c=Code(address);c.emit(b'\x9c\x60\xfc')
    firearm(c,control,service,'normal',b'\x8b\x5c\x24\x04')  # Original ESI is weapon.
    c.emit(b'\x8b\x44\x24\x2c\xc7\x00'+u32(control+ONE))
    c.emit(b'\x61\x9d\xb8\x01\x00\x00\x00\xc3')  # cdecl lookup(out pointer).
    c.label('normal');c.emit(b'\x61\x9d');c.jump(b'\xe9',BASE+SAFE_INT_GET)
    return c.finish()


def build_reload(address,control,service):
    c=Code(address);c.emit(b'\x9c\x60\xfc')
    firearm(c,control,service,'normal')
    c.emit(b'\x61\x9d\xc3')  # Suppress only the automatic empty-clip reload call.
    c.label('normal');c.emit(b'\x61\x9d');c.jump(b'\xe9',BASE+r.FORCE_RELOAD)
    return c.finish()


def build_damage(address,control,service):
    """Observe actual positive enemy damage once; preserve the native health read."""
    c=Code(address);c.emit(b'\x9c\x60\xfc')
    t.guard(c,control,service,'normal')
    t.deadline(c,control,t.VAMPIRE_END,'normal')
    # Caller has finished damage and all Damaged observers; no callbacks are
    # replaced and no queued hit can leak into a later activation or save load.
    c.emit(b'\x8b\x44\x24\x4c\x85\xc0');c.jump(b'\x0f\x8e','normal')
    c.emit(b'\x3d'+u32(0x7f800000));c.jump(b'\x0f\x83','normal')
    c.emit(b'\x8b\x44\x24\x08\x85\xc0');c.jump(b'\x0f\x84','normal')
    c.emit(b'\x8b\x40\x14\x85\xc0');c.jump(b'\x0f\x84','normal')
    c.emit(b'\x3b\x70\x04');c.jump(b'\x0f\x85','normal')  # Attacker is current Havoc.
    c.emit(b'\x8b\x5c\x24\x04\x85\xdb');c.jump(b'\x0f\x84','normal')
    c.emit(b'\x53\x89\xf1');c.jump(b'\xe8',BASE+IS_ENEMY)
    c.emit(b'\x84\xc0');c.jump(b'\x0f\x84','normal')
    for getter,maximum,setter,label in ((r.GET_HEALTH,r.GET_MAX_HEALTH,r.SET_HEALTH,'health'),
                                      (r.GET_ARMOUR,r.GET_MAX_ARMOUR,r.SET_ARMOUR,'armour')):
        c.emit(b'\x56');c.jump(b'\xe8',BASE+getter);c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(control+HEAL_VALUE))
        if label=='health':
            c.emit(b'\x83\x3d'+u32(control+HEAL_VALUE)+b'\x00');c.jump(b'\x0f\x8e','normal')
        c.emit(b'\x56');c.jump(b'\xe8',BASE+maximum);c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(control+HEAL_MAX))
        c.emit(b'\xd9\x05'+u32(control+r.TEN)+b'\xd8\x05'+u32(control+HEAL_VALUE)+b'\xd9\x1d'+u32(control+HEAL_VALUE))
        c.emit(b'\xa1'+u32(control+HEAL_VALUE)+b'\x3b\x05'+u32(control+HEAL_MAX));c.jump(b'\x0f\x86',label)
        c.emit(b'\xa1'+u32(control+HEAL_MAX))
        c.label(label);c.emit(b'\x50\x56');c.jump(b'\xe8',BASE+setter);c.emit(b'\x83\xc4\x08')
    c.label('normal');c.emit(b'\x61\x9d');c.jump(b'\xe9',BASE+DEFENSE_HEALTH)
    return c.finish()


def hologram_identity(c,control,service,reject):
    """EAX is a live actor supplied by the native caller, never a cached pointer.

    Retain protection/name during pause and the frame that performs cleanup.
    A loaded save or reused object address must not inherit either property.
    """
    c.emit(b'\x85\xc0');c.jump(b'\x0f\x84',reject)
    c.emit(b'\x3b\x05'+u32(control+HOLO_PTR));c.jump(b'\x0f\x85',reject)
    c.emit(b'\x8b\x50\x0c\x3b\x15'+u32(control+HOLO_ID));c.jump(b'\x0f\x85',reject)
    c.emit(b'\x8b\x15'+u32(service+s.GENERATION)+b'\x3b\x15'+u32(control+HOLO_GENERATION));c.jump(b'\x0f\x85',reject)
    c.emit(b'\x8b\x15'+u32(service+s.OBSERVER)+b'\x85\xd2');c.jump(b'\x0f\x84',reject)
    c.emit(b'\x3b\x15'+u32(BASE+s.STEAM.commands+0x204));c.jump(b'\x0f\x85',reject)


def build_holo_damage(address,control,service):
    """Skip defense damage only for our ally; preserve native float/ret-12 ABI."""
    c=Code(address);c.emit(b'\x9c\x60\xfc\x8d\x81'+u32(-0x72c))
    hologram_identity(c,control,service,'normal')
    c.emit(b'\x61\x9d');c.jump(b'\xe8',BASE+DEFENSE_HEALTH)
    c.emit(b'\xc2\x0c\x00')
    c.label('normal');c.emit(b'\x61\x9d');c.jump(b'\xe9',BASE+DEFENSE_DAMAGE)
    return c.finish()


def build_holo_name(address,control,service):
    """Override this HUD target's temporary string, never the shared definition."""
    c=Code(address);c.emit(b'\x9c\x60\xfc\x8b\x45\x94')  # HUD's obj local.
    hologram_identity(c,control,service,'normal')
    c.emit(b'\xc7\x44\x24\x28'+u32(control+HOLO_NAME))
    c.label('normal');c.emit(b'\x61\x9d');c.jump(b'\xe9',BASE+WIDE_ASSIGN)
    return c.finish()


def build_holo_render(address,control,service):
    """Private effect vtable: native shimmer, then cyan emissive material.

    Render_Push queues a material pass; the renderer consumes it after return.
    Each effect owns its material, so this cannot tint other actors/effects.
    Both native render/pop functions and native reference-counted destruction
    remain paired. No smart-object stealth flag or shared shader is changed.
    """
    c=Code(address);c.emit(b'\x56\x89\xce\xff\x74\x24\x0c\xff\x74\x24\x0c')
    c.jump(b'\xe8',BASE+EFFECT_RENDER)
    for offset in (8,4,0):
        c.emit(b'\x83\xec\x04\xd9\x46\x44\xd8\x0d'+u32(control+HOLO_COLOUR+offset)+b'\xd9\x1c\x24')
    c.emit(b'\x8b\x4e\x54\x8b\x49\x14');c.jump(b'\xe8',BASE+EMISSIVE)
    c.emit(b'\x5e\xc2\x08\x00')
    return c.finish()


def initialise(memory,control,render_target,*,effect_vtable=None):
    memory.write(control+HALF,struct.pack('<fI',.5,1))
    memory.write(control+PRESET,b'GDI_MiniGunner_0\0')
    memory.write(control+MODEL,b'c_ag_havoc\0')
    memory.write(control+SPAWN_DISTANCE,struct.pack('<f',2.5))
    memory.write(control+HOLO_NAME,'Hologram Havoc\0'.encode('utf-16le'))
    memory.write(control+HOLO_WEAPON,b'Weapon_VoltAutoRifle_Ai\0')
    vtable=list(effect_vtable or [BASE+rva for rva in NATIVE_EFFECT_VTABLE]);vtable[2]=render_target
    memory.write(control+EFFECT_VTABLE,struct.pack('<4I',*vtable))
    memory.write(control+HOLO_COLOUR,struct.pack('<3f',.12,.8,1.0))
    params=bytearray(196)
    def i(off,val):struct.pack_into('<I',params,off,val & 0xffffffff)
    def f(off,val):struct.pack_into('<f',params,off,val)
    i(0,80);i(4,0x415048);f(60,1);f(64,4)
    params[69]=1;params[71]=1  # Follow Havoc, use pathfinding.
    f(76,20);params[92]=1;params[108]=1;params[109]=1;params[111]=1
    f(128,2);i(168,-1)
    memory.write(control+ACTION,bytes(params))


def build_save(address,control,service,*,commands=BASE+s.STEAM.commands,
               object_list=BASE+OBJECT_LIST,save_objects=BASE+SAVE_OBJECTS,counter=None):
    """Exclude the temporary ally from the synchronous object-save traversal.

    GameObjManager::Save only walks the linked list; it never mutates it. Restore
    the exact link immediately after that call, including a false return value.
    """
    c=Code(address)
    c.emit(b'\x55\x89\xe5\x83\xec\x08\xc7\x45\xfc'+u32(0)+b'\x9c\x60\xfc')
    c.emit(b'\x8b\x1d'+u32(control+HOLO_PTR)+b'\x85\xdb');c.jump(b'\x0f\x84','save')
    c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\x3b\x05'+u32(control+HOLO_GENERATION));c.jump(b'\x0f\x85','save')
    c.emit(b'\xa1'+u32(service+s.OBSERVER)+b'\x3b\x05'+u32(commands+0x204));c.jump(b'\x0f\x85','save')
    c.emit(b'\xba'+u32(object_list)+b'\xb9'+u32(65536))
    c.label('scan');c.emit(b'\x8b\x02\x85\xc0');c.jump(b'\x0f\x84','save')
    c.emit(b'\x3b\x58\x04');c.jump(b'\x0f\x84','unlink')
    c.emit(b'\x89\xc2\x49');c.jump(b'\x0f\x85','scan');c.jump(b'\xe9','save')
    c.label('unlink')
    c.emit(b'\x8b\x4b\x0c\x3b\x0d'+u32(control+HOLO_ID));c.jump(b'\x0f\x85','save')
    c.emit(b'\x89\x55\xfc\x89\x45\xf8\x8b\x08\x89\x0a')
    if counter is not None:c.emit(b'\xff\x05'+u32(counter))
    c.label('save');c.emit(b'\x61\x9d\xff\x75\x08');c.jump(b'\xe8',save_objects)
    c.emit(b'\x83\xc4\x04\x9c\x60\x8b\x55\xfc\x85\xd2');c.jump(b'\x0f\x84','restored')
    c.emit(b'\x8b\x45\xf8\x89\x02')
    c.label('restored');c.emit(b'\x61\x9d\x89\xec\x5d\xc3')
    return c.finish()


def build_update(address,control,service,*,bindings=None,guard_builder=t.guard,
                 tick_iat=BASE+s.STEAM.tick_iat,pending_continue=BASE+s.STEAM.pending_continue,
                 frame_seconds=BASE+t.FRAME_SECONDS,registers=None):
    c=Code(address)
    def store(off,value=0):c.emit(b'\xc7\x05'+u32(control+off)+u32(value))
    def native(rva,args=b'',size=0):
        c.emit(args);c.jump(b'\xe8',(bindings or {}).get(rva,BASE+rva))
        if size:c.emit(b'\x83\xc4'+bytes([size]))
    def get_position():native(GET_POSITION,b'\x56\x68'+u32(control+POSITION),8)
    if registers:registers[0](c)
    else:c.emit(b'\x9c\x60\xfc')
    # Resolve an ID every time before using the ally, and verify pointer and load.
    c.emit(b'\x31\xff\x83\x3d'+u32(control+HOLO_PTR)+b'\x00');c.jump(b'\x0f\x84','resolved')
    c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\x3b\x05'+u32(control+HOLO_GENERATION));c.jump(b'\x0f\x85','forget')
    native(FIND,b'\xff\x35'+u32(control+HOLO_ID),4)
    c.emit(b'\x85\xc0');c.jump(b'\x0f\x84','forget')
    c.emit(b'\x3b\x05'+u32(control+HOLO_PTR));c.jump(b'\x0f\x85','forget')
    c.emit(b'\x8b\x50\x0c\x3b\x15'+u32(control+HOLO_ID));c.jump(b'\x0f\x85','forget')
    c.emit(b'\x89\xc7');c.jump(b'\xe9','resolved')
    c.label('forget');store(HOLO_PTR);store(HOLO_ID);store(t.HOLOGRAM_END)
    c.label('resolved')
    c.emit(b'\x83\x3d'+u32(control+CLEANUP)+b'\x00');c.jump(b'\x0f\x85','destroy')
    c.emit(b'\x83\x3d'+u32(service+s.ENABLED)+b'\x01');c.jump(b'\x0f\x85','destroy')
    c.emit(b'\xff\x15'+u32(tick_iat)+b'\x2b\x05'+u32(service+s.HEARTBEAT)+b'\x3d'+u32(t.LEASE_MS))
    c.jump(b'\x0f\x87','destroy')
    # Load/death/victory ends active effects, but pause/cinematic only freezes them.
    for off in (s.ALIVE,):
        c.emit(b'\x83\x3d'+u32(service+off)+b'\x01');c.jump(b'\x0f\x85','destroy')
    c.emit(b'\x80\x3d'+u32(pending_continue)+b'\x00');c.jump(b'\x0f\x85','destroy')
    c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\x3b\x05'+u32(control+t.GENERATION));c.jump(b'\x0f\x85','destroy')
    guard_builder(c,control,service,'pause')
    t.deadline(c,control,t.HOLOGRAM_END,'destroy_holo')
    c.emit(b'\x85\xff');c.jump(b'\x0f\x85','follow')
    get_position()
    # Find standing room beside Havoc before creating anything. Use his human
    # collision hull with its ordinary group; search only within 0.5m of each
    # candidate 2.5m away, so a fallback cannot put the ally back inside him.
    for axis,subtract in ((0,False),(0,True),(4,False),(4,True)):
        for off in (0,4,8):c.emit(b'\xa1'+u32(control+POSITION+off)+b'\xa3'+u32(control+SPAWN_CANDIDATE+off))
        c.emit(b'\xd9\x05'+u32(control+POSITION+axis)+(b'\xd8\x25' if subtract else b'\xd8\x05')+u32(control+SPAWN_DISTANCE)+b'\xd9\x1d'+u32(control+SPAWN_CANDIDATE+axis))
        c.emit(b'\x8b\x8e'+u32(t.PLAYER_PHYSICS)+b'\x85\xc9');c.jump(b'\x0f\x84','spawn_failed')
        native(FIND_TELEPORT,b'\x68'+u32(control+SPAWN_POSITION)+b'\x68'+struct.pack('<f',.5)+b'\x68'+u32(control+SPAWN_CANDIDATE))
        c.emit(b'\x84\xc0');c.jump(b'\x0f\x85','spawn_position_found')
    c.jump(b'\xe9','spawn_failed')
    c.label('spawn_position_found')
    native(CREATE,b'\x68'+u32(control+SPAWN_POSITION)+b'\x68'+u32(control+PRESET),8)
    c.emit(b'\x85\xc0');c.jump(b'\x0f\x84','spawn_failed')
    c.emit(b'\x89\xc7\xa3'+u32(control+HOLO_PTR)+b'\x8b\x40\x0c\xa3'+u32(control+HOLO_ID))
    c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\xa3'+u32(control+HOLO_GENERATION))
    native(SET_TEAM,b'\x6a\x01\x57',8)
    native(SET_MODEL,b'\x68'+u32(control+MODEL)+b'\x57',8)
    # PhysicsScene serializes dynamic bodies separately from GameObjManager.
    # Excluding only the actor leaves an orphan HumanPhys with no controller on
    # reload. Mark this private body transient before any checkpoint can run.
    c.emit(b'\x8b\x87'+u32(t.PLAYER_PHYSICS)+b'\x85\xc0');c.jump(b'\x0f\x84','setup_failed')
    c.emit(b'\x81\x48\x38'+u32(PHYS_DONT_SAVE))
    # Give the native AI variant directly, with unlimited reserve ammunition.
    # Clearing this instance's bag prevents AI from selecting its old rifle.
    native(CLEAR_WEAPONS,b'\x57',4)
    c.emit(b'\x8b\x8f\xb0\x07\x00\x00\x85\xc9');c.jump(b'\x0f\x84','setup_failed')
    native(ADD_WEAPON,b'\x6a\x01\x6a\xff\x68'+u32(control+HOLO_WEAPON))
    c.emit(b'\x85\xc0');c.jump(b'\x0f\x84','setup_failed')
    native(SELECT_WEAPON,b'\x68'+u32(control+HOLO_WEAPON)+b'\x57',8)
    # Hold a native spawn shimmer at its translucent phase. The physics effect
    # list owns the sole remaining reference and releases it with the actor.
    native(SPAWN_EFFECT)
    c.emit(b'\x85\xc0');c.jump(b'\x0f\x84','setup_failed')
    c.emit(b'\x89\xc3\xc7\x03'+u32(control+EFFECT_VTABLE))
    c.emit(b'\x66\xc7\x43\x10\x00\x01')  # persistent, suppress shadows
    for off,value in ((0x14,.65),(0x18,.65),(0x1c,0),(0x38,1),(0x3c,0),(0x44,.7)):
        c.emit(b'\xc7\x43'+bytes([off])+struct.pack('<f',value))
    c.emit(b'\xc7\x43\x40\x00\x00\x01\x00')  # no expiry; shimmer only, no base skin
    c.emit(b'\x8b\x8f'+u32(t.PLAYER_PHYSICS)+b'\x83\xc1\x54')
    native(EFFECT_ADD,b'\x6a\x01\x53')
    c.emit(b'\x84\xc0');c.jump(b'\x0f\x84','effect_failed')
    # Transfer the constructor reference to the list: native Add itself is
    # non-owning; native RefMultiList::Add would add one and Release_Ref one.
    native(AGGRESSION,b'\x68'+struct.pack('<f',.8)+b'\x57',8)
    store(FOLLOW_TIMER)
    c.label('follow')
    c.emit(b'\x89\xf9\x6a'+bytes([HOLO_COLLISION_GROUP]));native(SET_COLLISION_GROUP)
    c.emit(b'\x83\x3d'+u32(control+HOLO_ACTIVATION)+b'\x01');c.jump(b'\x0f\x85','acknowledged')
    store(HOLO_ACTIVATION);store(r.RESULT,1)
    c.label('acknowledged')
    c.emit(b'\x83\x3d'+u32(control+HOLO_PAUSED)+b'\x00');c.jump(b'\x0f\x84','follow_timer')
    native(INNATE_ON,b'\x57',4);store(HOLO_PAUSED);store(FOLLOW_TIMER)
    c.label('follow_timer')
    c.emit(b'\xd9\x05'+u32(control+FOLLOW_TIMER)+b'\xd8\x25'+u32(frame_seconds)+b'\xd9\x1d'+u32(control+FOLLOW_TIMER))
    c.emit(b'\x83\x3d'+u32(control+FOLLOW_TIMER)+b'\x00');c.jump(b'\x0f\x8f','done')
    store(FOLLOW_TIMER,0x3f000000)
    get_position()
    native(HOME,b'\x68'+struct.pack('<f',12)+b'\x68'+u32(control+POSITION)+b'\x57',12)
    c.emit(b'\x89\x35'+u32(control+ACTION+44))
    native(ACTION_GOTO,b'\x68'+u32(control+ACTION)+b'\x57',8)
    c.jump(b'\xe9','done')
    c.label('effect_failed')
    c.emit(b'\x8d\x4b\x08\x8b\x01\xff\x49\x04');c.jump(b'\x0f\x85','setup_failed')
    c.emit(b'\xff\x10')  # Release the unattached effect through native destructor.
    c.label('setup_failed');store(t.HOLOGRAM_END);c.jump(b'\xe9','destroy_holo')
    c.label('spawn_failed');store(t.HOLOGRAM_END)
    # Refund activation when this map cannot create the preset.
    store(HOLO_ACTIVATION);store(r.RESULT,2);c.jump(b'\xe9','done')
    c.label('pause')
    c.emit(b'\x85\xff');c.jump(b'\x0f\x84','done')
    c.emit(b'\x83\x3d'+u32(control+HOLO_PAUSED)+b'\x00');c.jump(b'\x0f\x85','done')
    native(INNATE_OFF,b'\x57',4)
    native(ACTION_RESET,b'\x68'+struct.pack('<f',10000)+b'\x57',8)
    store(HOLO_PAUSED,1);c.jump(b'\xe9','done')
    c.label('destroy')
    for offset in (*t.TIMERS,t.RELOADS,t.RELOAD_WEAPON):store(offset)
    c.label('destroy_holo')
    c.emit(b'\x85\xff');c.jump(b'\x0f\x84','cleared')
    native(DESTROY,b'\x57',4)
    # This helper runs before the normal game-object iteration, including while
    # paused. Complete native queued deletion now so disconnect/save is clean.
    native(DELETE_PENDING)
    c.label('cleared');store(HOLO_PTR);store(HOLO_ID);store(HOLO_PAUSED)
    c.emit(b'\x83\x3d'+u32(control+HOLO_ACTIVATION)+b'\x01');c.jump(b'\x0f\x85','done')
    store(HOLO_ACTIVATION);store(r.RESULT,2)
    c.label('done')
    if registers:registers[1](c)
    else:c.emit(b'\x61\x9d')
    c.emit(b'\xc3')
    return c.finish()


class NativeBonuses:
    def __init__(self,memory,control,service):
        self.memory,self.control,self.service=memory,control,service
        self.entries=[]
        for rva,expected in ANCHORS.items():
            if memory.read(BASE+rva,len(expected))!=expected:
                raise UnsupportedBuild(f'Helpful item anchor differs at {rva:#x}')
        render_code=memory.allocate()+CODE_OFFSET
        program=build_holo_render(render_code,control,service)
        memory.write(render_code,program)
        if memory.read(render_code,len(program))!=program:raise MemoryError('Hologram render wrapper read-back failed')
        memory.make_executable(render_code)
        initialise(memory,control,render_code)
        builders={'weapon':build_weapon,'ammo':build_ammo,'loaded':build_loaded,'reload':build_reload,
                  'save':build_save,'damage':build_damage,'holo_damage':build_holo_damage,'holo_name':build_holo_name}
        for rva,original,name in CALLS:
            if memory.read(BASE+rva,5)!=call_bytes(BASE+rva,BASE+original):
                raise UnsupportedBuild(f'Helpful call site differs at {rva:#x}')
            code=memory.allocate()+CODE_OFFSET
            program=builders[name](code,control,service)
            memory.write(code,program)
            if memory.read(code,len(program))!=program:raise MemoryError('Helpful wrapper read-back failed')
            memory.make_executable(code)
            self.entries.append((BASE+rva,call_bytes(BASE+rva,BASE+original),call_bytes(BASE+rva,code)))
        self.update_code=memory.allocate()+CODE_OFFSET
        program=build_update(self.update_code,control,service)
        if len(program)>4096:raise ValueError('Helpful service exceeds code page')
        memory.write(self.update_code,program)
        if memory.read(self.update_code,len(program))!=program:raise MemoryError('Helpful service read-back failed')
        memory.make_executable(self.update_code)

    def update(self):
        changes=[]
        for address,original,code in self.entries:
            current=self.memory.read(address,5)
            if current==code:continue
            if current!=original:raise MemoryError('Another hook owns a helpful-item callback')
            changes.append((address,original,code))
        if changes:self.memory.replace_calls(changes)

    def close(self):
        self.memory.write_u32(self.control+CLEANUP,1)
        until=time.monotonic()+2
        while self.memory.u32(self.control+HOLO_PTR) and time.monotonic()<until:
            time.sleep(.02)
        if self.memory.u32(self.control+HOLO_PTR):
            raise MemoryError('Bodyguard cleanup has not reached a game frame; resume Renegade before reconnecting')
        changes=[]
        for address,original,code in self.entries:
            current=self.memory.read(address,5)
            if current==code:changes.append((address,code,original))
        if changes:self.memory.replace_calls(changes)
        # Retain allocations until process exit for in-flight callbacks.

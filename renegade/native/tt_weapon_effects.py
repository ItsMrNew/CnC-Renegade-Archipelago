"""TT b9000 Butterfingers/Bullet Hose hooks on the active BandTest weapon code.

Update_State takes delta in XMM1, not a stack argument. SafeData lookup/write
also use TT-specific ABIs. Preserve SSE inputs across our guard's Win32 calls.
Only per-instance burst state is written; ammo/weapon definitions stay intact.
"""
from . import traps as t, services as s
from .bonuses import call_bytes, bullet_firearm
from .tt_traps import guarded
from .thunk import Code, CODE_OFFSET, u32
from .profile import UnsupportedBuild

UPDATE, LOOKUP, ASSIGN, DECREMENT = 0x282BE0, 0x2F20, 0x31C0, 0x285D10
FORCE_RELOAD = 0x2822F0
FORCE_PREFIX = bytes.fromhex('558bec83e4f883ec3453568bf157')
ONE = 1304
UPDATES, BLOCKED, RATE, LOADED, AMMO, RELOAD_STARTS = range(2020,2044,4) # Diagnostic-only counters.
CALLS = ((0x282A6D,UPDATE,'weapon'),(0x282CE9,LOOKUP,'loaded'),
         (0x282EB7,LOOKUP,'loaded'),(0x283DF2,DECREMENT,'ammo'))


def expected_anchors(a):
    b=a.band_base
    return {
        b+0x282A62:b'\xa1'+u32(b+0x5FA670)+bytes.fromhex('8bcef30f1008'),
        b+UPDATE:bytes.fromhex('83ec3c5355560f28d10f2f15')+u32(b+0x2F1AF4)+bytes.fromhex('8bf157f30f114c2410c6465100'),
        b+0x282CE2:bytes.fromhex('ff76248d4c2418'),
        b+0x282CEE:bytes.fromhex('8b44241883c40485c00f84920100008338000f95'),
        b+0x282EB0:bytes.fromhex('ff76248d4c2418'),
        b+0x282EBC:bytes.fromhex('8b44241883c40485c074408338000f95c084c075'),
        b+0x283DEA:bytes.fromhex('ffb6880000008bcf'),
        b+DECREMENT:bytes.fromhex('51568bf1c744240400000000837e24ff7425'),
        b+FORCE_RELOAD:FORCE_PREFIX,
        b+0x282331:bytes.fromhex('837e3c010f8fc30100008bcec7463c05000000e857f2ffff'),
        b+LOOKUP:bytes.fromhex('5156ff74240c8bf1e8539316008bd083c40485d2750532c05e59c3'),
        b+ASSIGN:bytes.fromhex('515357ff7424108bd9e8b29016008bf883c40485ff75065f32c05b59c3'),
    }


def save(c):
    c.emit(b'\x9c\x81\xec'+u32(128))
    for i in range(8):c.emit(b'\x0f\x11'+bytes([0x44+8*i,0x24,16*i]))
    c.emit(b'\x60\xfc')


def restore(c):
    c.emit(b'\x61')
    for i in range(8):c.emit(b'\x0f\x10'+bytes([0x44+8*i,0x24,16*i]))
    c.emit(b'\x81\xc4'+u32(128)+b'\x9d')


def owned(c,control,service,a,reject,source=b'\x8b\x5c\x24\x18'):
    guarded(a)(c,control,service,reject)
    c.emit(source+b'\x85\xdb');c.jump(b'\x0f\x84',reject)
    c.emit(b'\x8b\x43\x0c\x85\xc0');c.jump(b'\x0f\x84',reject)
    c.emit(b'\x3b\x70\x04');c.jump(b'\x0f\x85',reject)
    c.emit(b'\x80\x7b\x38\x00');c.jump(b'\x0f\x84',reject)


def build_weapon(address,control,service,a,*,testing=False):
    c=Code(address);save(c)
    owned(c,control,service,a,'normal')
    if testing:c.emit(b'\xff\x05'+u32(control+UPDATES))
    c.emit(b'\x83\x3d'+u32(control+t.RELOADS)+b'\x00');c.jump(b'\x0f\x84','bullet')
    # Block both triggers even before/after a reload, including placeable items.
    c.emit(b'\x66\xc7\x43\x47\x00\x00')
    if testing:c.emit(b'\xff\x05'+u32(control+BLOCKED))
    c.jump(b'\xe9','normal')
    c.label('bullet');bullet_firearm(c,control,'normal')
    c.emit(b'\x6a\xff\x89\xe1\xff\x73\x30');c.jump(b'\xe8',a.band_base+ASSIGN)
    c.emit(b'\x83\xc4\x08')
    c.emit(b'\x83\x7b\x3c\x04');c.jump(b'\x0f\x87','normal')
    # Saved XMM1 starts 32 (pushad) + 16 bytes into the SSE save area.
    c.emit(b'\xd9\x44\x24\x30\xd8\xc0\xd9\x5c\x24\x30')
    if testing:c.emit(b'\xff\x05'+u32(control+RATE))
    c.label('normal');restore(c);c.jump(b'\xe9',a.band_base+UPDATE)
    return c.finish()


def build_loaded(address,control,service,a,*,testing=False):
    c=Code(address);save(c)
    owned(c,control,service,a,'normal',b'\x8b\x5c\x24\x04') # Original ESI is weapon.
    bullet_firearm(c,control,'normal')
    if testing:c.emit(b'\xff\x05'+u32(control+LOADED))
    c.emit(b'\x8b\x44\x24\x18\xc7\x00'+u32(control+ONE)) # ECX is out pointer.
    restore(c);c.emit(b'\xb8\x01\x00\x00\x00\xc3')
    c.label('normal');restore(c);c.jump(b'\xe9',a.band_base+LOOKUP)
    return c.finish()


def build_ammo(address,control,service,a,*,testing=False):
    c=Code(address);save(c)
    owned(c,control,service,a,'normal');bullet_firearm(c,control,'normal')
    if testing:c.emit(b'\xff\x05'+u32(control+AMMO))
    restore(c);c.emit(b'\xc2\x04\x00')
    c.label('normal');restore(c);c.jump(b'\xe9',a.band_base+DECREMENT)
    return c.finish()


def build_forced_reload(address,a):
    # Enter the verified native reload body after its reserve-ammo check. This
    # preserves its animation, sound and duration even with zero spare rounds.
    # No ammunition is created/deleted just to make the animation start.
    c=Code(address);c.emit(FORCE_PREFIX);c.jump(b'\xe9',a.band_base+0x282331)
    return c.finish()


def build_reloads(address,control,service,a,force,*,testing=False):
    c=Code(address);save(c)
    guarded(a)(c,control,service,'done')
    c.emit(b'\x83\x3d'+u32(control+t.RELOADS)+b'\x00');c.jump(b'\x0f\x84','done')
    c.emit(b'\x8b\x9e'+u32(0x7B0)+b'\x85\xdb');c.jump(b'\x0f\x84','done')
    c.emit(b'\x83\x7b\x14\x40');c.jump(b'\x0f\x87','done')
    c.emit(b'\x8b\x43\x1c\x3b\x43\x14');c.jump(b'\x0f\x83','done')
    c.emit(b'\x8b\x53\x08\x85\xd2');c.jump(b'\x0f\x84','done')
    c.emit(b'\x8b\x1c\x82\x85\xdb');c.jump(b'\x0f\x84','done')
    c.emit(b'\x80\x7b\x38\x00');c.jump(b'\x0f\x84','done')
    c.emit(b'\x3b\x1d'+u32(control+t.RELOAD_WEAPON));c.jump(b'\x0f\x84','same')
    c.emit(b'\xc7\x05'+u32(control+t.RELOAD_WEAPON)+u32(0))
    c.label('same')
    c.emit(b'\x8b\x03\x85\xc0');c.jump(b'\x0f\x84','done')
    c.emit(b'\x8b\x40\x1c\x83\xf8\x02');c.jump(b'\x0f\x82','done')
    c.emit(b'\x83\xf8\x05');c.jump(b'\x0f\x87','done')
    c.emit(b'\x83\xbe'+u32(t.HUMAN_STATE)+b'\x09');c.jump(b'\x0f\x84','done')
    c.emit(b'\x83\x7b\x3c\x01');c.jump(b'\x0f\x87','done')
    c.emit(b'\x83\x3d'+u32(control+t.RELOAD_WEAPON)+b'\x00');c.jump(b'\x0f\x84','start')
    c.emit(b'\xc7\x05'+u32(control+t.RELOAD_WEAPON)+u32(0))
    c.emit(b'\xff\x0d'+u32(control+t.RELOADS));c.jump(b'\x0f\x84','done')
    c.label('start');c.emit(b'\x89\xd9');c.jump(b'\xe8',force)
    if testing:c.emit(b'\xff\x05'+u32(control+RELOAD_STARTS))
    c.emit(b'\x89\x1d'+u32(control+t.RELOAD_WEAPON))
    c.label('done');restore(c);c.emit(b'\xc3')
    return c.finish()


class NativeWeaponEffects:
    def __init__(self,memory,control,service,a,*,testing=False):
        from .tt_inventory import install_code
        self.memory,self.service,self.entries=memory,service,[]
        for address,expected in expected_anchors(a).items():
            if memory.read(address,len(expected))!=expected:
                raise UnsupportedBuild(f'TT weapon-effect anchor differs at {address:#x}')
        for rva,target,name in CALLS:
            site=a.band_base+rva
            original=call_bytes(site,a.band_base+target)
            if memory.read(site,5)!=original:raise UnsupportedBuild('Another hook owns a TT weapon-effect call')
        memory.write_u32(control+ONE,1)
        builders={'weapon':build_weapon,'loaded':build_loaded,'ammo':build_ammo}
        for rva,target,name in CALLS:
            site=a.band_base+rva;code=memory.allocate()+CODE_OFFSET
            install_code(memory,code,builders[name](code,control,service,a,testing=testing))
            self.entries.append((site,call_bytes(site,a.band_base+target),call_bytes(site,code)))
        force=memory.allocate()+CODE_OFFSET
        install_code(memory,force,build_forced_reload(force,a))
        self.code=memory.allocate()+CODE_OFFSET
        install_code(memory,self.code,build_reloads(self.code,control,service,a,force,testing=testing))

    def update(self):
        changes=[]
        for site,original,replacement in self.entries:
            current=self.memory.read(site,5)
            if current==replacement:continue
            if current!=original:raise OSError('Another hook owns a TT weapon-effect call')
            changes.append((site,original,replacement))
        if changes:self.memory.replace_calls(changes)

    def close(self):
        self.memory.write_u32(self.service+s.ENABLED,0)
        changes=[(site,replacement,original) for site,original,replacement in self.entries
                 if self.memory.read(site,5)==replacement]
        if changes:self.memory.replace_calls(changes)
        # Retain allocations for callbacks already in flight.

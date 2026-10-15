"""TT positive enemy-hit healing, after native damage and Damaged observers.

Observe actual health+armour loss, not requested damage or a kill event. Preserve
the caller's SafeData health lookup and SSE registers; never replace observers.
"""
from . import traps as t, rewards as r
from .bonuses import HEAL_VALUE, HEAL_MAX, call_bytes
from .tt_traps import guarded
from .tt_weapon_effects import save,restore
from .thunk import Code, CODE_OFFSET, u32
from .profile import UnsupportedBuild

CALL, LOOKUP, HITS = 0x168871, 0x33D0, 2044
NONLETHAL, HP_BEFORE, HP_AFTER, ARMOUR_BEFORE, ARMOUR_AFTER = range(2048,2068,4)


def expected_anchors(a):
    b=a.band_base
    return {
        b+0x1687EA:bytes.fromhex('f30f10442410f30f5c44242033c9894c2424f30f58442414f30f5c00'),
        b+0x16880E:bytes.fromhex('f30f11442410398bf80600007e3a8d642400'),
        b+0x168856:bytes.fromhex('83bb30070000ffc744242400000000741affb3300700008d4c2428'),
        b+0x168876:bytes.fromhex('8b44242883c40485c07505'),
        b+LOOKUP:bytes.fromhex('5156ff74240c8bf1e8a38e16008bd083c40485d2750532c05e59c3'),
        b+0x118D10:bytes.fromhex('8b8164070000c3'), # Get_Player_Type, virtual slot 0x68.
    }


def build_hit(address,control,service,a,*,testing=False):
    from .tt_inventory import GET_HEALTH,GET_MAX_HEALTH,SET_HEALTH,GET_ARMOUR,GET_MAX_ARMOUR,SET_ARMOUR
    c=Code(address);save(c)
    guarded(a)(c,control,service,'normal')
    t.deadline(c,control,t.VAMPIRE_END,'normal')
    # Original caller ESP+0x10 contains total actual damage. One argument and
    # the return address precede our 164-byte register save: 0x10+8+164=0xBC.
    c.emit(b'\x8b\x84\x24'+u32(0xBC)+b'\x85\xc0');c.jump(b'\x0f\x8e','normal')
    c.emit(b'\x3d'+u32(0x7F800000));c.jump(b'\x0f\x83','normal')
    c.emit(b'\x8b\x44\x24\x08\x85\xc0');c.jump(b'\x0f\x84','normal') # Offense EBP.
    c.emit(b'\x8b\x40\x14\x85\xc0');c.jump(b'\x0f\x84','normal')
    c.emit(b'\x3b\x70\x04');c.jump(b'\x0f\x85','normal')
    c.emit(b'\x8b\x5c\x24\x10\x85\xdb');c.jump(b'\x0f\x84','normal') # Victim EBX.
    c.emit(b'\x39\xf3');c.jump(b'\x0f\x84','normal')
    # Same team rules as TT engine_obj.cpp::Is_Enemy. Use virtual team getters
    # so objects overriding team membership retain their native semantics.
    c.emit(b'\x8b\x06\x89\xf1\xff\x50\x68\x50')
    c.emit(b'\x8b\x03\x89\xd9\xff\x50\x68\x5a')
    for value in (0xFE,0xFC): # -2 neutral and -4 spectator are never enemies.
        c.emit(b'\x83\xf8'+bytes([value]));c.jump(b'\x0f\x84','normal')
        c.emit(b'\x83\xfa'+bytes([value]));c.jump(b'\x0f\x84','normal')
    c.emit(b'\x83\xf8\xff');c.jump(b'\x0f\x84','enemy')
    c.emit(b'\x83\xfa\xff');c.jump(b'\x0f\x84','enemy')
    c.emit(b'\x39\xd0');c.jump(b'\x0f\x84','normal')
    c.label('enemy')
    for getter,maximum,setter,label in ((GET_HEALTH,GET_MAX_HEALTH,SET_HEALTH,'health'),
                                      (GET_ARMOUR,GET_MAX_ARMOUR,SET_ARMOUR,'armour')):
        c.emit(b'\x56');c.jump(b'\xe8',getter);c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(control+HEAL_VALUE))
        if label=='health':
            c.emit(b'\x83\x3d'+u32(control+HEAL_VALUE)+b'\x00');c.jump(b'\x0f\x8e','normal')
        if testing:
            c.emit(b'\xa1'+u32(control+HEAL_VALUE)+b'\xa3'+u32(control+(HP_BEFORE if label=='health' else ARMOUR_BEFORE)))
        c.emit(b'\x56');c.jump(b'\xe8',maximum);c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(control+HEAL_MAX))
        c.emit(b'\xd9\x05'+u32(control+r.TEN)+b'\xd8\x05'+u32(control+HEAL_VALUE)+b'\xd9\x1d'+u32(control+HEAL_VALUE))
        c.emit(b'\xa1'+u32(control+HEAL_VALUE)+b'\x3b\x05'+u32(control+HEAL_MAX));c.jump(b'\x0f\x86',label)
        c.emit(b'\xa1'+u32(control+HEAL_MAX))
        c.label(label)
        if testing:c.emit(b'\xa3'+u32(control+(HP_AFTER if label=='health' else ARMOUR_AFTER)))
        c.emit(b'\x50\x56');c.jump(b'\xe8',setter);c.emit(b'\x83\xc4\x08')
    if testing:c.emit(b'\xff\x05'+u32(control+HITS))
    if testing:
        c.emit(b'\x53');c.jump(b'\xe8',GET_HEALTH);c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(control+HEAL_VALUE))
        c.emit(b'\x83\x3d'+u32(control+HEAL_VALUE)+b'\x00');c.jump(b'\x0f\x8e','normal')
        c.emit(b'\xff\x05'+u32(control+NONLETHAL))
    c.label('normal');restore(c);c.jump(b'\xe9',a.band_base+LOOKUP)
    return c.finish()


class NativeVampiric:
    def __init__(self,memory,control,service,a,*,testing=False):
        from .tt_inventory import install_code
        self.memory=memory
        for address,expected in expected_anchors(a).items():
            if memory.read(address,len(expected))!=expected:
                raise UnsupportedBuild(f'TT enemy-hit anchor differs at {address:#x}')
        self.site=a.band_base+CALL
        self.original=call_bytes(self.site,a.band_base+LOOKUP)
        if memory.read(self.site,5)!=self.original:raise UnsupportedBuild('Another hook owns the TT enemy-hit call')
        self.code=memory.allocate()+CODE_OFFSET
        install_code(memory,self.code,build_hit(self.code,control,service,a,testing=testing))
        self.replacement=call_bytes(self.site,self.code)

    def update(self):
        current=self.memory.read(self.site,5)
        if current==self.replacement:return
        if current!=self.original:raise OSError('Another hook owns the TT enemy-hit call')
        self.memory.replace_calls([(self.site,self.original,self.replacement)])

    def close(self):
        if self.memory.read(self.site,5)==self.replacement:
            self.memory.replace_calls([(self.site,self.replacement,self.original)])

"""TT movement effects: per-frame controls and local ballistic acceleration.

TT compiles ballistic motion with SSE, unlike Steam's x87 implementation. Only
the current call's XMM1 acceleration is scaled; saved physics stays untouched.
"""
from functools import partial

from . import traps as t
from .tt_discs import STAR, CAMERA, COMBAT_OBJECT
from .thunk import Code, CODE_OFFSET, u32
from .profile import UnsupportedBuild

APPLY_CONTROL, CONTROL_SLOT = 0x248A50, 0x2EEA6C
BALLISTIC_MOVE, BALLISTIC_SLOT = 0x1BF130, 0x2E8310
FRAME_SECONDS = 0x857290


def ballistic_prefix(a):
    return (bytes.fromhex('558bec83e4f883ec20f30f105508a1')+u32(a.band_base+0x3221D8)+
            bytes.fromhex('53f30f104808568bf151f30f1086e0000000f30f598e8c000000'))


def expected_anchors(a):
    b=a.band_base
    return {
        b+APPLY_CONTROL:bytes.fromhex('558bec83e4f883ec285355568bf15780be4d0d0000007439'),
        b+BALLISTIC_MOVE:ballistic_prefix(a),
        b+0x5FA670:u32(FRAME_SECONDS),
        0x701D1D:bytes.fromhex('a1907285008bcb50e826f6ffff'),
    }


def guarded(a):
    return partial(t.guard,addresses=a,star=STAR,camera=CAMERA,combat_object=COMBAT_OBJECT)


def build_gravity(address,control,service,a):
    c=Code(address)
    c.emit(b'\x9c\x60\xfc')
    guarded(a)(c,control,service,'ordinary')
    c.emit(b'\x8b\x54\x24\x18\x3b\x96'+u32(t.PLAYER_PHYSICS));c.jump(b'\x0f\x85','ordinary')
    c.emit(b'\x8b\x96'+u32(t.HUMAN_STATE)+b'\x85\xd2');c.jump(b'\x0f\x84','eligible')
    c.emit(b'\x83\xfa\x05');c.jump(b'\x0f\x85','ordinary')
    c.label('eligible');t.deadline(c,control,t.MOON_END,'ordinary')
    c.emit(b'\x61\x9d'+ballistic_prefix(a))
    c.emit(b'\xf3\x0f\x59\x0d'+u32(control+t.GRAVITY_FACTOR))
    c.jump(b'\xe9',a.band_base+BALLISTIC_MOVE+len(ballistic_prefix(a)))
    c.label('ordinary');c.emit(b'\x61\x9d');c.jump(b'\xe9',a.band_base+BALLISTIC_MOVE)
    return c.finish()


class NativeTraps(t.NativeTraps):
    def __init__(self,memory,control,service,a):
        from .tt_inventory import install_code
        self.memory,self.service,self.entries=memory,service,[]
        self.addresses,self.control,self.hud=a,control,None
        for address,expected in expected_anchors(a).items():
            if memory.read(address,len(expected))!=expected:
                raise UnsupportedBuild(f'TT movement anchor differs at {address:#x}')
        for slot,original in ((CONTROL_SLOT,APPLY_CONTROL),(BALLISTIC_SLOT,BALLISTIC_MOVE)):
            if memory.u32(a.band_base+slot)!=a.band_base+original:
                raise UnsupportedBuild('Another hook owns a TT movement callback')
        t.initialise(memory,control)
        self.clock=memory.allocate()+CODE_OFFSET
        install_code(memory,self.clock,t.build_clock(self.clock,control,service,
            guard_builder=guarded(a),frame_seconds=FRAME_SECONDS))
        for slot,original in ((CONTROL_SLOT,APPLY_CONTROL),(BALLISTIC_SLOT,BALLISTIC_MOVE)):
            code=memory.allocate()+CODE_OFFSET
            program=(t.build_movement(code,control,service,guard_builder=guarded(a),
                apply_control=a.band_base+APPLY_CONTROL,collision_group=0x6A59B0) if slot==CONTROL_SLOT
                else build_gravity(code,control,service,a))
            install_code(memory,code,program)
            self.entries.append((a.band_base+slot,a.band_base+original,code))

    def add_render(self,hud_guard):
        from .tt_inventory import install_code
        from .tt_effects_hud import NativeEffectsHUD,bindings
        if self.hud is not None:raise ValueError('TT effects renderer already constructed')
        a=self.addresses
        original=hud_guard.control+CODE_OFFSET
        field=hud_guard.a['slot']
        if self.memory.u32(field)!=original:
            raise UnsupportedBuild('TT effects renderer requires the verified HUD load guard')
        self.hud=NativeEffectsHUD(self.memory,self.control,self.service,a)
        code=self.memory.allocate()+CODE_OFFSET
        program=t.build_disco(code,self.control,self.service,self.hud.code,
            guard_builder=guarded(a),render=original,bindings=bindings(a))
        install_code(self.memory,code,program)
        self.entries.append((field,original,code))

    def update(self):
        if self.hud:self.hud.update()
        # Validate every owner before replacing any pointer.
        for field,original,code in self.entries:
            if self.memory.u32(field) not in (original,code):
                raise OSError('Another hook owns a TT movement callback')
        for field,original,code in self.entries:
            if self.memory.u32(field)==original:
                self.memory.replace_readonly_u32(field,original,code)

"""TT's render bindings for the private effects table and Disco Havoc overlay."""
import struct

from . import traps as t, effects_hud as h
from .tt_discs import STAR, CAMERA
from .thunk import CODE_OFFSET, u32
from .profile import UnsupportedBuild

FONT_INSTANCE = 0x81DF54


def bindings(a):
    return {t.NEW:a.band_base+0x29B5D0,t.RENDER_CTOR:0x532590,t.SET_RANGE:0x532B20,
        t.ALPHA:0x532A70,t.ADD_QUAD:0x5340C0,t.DRAW:a.band_base+0xB2E30,
        h.TEXT_CTOR:0x535D80,h.TEXT_DRAW:a.band_base+0xB27E0,h.SET_LOCATION:0x42C5D0}


def expected_anchors(a):
    b=a.band_base
    anchors={
        0x532590:bytes.fromhex('5355568bf1b80000803f33db8d6e2089460489460857895e0c'),
        0x532B20:bytes.fromhex('8b44240483ec08d94008d820d83d38f07c00d95904'),
        0x532A70:bytes.fromhex('8a44240484c08b4120741524bf0ca08bd089412080e6bf80'),
        0x5340C0:bytes.fromhex('5356578bf96a008b87c400000050e85df3ffff8b87bc0000'),
        0x535D80:bytes.fromhex('83ec085333db568bf153e801c8ffff899eb4050000'),
        0x42C5D0:bytes.fromhex('8b4424048b108991b80500008b5004'),
        0x42BCF1:b'\x89\x35'+u32(FONT_INSTANCE),
        b+0xB2E30:bytes.fromhex('558bec83e4f883ec3c53568bf157837e34000f84da030000'),
        b+0xB27E0:bytes.fromhex('558bec83e4f85153568bf157f30f1086bc050000f30f1096b8050000'),
        b+0x29B5D0:bytes.fromhex('6a006a0068')+u32(b+0x2F258C)+bytes.fromhex('6a06ff742414ff15')+u32(b+0x2C9474)+bytes.fromhex('83c414c3'),
    }
    for address,target in ((0x672B70,b+0x29B5D0),(0x534FC0,b+0xB2E30),(0x536720,b+0xB27E0)):
        anchors[address]=b'\xe9'+u32((target-address-5)&0xFFFFFFFF)
    return anchors


class NativeEffectsHUD(h.NativeEffectsHUD):
    table_top = 100  # Leave the TT EVA portrait, objective and range text clear.
    def __init__(self,memory,reward,service,a):
        from .tt_inventory import install_code
        self.memory,self.reward,self.service=memory,reward,service
        for address,expected in expected_anchors(a).items():
            if memory.read(address,len(expected))!=expected:
                raise UnsupportedBuild(f'TT effects renderer anchor differs at {address:#x}')
        self.control=memory.allocate()
        memory.write(self.control,b'\0'*4096)
        memory.write(self.control+h.RANGE,struct.pack('<5f',0,0,640,480,14))
        self.code=self.control+CODE_OFFSET
        install_code(memory,self.code,h.build_hud(self.code,self.control,reward,service,
            bindings=bindings(a),addresses=a,star=STAR,camera=CAMERA,font_instance=FONT_INSTANCE))

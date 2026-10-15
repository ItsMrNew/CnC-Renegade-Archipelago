"""TT supply shortcuts using the live primary/secondary key tables.

TT can load its configuration from packed assets. Read current mappings rather
than assuming an absent loose input01.cfg contains the active bindings.
"""
import logging
import struct

from ..hotkeys import SupplyHotkeys
from .profile import UnsupportedBuild

PRIMARY, SECONDARY, COUNT = 0x856BD8, 0x856524, 134
CHAT = {51,52,53}
ANCHORS = {
    0x6763C0: bytes.fromhex('8b4424048b0485d86b8500c3'),
    0x6763D0: bytes.fromhex('8b4424048b048524658500c3'),
    0x675CD3: bytes.fromhex('8b14b5d86b8500680000803f52'),
    0x675CE6: bytes.fromhex('8b04b524658500680000803f'),
}


def conflicts(memory):
    disabled=set()
    for table in (PRIMARY,SECONDARY):
        for index,key in enumerate(struct.unpack('<134I',memory.read(table,COUNT*4))):
            if key in (0x3C,0x3D,0x3E) and index not in CHAT:
                disabled.add(key+0x35)  # DirectInput scan code -> F2/F3/F4 virtual key.
    return disabled


class NativeHotkeys(SupplyHotkeys):
    def __init__(self,memory,sample=None):
        for address,expected in ANCHORS.items():
            if memory.read(address,len(expected)) != expected:
                raise UnsupportedBuild(f'TT input mapping anchor differs at {address:#x}')
        self.memory,self.pid=memory,memory.pid
        self.previous=None
        self.disabled=conflicts(memory)
        self.sample=sample or self.windows_sample()
        self.last_report=None

    def update(self,client,active):
        self.disabled=conflicts(self.memory)
        if self.disabled != self.last_report:
            if self.disabled:
                labels=', '.join(f'F{key-0x6f}' for key in sorted(self.disabled))
                logging.getLogger('Renegade').warning('TT supply shortcuts %s conflict with game bindings; use launcher buttons',labels)
            self.last_report=set(self.disabled)
        super().update(client,active)

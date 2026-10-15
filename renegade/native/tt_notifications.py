"""TT local message window; its old DebugManager is uninitialized in TT.

Calls the same local display used by TT Send_Message without broadcasting any
network text. WideString and RGB buffers are owned by this mailbox; the native
window copies the text before acknowledging the request.
"""
import struct
import time

from .notifications import NativeNotifications as SharedNotifications, REQUEST, TEXT, COLOUR
from .thunk import Code, CODE_OFFSET, u32
from .profile import UnsupportedBuild

MESSAGE_WINDOW, ADD_MESSAGE, WIDE_STRING = 0x855EDC, 0x6A76E0, 32
ANCHORS = {
    ADD_MESSAGE: bytes.fromhex('51538bd9895c24048b430485c00f8468020000d9442418d81db4d77c00555657'),
    0x6A795B: bytes.fromhex('5b59c21000'),
}


def build_notifications(control):
    c=Code(control+CODE_OFFSET)
    c.emit(b'\x9c\x60\x83\x3d'+u32(control+REQUEST)+b'\x01')
    c.jump(b'\x0f\x85','done')
    c.emit(b'\x8b\x0d'+u32(MESSAGE_WINDOW)+b'\x85\xc9');c.jump(b'\x0f\x84','done')
    c.emit(b'\x83\x79\x04\x00');c.jump(b'\x0f\x84','done')
    c.emit(b'\x68'+struct.pack('<f',6.0)+b'\x6a\x00\x68'+u32(control+COLOUR)+b'\x68'+u32(control+WIDE_STRING))
    c.jump(b'\xe8',ADD_MESSAGE)  # thiscall; native callee pops 16 bytes.
    c.emit(b'\xc7\x05'+u32(control+REQUEST)+u32(0))
    c.label('done');c.emit(b'\x61\x9d\xc3')
    return c.finish()


class NativeNotifications(SharedNotifications):
    def __init__(self,memory,clock=time.monotonic):
        from .tt_inventory import install_code
        for address,expected in ANCHORS.items():
            if memory.read(address,len(expected))!=expected:
                raise UnsupportedBuild(f'TT message window anchor differs at {address:#x}')
        self.memory,self.clock,self.next_message=memory,clock,0
        self.control=memory.allocate()
        memory.write(self.control,b'\0'*4096)
        memory.write_u32(self.control+WIDE_STRING,self.control+TEXT)
        install_code(memory,self.control+CODE_OFFSET,build_notifications(self.control))

    def write_message(self,message,colour):
        text=message.rstrip('\0')[:200]
        encoded=(text+'\0').encode('utf-16le')
        self.memory.write(self.control+TEXT-8,struct.pack('<2I',len(text)+1,len(text))+encoded)
        self.memory.write(self.control+COLOUR,struct.pack('<3f',*(n/255 for n in colour)))

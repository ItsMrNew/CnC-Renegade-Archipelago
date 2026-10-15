"""Game-thread text display. No remote strings are used as printf formats."""
import struct
import time
import textwrap

from ..notifications import item_colour, safe_text
from .thunk import Code, u32, CODE_OFFSET
from .savebuilder import BASE
from .profile import UnsupportedBuild

REQUEST, TEXT, COLOUR = 0, 64, 512
# DebugManager::Display_Text(const char *, const Vector4 &), stock Steam.
DISPLAY_TEXT = 0xF1090
ANCHOR = bytes.fromhex('8b0d3869850085c9740f8b5424088b01528b54240852ff5004c3')


def build_notifications(control, display=BASE+DISPLAY_TEXT):
    c = Code(control + CODE_OFFSET)
    c.emit(b'\x9c\x60\x83\x3d' + u32(control+REQUEST) + b'\x01')
    c.jump(b'\x0f\x85', 'done')
    c.emit(b'\x68' + u32(control+COLOUR) + b'\x68' + u32(control+TEXT))
    c.jump(b'\xe8', display)
    c.emit(b'\x83\xc4\x08\xc7\x05' + u32(control+REQUEST) + u32(0))
    c.label('done')
    c.emit(b'\x61\x9d\xc3')
    return c.finish()


class NativeNotifications:
    def __init__(self, memory, clock=time.monotonic, *, display=BASE+DISPLAY_TEXT, anchor=ANCHOR):
        self.memory, self.clock = memory, clock
        if memory.read(display, len(anchor)) != anchor:
            raise UnsupportedBuild('Native notification display anchor differs')
        self.control = memory.allocate()
        memory.write(self.control, b'\0'*4096)
        program = build_notifications(self.control, display)
        memory.write(self.control+CODE_OFFSET, program)
        if memory.read(self.control+CODE_OFFSET, len(program)) != program:
            raise OSError('Notification program read-back failed')
        memory.make_executable(self.control+CODE_OFFSET)
        self.next_message = 0

    def update(self, client):
        mem, ctrl = self.memory, self.control
        if not client.ready or mem.u32(ctrl+REQUEST) or self.clock() < self.next_message:
            return
        feed = client.notifications
        while feed.queue:
            event = feed.queue.popleft()
            if not feed.accepts(event):
                continue
            # Keep sender, item and recipient independently bounded so all three
            # remain visible even with a long player alias or cross-game item name.
            if event[0] is None:
                text = safe_text(feed.text(event), 160)
            else:
                parts = feed.log_parts(event)
                text = (safe_text(feed.name(event[0]), 32) + ' sent ' +
                        safe_text(parts[1][0], 70) + ' to ' + safe_text(feed.name(event[1]), 32))
            colour = bytes.fromhex(item_colour(event[4])[1:])
            message = '\n'.join(textwrap.wrap('[AP] '+text, width=72)) + '\n\0'
            self.write_message(message, colour)
            # Publish last; do not touch the buffer until the game acknowledges it.
            mem.write_u32(ctrl+REQUEST, 1)
            self.next_message = self.clock()+2
            break

    def write_message(self, message, colour):
        mem, ctrl = self.memory, self.control
        mem.write(ctrl+TEXT, message.encode('ascii'))
        # Stock Print(Vector4) packs components left-to-right into a DWORD,
        # then Draw_Text consumes ARGB. Supply A,R,G,B to avoid shifted
        # channels/transparency (not the conventional R,G,B,A order).
        mem.write(ctrl+COLOUR, struct.pack('<4f', 1, *(n/255 for n in colour)))

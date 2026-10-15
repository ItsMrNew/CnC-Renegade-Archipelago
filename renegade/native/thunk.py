"""Emit a small x86 cdecl wrapper around ScriptCommands::Mission_Complete.

Preserves registers, flags, stack and the original callback's return behavior.
Only single-player successful completions with an active client lease are latched.
"""
import struct
from types import SimpleNamespace
from ..data import MISSIONS
from .profile import STEAM

LEASE_MS = 2000
CODE_OFFSET = 4096
ALLOWED = 0
HEARTBEAT = 4
COMPLETED = 8
MAGIC = b"RENEAP01"


def u32(value):
    return struct.pack("<I", value & 0xFFFFFFFF)


class Code:
    def __init__(self, address):
        self.address = address
        self.data = bytearray()
        self.labels = {}
        self.fixups = []

    def emit(self, value):
        self.data.extend(value)

    def label(self, name):
        self.labels[name] = len(self.data)

    def jump(self, opcode, label):
        self.emit(opcode)
        self.fixups.append((len(self.data), label))
        self.emit(b"\0" * 4)

    def finish(self):
        for offset, label in self.fixups:
            destination = self.labels[label] if isinstance(label, str) else label - self.address
            struct.pack_into("<I", self.data, offset, (destination - offset - 4) & 0xFFFFFFFF)
        return bytes(self.data)


def steam_addresses(base=0x400000):
    """Absolute addresses; another backend must supply its own verified binding."""
    return SimpleNamespace(**{name: base + getattr(STEAM, name) for name in
        ('tick_iat', 'game_mode', 'game_data', 'loaded_map', 'mission_complete',
         'combat_keyboard', 'combat_think', 'message_count', 'message_dialog',
         'pending_continue', 'end_game', 'end_game_menu')})


def build_thunk(control, base=0x400000, *, addresses=None):
    addresses = addresses or steam_addresses(base)
    code = Code(control + CODE_OFFSET)
    code.emit(b"\x9c\x60")  # pushfd; pushad
    code.emit(b"\x80\x7c\x24\x28\x00")  # bool success, original esp+4
    code.jump(b"\x0f\x84", "exit")
    code.emit(b"\x83\x3d" + u32(control + ALLOWED) + b"\x00")
    code.jump(b"\x0f\x84", "exit")
    code.emit(b"\xff\x15" + u32(addresses.tick_iat))
    code.emit(b"\x2b\x05" + u32(control + HEARTBEAT))
    code.emit(b"\x3d" + u32(LEASE_MS))
    code.jump(b"\x0f\x87", "exit")  # unsigned elapsed > lease
    code.emit(b"\x83\x3d" + u32(addresses.game_mode) + b"\x01")
    code.jump(b"\x0f\x85", "exit")
    code.emit(b"\xa1" + u32(addresses.game_data))
    code.emit(b"\x85\xc0")
    code.jump(b"\x0f\x84", "exit")
    code.emit(b"\x8b\x15" + u32(addresses.loaded_map))  # loaded level, including save loads
    code.emit(b"\x85\xd2")
    code.jump(b"\x0f\x84", "exit")
    # Check the first four characters before reading beyond a short map string.
    code.emit(b"\x8b\x02\x0d" + u32(0x20202020))
    for index, mission in enumerate(MISSIONS):
        code.emit(b"\x3d" + (mission.map_name.lower() + ".").encode("ascii"))
        code.jump(b"\x0f\x84", f"mission{index}")
    code.jump(b"\xe9", "exit")
    for index, _ in enumerate(MISSIONS):
        code.label(f"mission{index}")
        code.emit(b"\xb9" + u32(1 << index))  # ecx = mission bit
        code.jump(b"\xe9", "capture")
    code.label("capture")
    code.emit(b"\x8b\x42\x04\x0d" + u32(0x00202020))
    code.emit(b"\x3d" + b"mix\0")
    code.jump(b"\x0f\x84", "known_suffix")
    code.emit(b"\x3d" + b"lsd\0")
    code.jump(b"\x0f\x85", "exit")
    code.label("known_suffix")
    code.emit(b"\x85\x0d" + u32(control + ALLOWED))
    code.jump(b"\x0f\x84", "exit")
    code.emit(b"\xf0\x09\x0d" + u32(control + COMPLETED))  # lock or [completed], ecx
    code.label("exit")
    code.emit(b"\x61\x9d")  # popad; popfd
    code.jump(b"\xe9", addresses.mission_complete)
    result = code.finish()
    if len(result) > 4096:
        raise ValueError("Native thunk exceeds its code page")
    return result

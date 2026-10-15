"""Keep TT-only purchase definitions out of the campaign object-ID registry.

Live collision: a PurchaseSettingsDefClass (network class 2004) acquired the
saved campaign object's ID 1500000204 while loading an unmodified Steam save.
TT's network-base constructor auto-registers definitions when IsServer is true.

Wrap only the PurchaseSettings/TeamPurchaseSettings constructor calls. In SP,
execute a verified copy of the same base constructor with auto-registration
skipped. Its initialized ID remains zero. Other objects, saved IDs, multiplayer
and the collision validator are unchanged. No shared IsServer toggle is used.
The compatibility wrapper stays until process exit, independent of AP rooms.
"""
import hashlib
import struct

from .memory import MemoryError
from .profile import UnsupportedBuild
from .thunk import Code, CODE_OFFSET, u32

MAGIC = b'RENETTLOAD01\0'
SINGLE_PLAYER_DEFINITIONS, ORIGINAL_CALLS = 16, 20
CONSTRUCTOR = 0x2AF410
CTOR_SIZE = 0x107
CLONE_OFFSET = 0x100
CALL_SITES = (0x2014A3, 0x25C99D)
RELOCATIONS = (2, 12, 18, 59, 72, 94)
RELATIVE_CALLS = (193, 206, 211, 220, 242, 252)
CTOR_SHA256 = '4e8d8a8cfeae3a3c7336a093f2c5e3e922d45fa4ae1ebfcb723aa39e982bc85c'


def verify_constructor(memory, addresses):
    code = memory.read(addresses.band_base+CONSTRUCTOR, CTOR_SIZE)
    canonical = bytearray(code)
    for offset in RELOCATIONS:
        value = struct.unpack_from('<I', canonical, offset)[0]
        struct.pack_into('<I', canonical, offset, (value-addresses.band_base+0x12000000)&0xFFFFFFFF)
    if hashlib.sha256(canonical).hexdigest() != CTOR_SHA256:
        raise UnsupportedBuild('TT network constructor differs; save-load guard not installed')
    return code


def call(site, destination):
    return b'\xe8'+u32(destination-site-5)


def build_guard(control, addresses, original):
    entry = control+CODE_OFFSET
    c = Code(entry)
    c.emit(b'\x83\x3d'+u32(addresses.game_mode)+b'\x01')
    c.jump(b'\x0f\x85', 'original')
    c.emit(b'\xff\x05'+u32(control+SINGLE_PLAYER_DEFINITIONS))
    c.jump(b'\xe9', entry+CLONE_OFFSET)
    c.label('original')
    c.emit(b'\xff\x05'+u32(control+ORIGINAL_CALLS))
    c.jump(b'\xe9', addresses.band_base+CONSTRUCTOR)
    prefix = c.finish()
    clone = bytearray(original)
    # Replace the constructor's IsServer conditional with its own skip branch.
    # It still initializes every member and returns the original this pointer.
    if clone[0xB4:0xB6] != b'\x74\x2e':
        raise UnsupportedBuild('TT constructor registration branch differs')
    clone[0xB4] = 0xEB
    for offset in RELATIVE_CALLS:
        destination = addresses.band_base+CONSTRUCTOR+offset+5+struct.unpack_from('<i',original,offset+1)[0]
        clone[offset:offset+5] = call(entry+CLONE_OFFSET+offset, destination)
    return prefix+b'\xcc'*(CLONE_OFFSET-len(prefix))+clone


class NativeLoadGuard:
    def __init__(self, memory, addresses):
        self.memory, self.addresses = memory, addresses
        self.original = verify_constructor(memory, addresses)
        self.control = None
        original_target = addresses.band_base+CONSTRUCTOR
        targets = []
        for rva in CALL_SITES:
            site = addresses.band_base+rva
            raw = memory.read(site, 5)
            if raw[:1] != b'\xe8':
                raise UnsupportedBuild('TT purchase constructor call is modified')
            targets.append(site+5+struct.unpack_from('<i',raw,1)[0])
        if targets == [original_target]*len(CALL_SITES):
            return
        if len(set(targets)) != 1 or targets[0] < CODE_OFFSET:
            raise UnsupportedBuild('TT purchase constructors have unknown owners')
        control = targets[0]-CODE_OFFSET
        try:
            code = build_guard(control, addresses, self.original)
            valid = (memory.read(control,len(MAGIC)) == MAGIC
                     and memory.read(targets[0],len(code)) == code)
        except OSError:
            valid = False
        if not valid:
            raise UnsupportedBuild('TT purchase constructors have an unknown hook')
        self.control = control

    def install(self):
        if self.control is not None:
            return False
        m, a = self.memory, self.addresses
        control = m.allocate()
        m.write(control,b'\0'*4096)
        m.write(control,MAGIC)
        program = build_guard(control,a,self.original)
        m.write(control+CODE_OFFSET,program)
        if m.read(control+CODE_OFFSET,len(program)) != program:
            raise MemoryError('TT save-load guard read-back failed')
        m.make_executable(control+CODE_OFFSET)
        # The memory layer suspends the process, verifies both complete CALL
        # instructions, rolls back a failed batch and resumes it in finally.
        m.replace_calls([(a.band_base+rva,call(a.band_base+rva,a.band_base+CONSTRUCTOR),
                          call(a.band_base+rva,control+CODE_OFFSET)) for rva in CALL_SITES])
        self.control = control
        return True

    def snapshot(self):
        return dict(installed=self.control is not None,
                    campaign_definitions=self.memory.u32(self.control+SINGLE_PLAYER_DEFINITIONS) if self.control else 0,
                    original_calls=self.memory.u32(self.control+ORIGINAL_CALLS) if self.control else 0)

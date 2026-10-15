"""Small Win32 process-memory adapter, based on our Zero Hour adapter.

Read access is the default. Writes require a separate explicit writable handle.
"""
import ctypes as ct
from ctypes import wintypes as wt
from dataclasses import dataclass
import os
from pathlib import Path
import struct


class MemoryError(OSError):
    pass


class PROCESSENTRY32W(ct.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ct.c_size_t), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]


class MODULEENTRY32W(ct.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD),
                ("modBaseAddr", ct.c_void_p), ("modBaseSize", wt.DWORD), ("hModule", wt.HMODULE),
                ("szModule", wt.WCHAR * 256), ("szExePath", wt.WCHAR * 260)]


@dataclass(frozen=True)
class Module:
    name: str
    path: Path
    base: int
    size: int


def kernel():
    if os.name != "nt":
        raise MemoryError("Native Renegade integration requires Windows")
    api = ct.WinDLL("kernel32", use_last_error=True)
    specs = {
        "CreateToolhelp32Snapshot": ([wt.DWORD, wt.DWORD], wt.HANDLE),
        "Process32FirstW": ([wt.HANDLE, ct.POINTER(PROCESSENTRY32W)], wt.BOOL),
        "Process32NextW": ([wt.HANDLE, ct.POINTER(PROCESSENTRY32W)], wt.BOOL),
        "Module32FirstW": ([wt.HANDLE, ct.POINTER(MODULEENTRY32W)], wt.BOOL),
        "Module32NextW": ([wt.HANDLE, ct.POINTER(MODULEENTRY32W)], wt.BOOL),
        "OpenProcess": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        "CloseHandle": ([wt.HANDLE], wt.BOOL),
        "ReadProcessMemory": ([wt.HANDLE, ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.POINTER(ct.c_size_t)], wt.BOOL),
        "WriteProcessMemory": ([wt.HANDLE, ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.POINTER(ct.c_size_t)], wt.BOOL),
        "VirtualAllocEx": ([wt.HANDLE, ct.c_void_p, ct.c_size_t, wt.DWORD, wt.DWORD], ct.c_void_p),
        "VirtualProtectEx": ([wt.HANDLE, ct.c_void_p, ct.c_size_t, wt.DWORD, ct.POINTER(wt.DWORD)], wt.BOOL),
        "FlushInstructionCache": ([wt.HANDLE, ct.c_void_p, ct.c_size_t], wt.BOOL),
        "GetTickCount": ([], wt.DWORD),
    }
    for name, (args, result) in specs.items():
        getattr(api, name).argtypes = args
        getattr(api, name).restype = result
    return api


def fail(action, pid):
    code = ct.get_last_error()
    if code == 5:
        raise MemoryError(f"Access denied to Renegade process {pid}. The game may be elevated; "
                          "run this client with the same Windows privileges as the game.")
    raise MemoryError(f"Could not {action} process {pid} (Windows error {code})")


def snapshot(api, flags, pid):
    handle = api.CreateToolhelp32Snapshot(flags, pid)
    if handle == ct.c_void_p(-1).value:
        fail("inspect", pid)
    return handle


def candidates():
    api = kernel()
    handle = snapshot(api, 2, 0)
    entry = PROCESSENTRY32W()
    entry.dwSize = ct.sizeof(entry)
    result = []
    try:
        ok = api.Process32FirstW(handle, ct.byref(entry))
        while ok:
            if entry.szExeFile.lower() in {"game.dat", "game.exe", "game2.exe"}:
                result.append(entry.th32ProcessID)
            ok = api.Process32NextW(handle, ct.byref(entry))
    finally:
        api.CloseHandle(handle)
    return result


class ProcessMemory:
    def __init__(self, pid, writable=False):
        self.api = kernel()
        self.pid, self.writable = pid, writable
        self.handle = self.api.OpenProcess(0x1038 if writable else 0x1010, False, pid)
        if not self.handle:
            fail("open", pid)
        self.modules = []
        try:
            handle = snapshot(self.api, 0x18, pid)
            try:
                entry = MODULEENTRY32W()
                entry.dwSize = ct.sizeof(entry)
                ok = self.api.Module32FirstW(handle, ct.byref(entry))
                while ok:
                    self.modules.append(Module(entry.szModule, Path(entry.szExePath), entry.modBaseAddr, entry.modBaseSize))
                    ok = self.api.Module32NextW(handle, ct.byref(entry))
            finally:
                self.api.CloseHandle(handle)
            if not self.modules:
                raise MemoryError("Process has no readable modules")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def read(self, address, size):
        if not 0x10000 <= address <= 0xFFFFFFFF or not 0 < size <= 8192:
            raise MemoryError("Invalid x86 memory range")
        buffer = ct.create_string_buffer(size)
        count = ct.c_size_t()
        if not self.api.ReadProcessMemory(self.handle, address, buffer, size, ct.byref(count)) or count.value != size:
            fail("read", self.pid)
        return buffer.raw

    def u32(self, address):
        return struct.unpack("<I", self.read(address, 4))[0]

    def write(self, address, data):
        if not self.writable:
            raise MemoryError("This process handle is read-only")
        if not 0x10000 <= address <= 0xFFFFFFFF or not 0 < len(data) <= 8192:
            raise MemoryError("Invalid x86 memory write")
        buffer = ct.create_string_buffer(data)
        count = ct.c_size_t()
        if not self.api.WriteProcessMemory(self.handle, address, buffer, len(data), ct.byref(count)) or count.value != len(data):
            fail("write", self.pid)

    def write_u32(self, address, value):
        self.write(address, struct.pack("<I", value))

    def allocate(self):
        if not self.writable:
            raise MemoryError("This process handle is read-only")
        # Data and code use separate pages; only the code page becomes executable.
        address = self.api.VirtualAllocEx(self.handle, None, 8192, 0x3000, 4)
        if not address or address + 8192 > 0x100000000:
            fail("allocate x86 bridge memory in", self.pid)
        return address

    def make_executable(self, address):
        old = wt.DWORD()
        if not self.api.VirtualProtectEx(self.handle, address, 4096, 0x20, ct.byref(old)):
            fail("protect bridge code in", self.pid)
        if not self.api.FlushInstructionCache(self.handle, address, 4096):
            fail("flush bridge code in", self.pid)

    def tick_count(self):
        return self.api.GetTickCount()

    def replace_readonly_u32(self, address, expected, replacement):
        """Replace one aligned vtable pointer and restore the page protection."""
        if not self.writable or address % 4:
            raise MemoryError("Vtable replacement requires a writable handle and aligned address")
        if self.u32(address) != expected:
            raise MemoryError("Vtable ownership changed before replacement")
        old = wt.DWORD()
        if not self.api.VirtualProtectEx(self.handle, address, 4, 4, ct.byref(old)):
            fail("unprotect vtable in", self.pid)
        try:
            if self.u32(address) != expected:
                raise MemoryError("Vtable ownership changed during replacement")
            self.write_u32(address, replacement)
            if self.u32(address) != replacement:
                raise MemoryError("Vtable replacement failed read-back verification")
        finally:
            ignored = wt.DWORD()
            if not self.api.VirtualProtectEx(self.handle, address, 4, old.value, ct.byref(ignored)):
                fail("restore vtable protection in", self.pid)

    def replace_calls(self, replacements):
        """Replace complete x86 CALL instructions while the process is suspended.

        Call sites are single instructions: a suspended instruction pointer cannot
        be inside an overwritten prologue. Roll back the whole batch on failure.
        """
        if not self.writable or any(len(old)!=5 or len(new)!=5 or old[0]!=0xE8 or new[0]!=0xE8
                                    for _,old,new in replacements):
            raise MemoryError('Only complete five-byte calls can be replaced')
        handle = self.api.OpenProcess(0x800, False, self.pid)
        if not handle: fail('open for callback synchronization', self.pid)
        native = ct.WinDLL('ntdll')
        for name in ('NtSuspendProcess','NtResumeProcess'):
            getattr(native,name).argtypes=[wt.HANDLE]
            getattr(native,name).restype=wt.LONG
        suspended = False
        changed = []
        def write_code(address, data):
            protection = wt.DWORD()
            if not self.api.VirtualProtectEx(self.handle,address,5,0x40,ct.byref(protection)):
                fail('unprotect callback',self.pid)
            try:
                self.write(address,data)
                if self.read(address,5)!=data: raise MemoryError('Callback write read-back failed')
                if not self.api.FlushInstructionCache(self.handle,address,5): fail('flush callback',self.pid)
            finally:
                ignored=wt.DWORD()
                if not self.api.VirtualProtectEx(self.handle,address,5,protection.value,ct.byref(ignored)):
                    fail('restore callback protection',self.pid)
        try:
            status=native.NtSuspendProcess(handle)
            if status<0: raise MemoryError(f'Could not suspend process for callback replacement: {status:#x}')
            suspended=True
            for address,old,new in replacements:
                if self.read(address,5)!=old: raise MemoryError('Another hook owns an item callback')
            try:
                for address,old,new in replacements:
                    changed.append((address,old))
                    write_code(address,new)
            except BaseException:
                for address,old in reversed(changed): write_code(address,old)
                raise
        finally:
            try:
                if suspended and native.NtResumeProcess(handle)<0:
                    raise MemoryError('Could not resume Renegade after callback replacement')
            finally:
                self.api.CloseHandle(handle)

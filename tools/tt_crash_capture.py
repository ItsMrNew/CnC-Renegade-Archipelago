"""Capture TT exceptions externally, including when its crash reporter exits silently.

Only reads game memory. Debug events temporarily suspend execution; real
exceptions are passed to the game unchanged. Detaching does not kill the game.
Local dumps can contain private process data; never upload them automatically.

Win32 contracts:
https://learn.microsoft.com/en-us/windows/win32/api/debugapi/nf-debugapi-debugactiveprocess
https://learn.microsoft.com/en-us/windows/win32/api/minidumpapiset/nf-minidumpapiset-minidumpwritedump
"""
import argparse
import ctypes as ct
from ctypes import wintypes as wt
from datetime import datetime, timezone
import json
import msvcrt
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.native.memory import ProcessMemory, candidates
from renegade.native.profile import require_digest
from renegade.native.tt_profile import HASHES, TTAddresses, telemetry
from renegade.native.tt_hud import NativeHUDGuard, bindings, verify_live
from renegade.native.tt_load import NativeLoadGuard

CONTINUE, NOT_HANDLED = 0x10002, 0x80010001
FAULTS = {0xC0000005, 0xC000001D, 0xC0000094, 0xC00000FD, 0xC0000374, 0xC0000409}


class ExceptionRecord(ct.Structure):
    _fields_ = [('code', wt.DWORD), ('flags', wt.DWORD), ('record', ct.c_void_p),
                ('address', ct.c_void_p), ('count', wt.DWORD), ('params', ct.c_size_t*15)]


class ExceptionInfo(ct.Structure):
    _fields_ = [('record', ExceptionRecord), ('first_chance', wt.DWORD)]


class DebugString(ct.Structure):
    _fields_ = [('pointer', ct.c_void_p), ('unicode', wt.WORD), ('length', wt.WORD)]


class EventData(ct.Union):
    _fields_ = [('exception', ExceptionInfo), ('string', DebugString),
                ('handle', wt.HANDLE), ('exit_code', wt.DWORD), ('raw', ct.c_byte*160)]


class DebugEvent(ct.Structure):
    _fields_ = [('kind', wt.DWORD), ('pid', wt.DWORD), ('tid', wt.DWORD), ('data', EventData)]


def api():
    if ct.sizeof(ct.c_void_p) != 8:
        raise RuntimeError('This diagnostic requires the project\'s 64-bit Python')
    k = ct.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'DebugActiveProcess': ([wt.DWORD], wt.BOOL),
        'DebugActiveProcessStop': ([wt.DWORD], wt.BOOL),
        'DebugSetProcessKillOnExit': ([wt.BOOL], wt.BOOL),
        'WaitForDebugEventEx': ([ct.POINTER(DebugEvent), wt.DWORD], wt.BOOL),
        'ContinueDebugEvent': ([wt.DWORD, wt.DWORD, wt.DWORD], wt.BOOL),
        'CloseHandle': ([wt.HANDLE], wt.BOOL),
        'OpenThread': ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
        'Wow64GetThreadContext': ([wt.HANDLE, ct.c_void_p], wt.BOOL),
    }
    for name, (args, result) in signatures.items():
        getattr(k, name).argtypes, getattr(k, name).restype = args, result
    return k


def checked(ok, operation):
    if not ok:
        raise OSError(f'{operation} failed (Windows error {ct.get_last_error()})')


def write_dump(memory, path):
    dbg = ct.WinDLL('dbghelp', use_last_error=True)
    fn = dbg.MiniDumpWriteDump
    fn.argtypes = [wt.HANDLE, wt.DWORD, wt.HANDLE, wt.DWORD, ct.c_void_p, ct.c_void_p, ct.c_void_p]
    fn.restype = wt.BOOL
    # Target threads are suspended by the debug event. Keep the exception in the
    # adjacent JSON and capture the stopped threads in the dump (no cross-bitness
    # EXCEPTION_POINTERS passed to DbgHelp). Include globals and referenced memory.
    # DbgHelp requires QUERY_INFORMATION, beyond the adapter's usual limited
    # query handle. Still only request read/query access, never VM_WRITE.
    process = memory.api.OpenProcess(0x410, False, memory.pid)
    checked(process, 'OpenProcess for dump')
    try:
        with path.open('xb') as stream:
            checked(fn(process, memory.pid, msvcrt.get_osfhandle(stream.fileno()),
                       0x1961, None, None, None), 'MiniDumpWriteDump')
    finally:
        memory.api.CloseHandle(process)


def registers(k, tid):
    thread = k.OpenThread(0x48, False, tid)
    checked(thread, 'OpenThread')
    try:
        context = ct.create_string_buffer(716)  # WOW64_CONTEXT, CONTEXT_FULL.
        struct.pack_into('<I', context, 0, 0x10007)
        checked(k.Wow64GetThreadContext(thread, context), 'Wow64GetThreadContext')
        return {name: struct.unpack_from('<I', context, offset)[0] for name, offset in
                zip(('edi','esi','ebx','edx','ecx','eax','ebp','eip','eflags','esp'),
                    (156,160,164,168,172,176,180,184,192,196))}
    finally:
        k.CloseHandle(thread)


def tt_state(memory, addresses):
    a = bindings(addresses)
    result = telemetry(memory, addresses)
    result.update(renderer_index=memory.u32(a['index']),
                  renderer_pointers=[memory.u32(a['renderers']+4*i) for i in range(8)],
                  hidden=memory.read(a['hidden'], 1)[0], render_callback=memory.u32(a['slot']))
    try:
        result['hud_guard'] = NativeHUDGuard(memory, addresses).snapshot()
    except OSError as error:
        result['hud_guard_error'] = str(error)
    try:
        result['load_guard'] = NativeLoadGuard(memory, addresses).snapshot()
    except OSError as error:
        result['load_guard_error'] = str(error)
    return result


def capture(memory, output, seconds, *, state=lambda: {}, get_registers=registers):
    """Debugger loop; always continue pending events and detach on early exit."""
    output.mkdir(parents=True, exist_ok=False)
    k = api()
    log = (output/'events.jsonl').open('a', encoding='utf-8', buffering=1)
    attached, exited = False, False
    seen, saved = set(), set()
    attach_breakpoints = {0x80000003, 0x4000001F}
    last_state, next_sample = None, 0
    def record(kind, **values):
        log.write(json.dumps(dict(time=datetime.now(timezone.utc).isoformat(), kind=kind, **values))+'\n')
    def snapshot():
        try: return state()
        except (OSError, ValueError) as error: return {'read_error': str(error)}
    try:
        checked(k.DebugActiveProcess(memory.pid), 'DebugActiveProcess')
        attached = True
        checked(k.DebugSetProcessKillOnExit(False), 'DebugSetProcessKillOnExit')
        record('attached', pid=memory.pid, modules=[dict(name=m.name, base=m.base, size=m.size)
                                                  for m in memory.modules])
        print(f'Crash capture attached to process {memory.pid}. Reproduce the save-load crash now.', flush=True)
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            event = DebugEvent()
            if not k.WaitForDebugEventEx(ct.byref(event), 100):
                if ct.get_last_error() not in (0, 121):
                    checked(False, 'WaitForDebugEventEx')
            else:
                status = NOT_HANDLED if event.kind == 1 else CONTINUE
                try:
                    if event.kind == 1:
                        info = event.data.exception
                        code, address = info.record.code, info.record.address or 0
                        initial = (bool(info.first_chance) and code in attach_breakpoints and any(
                            m.name.lower() == 'ntdll.dll' and m.base <= address < m.base+m.size
                            for m in memory.modules))
                        if initial:
                            attach_breakpoints.remove(code)
                            status = CONTINUE  # Only the OS-created attach breakpoints.
                        else:
                            key = (code, address, bool(info.first_chance))
                            if key not in seen:
                                seen.add(key)
                                record('exception', thread=event.tid, code=hex(code), address=hex(address),
                                       first_chance=bool(info.first_chance),
                                       params=list(info.record.params)[:min(info.record.count, 15)])
                            if (not info.first_chance or code in FAULTS) and key not in saved:
                                # Bound first-chance dumps; always preserve a new second-chance fault.
                                if len(saved) < 6 or not info.first_chance:
                                    saved.add(key)
                                    stem = f'fault-{len(saved):02d}-{code:08x}-{address:x}'
                                    report = dict(code=hex(code), address=hex(address), thread=event.tid,
                                                  first_chance=bool(info.first_chance),
                                                  params=list(info.record.params)[:min(info.record.count,15)],
                                                  state=snapshot())
                                    try: report['registers'] = get_registers(k, event.tid)
                                    except OSError as error: report['register_error'] = str(error)
                                    (output/(stem+'.json')).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
                                    try:
                                        write_dump(memory, output/(stem+'.dmp'))
                                        record('dump_saved', file=stem+'.dmp')
                                        print(f'Captured {stem}', flush=True)
                                    except OSError as error: record('dump_error', error=str(error))
                    elif event.kind in (3, 6):
                        # CREATE_PROCESS and LOAD_DLL file handles are debugger-owned.
                        if event.data.handle: k.CloseHandle(event.data.handle)
                    elif event.kind == 8:
                        s = event.data.string
                        try:
                            raw = memory.read(s.pointer, min(s.length*(2 if s.unicode else 1), 2048))
                            record('debug_string', text=raw.decode('utf-16-le' if s.unicode else 'utf-8', errors='replace').rstrip('\0'))
                        except (OSError, ValueError): pass
                    elif event.kind == 5:
                        exited = True
                        record('process_exit', code=hex(event.data.exit_code))
                        print(f'Game exited with code {event.data.exit_code:#x}. Evidence: {output}', flush=True)
                finally:
                    checked(k.ContinueDebugEvent(event.pid, event.tid, status), 'ContinueDebugEvent')
                if exited: return
            if time.monotonic() >= next_sample:
                current = snapshot()
                # Save transitions immediately plus periodic counters; no memory writes.
                if current != last_state:
                    record('state', **current)
                    last_state = current
                next_sample = time.monotonic()+1
    finally:
        try:
            if attached and not exited:
                checked(k.DebugActiveProcessStop(memory.pid), 'DebugActiveProcessStop')
                record('detached')
        finally:
            log.close()


def find_tt(game_dir, pid=None):
    for candidate in [pid] if pid is not None else candidates():
        memory = ProcessMemory(candidate)
        selected = False
        try:
            game = memory.modules[0]
            if game.name.lower() != 'game2.exe' or game.path.resolve().parent != game_dir.resolve(): continue
            mods = {m.name.lower():m for m in memory.modules}
            for name, digest in HASHES.items():
                if name not in mods: raise RuntimeError('Wait for TT to finish starting')
                if mods[name].path.resolve().parent != game_dir.resolve():
                    raise RuntimeError('A TT module belongs to another installation')
                require_digest(mods[name].path, digest)
            a = TTAddresses(mods['bandtest.dll'].base, mods['scripts.dll'].base)
            verify_live(memory, a)
            selected = True
            return memory, a
        finally:
            if not selected: memory.close()
    raise RuntimeError('Start the isolated TT game and wait for its main menu')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pid',type=int)
    p.add_argument('--game-dir',type=Path,default=Path('D:/Renegade AP Testing/TT-4.8.4'))
    p.add_argument('--seconds',type=int,default=900)
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    if not 1 <= args.seconds <= 3600: p.error('--seconds must be between 1 and 3600')
    out=args.output or ROOT/'.research/tt-4.8.4'/('capture-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
    mem=None
    try:
        mem, addresses=find_tt(args.game_dir,args.pid)
        capture(mem,out,args.seconds,state=lambda:tt_state(mem,addresses))
    except KeyboardInterrupt:
        print('Capture stopped; game left running.')
    except (OSError, ValueError, RuntimeError) as error:
        print(f'ERROR: {error}',file=sys.stderr)
        return 1
    finally:
        if mem: mem.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

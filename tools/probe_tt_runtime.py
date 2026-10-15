"""Read-only observations for the pinned TT 4.8.4 build; NOT an AP adapter.

Addresses were checked against the official rev9000 binaries, public scripts
source and a live menu. Only inspect these fields after checking every owning
binary and the command-initialization anchor. Never use this as hook permission.
"""
import struct

HASHES = {
    'game2.exe': '1325ed64b91c023caf6a96f35654156e0ef277069f6d1da580c258bb459a809e',
    'bandtest.dll': 'd520443f5618b7d34d48a2e1d4b8348516d53d3ac9a7e201077e259f64c100db',
    'scripts.dll': '65ed3a90adb147d161b229b91915c519d41ad7949ed89bbb9a950f3813461666',
    'scripts2.dll': 'a0439b3bfe6cb497f402291548a312a9f43c0d38bdaf26c83e21dbd3f4d55702',
}
COMMANDS_POINTER = 0x2E690C  # scripts!Set_Script_Commands stores here.
COMMANDS_TABLE = 0x314080   # BandTest owns the live table, not game2.
MISSION_COMPLETE = 0x32A110


def inspect(memory, digest):
    result = dict(build='TT 4.8.4 rev9000', native_adapter_supported=False,
                  memory_written=False, layout_verified=False)
    modules = {module.name.lower(): module for module in memory.modules}
    if memory.modules[0].name.lower() != 'game2.exe':
        return {**result, 'status': 'not-tt-game2'}
    missing = HASHES.keys() - modules.keys()
    if missing:
        return {**result, 'status': 'modules-not-loaded', 'missing': sorted(missing)}
    root = memory.modules[0].path.resolve().parent
    mismatches = [name for name, expected in HASHES.items()
                  if modules[name].path.resolve().parent != root or digest(modules[name].path) != expected]
    if mismatches:
        return {**result, 'status': 'unrecognized-binaries', 'mismatches': mismatches}
    game, scripts, tt = (modules[name] for name in ('game2.exe', 'scripts.dll', 'bandtest.dll'))
    try:
        initializer = bytes.fromhex('83ec08558b6c24108b4500a3') + struct.pack('<I', scripts.base + COMMANDS_POINTER)
        if memory.read(scripts.base + 0x17C70, len(initializer)) != initializer:
            raise ValueError('TT command initializer has changed')
        table = memory.u32(scripts.base + COMMANDS_POINTER)
        if table != tt.base + COMMANDS_TABLE:
            raise ValueError('TT command table owner/address has changed')
        if memory.read(table, 8) != struct.pack('<II', 0x330, 174):
            raise ValueError('TT command table size/version has changed')
        victory = memory.u32(table + 0x1AC)
        if victory != game.base + MISSION_COMPLETE:
            raise ValueError('TT mission callback has changed')
        if memory.read(victory, 12) != bytes.fromhex('8b44240450e86672f4ff59c3'):
            raise ValueError('TT mission callback code has changed')
        paths = {}
        for name, offsets in {
            'scripts.dll': (0x2B33D0, 0x2B34D8, 0x2B35E0),
            'bandtest.dll': (0x31FA40, 0x31FB48, 0x31FC50),
        }.items():
            values = {}
            for field, rva in zip(('registry', 'file_path', 'app_data_path'), offsets):
                raw = memory.read(modules[name].base + rva, 260)
                if b'\0' not in raw:
                    raise ValueError(f'TT {name} {field} is not terminated')
                values[field] = raw.split(b'\0', 1)[0].decode('latin-1')
            paths[name] = values
        result.update(status='diagnostic-layout-verified-no-adapter', layout_verified=True,
                      commands=dict(owner='bandtest.dll', rva=hex(COMMANDS_TABLE), size=0x330, version=174),
                      mission_callback=dict(owner='game2.exe', rva=hex(MISSION_COMPLETE)), paths=paths)
    except (OSError, ValueError) as error:
        result.update(status='live-layout-mismatch', reason=str(error))
    return result

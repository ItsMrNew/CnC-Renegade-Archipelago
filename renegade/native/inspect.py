"""Read-only native validation and telemetry. No hook installation here."""
from dataclasses import asdict
from .memory import MemoryError, ProcessMemory, candidates
from .profile import STEAM, UnsupportedBuild, require_digest


def verify(memory, allow_hook=None):
    game = memory.modules[0]
    require_digest(game.path, STEAM.game_sha256)
    # This stock image has absolute addresses and no relocation table.
    if game.base != 0x400000:
        raise UnsupportedBuild("Unexpected image base for this stock executable")
    scripts = next((m for m in memory.modules if m.name.lower() == "scripts.dll"), None)
    if scripts is None:
        raise MemoryError("Scripts.dll is not loaded; wait for the game menu")
    require_digest(scripts.path, STEAM.scripts_sha256)
    for rva, expected in STEAM.anchors.items():
        if memory.read(game.base + rva, len(expected)) != expected:
            raise UnsupportedBuild(f"Live executable bytes differ at RVA {rva:#x}")
    table = game.base + STEAM.commands
    if (memory.u32(table) != STEAM.commands_size or memory.u32(table + 4) != STEAM.commands_version
            or memory.u32(scripts.base + STEAM.scripts_commands) != table):
        raise UnsupportedBuild("Live script command table does not match the Steam profile")
    expected_callback = game.base + STEAM.mission_complete
    actual = memory.u32(table + STEAM.mission_command_offset)
    if actual != expected_callback and actual != allow_hook:
        raise UnsupportedBuild("Mission callback was changed by another hook; restart the game before attaching")
    return game


def telemetry(memory):
    base = memory.modules[0].base
    mode = memory.u32(base + STEAM.game_mode)
    game_data = memory.u32(base + STEAM.game_data)
    map_name = None
    source_name = None
    if game_data:
        text = memory.u32(game_data + STEAM.map_offset)
        if text:
            # Read a byte at a time so a short string cannot cross a page boundary.
            raw = bytearray()
            for offset in range(64):
                value = memory.read(text + offset, 1)[0]
                if value == 0:
                    break
                raw.append(value)
            else:
                raise MemoryError("Game map name is not terminated")
            source_name = raw.decode("ascii")
        loaded = memory.u32(base + STEAM.loaded_map)
        if loaded:
            raw = bytearray()
            for offset in range(64):
                value = memory.read(loaded + offset, 1)[0]
                if not value:
                    break
                raw.append(value)
            else:
                raise MemoryError("Loaded map name is not terminated")
            map_name = raw.decode("ascii")
    return {"pid": memory.pid, "mode": mode, "single_player": mode == 1,
            "map_name": map_name, "source_name": source_name, "game_data": hex(game_data),
            "pending_campaign_continue": bool(memory.read(base + STEAM.pending_continue, 1)[0])}


def diagnose(pid=None):
    reports = []
    for candidate in [pid] if pid is not None else candidates():
        memory = None
        try:
            memory = ProcessMemory(candidate)
            game = verify(memory)
            reports.append({"supported": True, "game_path": str(game.path), **telemetry(memory)})
        except (OSError, ValueError) as error:
            reports.append({"pid": candidate, "supported": False, "error": str(error)})
        finally:
            if memory:
                memory.close()
    return {"profile": asdict(STEAM), "read_only": True, "processes": reports}

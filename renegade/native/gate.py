"""Experimental gameplay gate: hold locked campaign maps at the combat update.

The AP client shows a native lock popup and returns to the menu on dismissal.
The standalone pause test instead keeps Combat_Keyboard active. Loading is not
intercepted. A stale client lease fails open to avoid stranding the game.
"""
import asyncio
import logging

from ..data import MISSIONS
from .memory import MemoryError, ProcessMemory, candidates
from .profile import STEAM, UnsupportedBuild, require_digest
from .thunk import Code, u32, CODE_OFFSET, LEASE_MS, ALLOWED, HEARTBEAT, MAGIC, steam_addresses

LOG = logging.getLogger("Renegade.Gate")
BLOCKED = 8
BLOCKED_FRAMES = 20
ALLOWED_FRAMES = 24
NOTICE_STATE = 28  # 0 = idle, 1 = popup open, 2 = exiting (reentry guard)
MENU_RETURNS = 32
NOTICES_SHOWN = 36
TITLE = 128
MESSAGE = 256
LOCK_TITLE = "Archipelago - Mission locked"
LOCK_MESSAGE = ("This mission has not been unlocked in Archipelago.\n\n"
                "Receive the required mission unlock item to play it.\n\n"
                "Select OK to return to the main menu.")


def build_gate(control, base=0x400000, return_to_menu=False, forward_target=None, *, addresses=None, transition_target=None):
    addresses = addresses or steam_addresses(base)
    code = Code(control + CODE_OFFSET)
    code.emit(b"\x9c\x60")  # Save caller's flags/registers, including this in ECX.
    if return_to_menu:
        code.emit(b"\x83\x3d" + u32(control + NOTICE_STATE) + b"\x02")
        code.jump(b"\x0f\x84", "hold")  # End_Game can reenter GameModeManager::Think.
    if transition_target:
        code.jump(b'\xe8', transition_target)
        code.emit(b'\x85\xc0'); code.jump(b'\x0f\x85', 'hold')
        code.emit(b'\x8b\x4c\x24\x18')
    code.emit(b"\xc7\x05" + u32(control + BLOCKED) + u32(0))
    code.emit(b"\x83\x79\x04\x00")  # GameMode::State == ACTIVE (0)
    code.jump(b"\x0f\x85", "forward")
    code.emit(b"\x83\x3d" + u32(control + ALLOWED) + b"\x00")
    code.jump(b"\x0f\x84", "forward")  # Disarmed, not a zero-mission inventory.
    code.emit(b"\xff\x15" + u32(addresses.tick_iat))
    code.emit(b"\x2b\x05" + u32(control + HEARTBEAT))
    code.emit(b"\x3d" + u32(LEASE_MS))
    code.jump(b"\x0f\x87", "forward")
    code.emit(b"\x83\x3d" + u32(addresses.game_mode) + b"\x01")
    code.jump(b"\x0f\x85", "forward")
    code.emit(b"\xa1" + u32(addresses.game_data))
    code.emit(b"\x85\xc0")
    code.jump(b"\x0f\x84", "forward")
    code.emit(b"\x8b\x15" + u32(addresses.loaded_map))
    code.emit(b"\x85\xd2")
    code.jump(b"\x0f\x84", "forward")
    # Use the loaded level metadata, never infer a mission from a save filename.
    # Byte comparisons avoid reading past short NUL-terminated names.
    for index, mission in enumerate(MISSIONS):
        for suffix in (".lsd", ".mix"):
            label = f"next{index}{suffix}"
            for offset, char in enumerate((mission.map_name.lower() + suffix).encode() + b"\0"):
                code.emit(b"\x8a\x42" + bytes([offset]))
                if 97 <= char <= 122:
                    code.emit(b"\x0c\x20")
                code.emit(b"\x3c" + bytes([char]))
                code.jump(b"\x0f\x85", label)
            code.emit(b"\xb9" + u32(1 << index))
            code.jump(b"\xe9", "decide")
            code.label(label)
    code.jump(b"\xe9", "forward")
    code.label("decide")
    if return_to_menu:
        code.emit(b"\x83\x3d" + u32(control + NOTICE_STATE) + b"\x01")
        code.jump(b"\x0f\x85", "permission")
        code.emit(b"\x83\x3d" + u32(addresses.message_count) + b"\x00")
        code.jump(b"\x0f\x85", "blocked")  # Keep frozen until the visible popup closes.
        code.label("permission")
    code.emit(b"\x85\x0d" + u32(control + ALLOWED))
    code.jump(b"\x0f\x85", "allowed")
    code.label("blocked")
    code.emit(b"\x89\x0d" + u32(control + BLOCKED))
    code.emit(b"\xf0\xff\x05" + u32(control + BLOCKED_FRAMES))
    if return_to_menu:
        emit_locked_menu(code, control, base, addresses=addresses)
    code.emit(b"\x61\x9d")
    # Tail-call the game's normal menu/help/quicksave input handler with the
    # original this pointer and stack, without simulation or pending victory.
    code.jump(b"\xe9", addresses.combat_keyboard)
    code.label("allowed")
    code.emit(b"\xf0\xff\x05" + u32(control + ALLOWED_FRAMES))
    code.label("forward")
    if return_to_menu:
        code.emit(b"\xc7\x05" + u32(control + NOTICE_STATE) + u32(0))
    code.emit(b"\x61\x9d")
    code.jump(b"\xe9", forward_target or addresses.combat_think)
    if return_to_menu or transition_target:
        code.label("hold")
        code.emit(b"\x61\x9d\xc3")
    result = code.finish()
    if len(result) > 4096:
        raise ValueError("Gate exceeds its code page")
    return result


def emit_locked_menu(code, control, base, *, addresses=None):
    addresses = addresses or steam_addresses(base)
    # A dialog already present must finish before ours is created. CurrentCount
    # includes dialogs awaiting destruction, so dismissal is observed next frame.
    code.emit(b"\x83\x3d" + u32(addresses.message_count) + b"\x00")
    code.jump(b"\x0f\x85", "hold")
    code.emit(b"\x83\x3d" + u32(control + NOTICE_STATE) + b"\x01")
    code.jump(b"\x0f\x84", "return_menu")
    code.emit(b"\xc7\x05" + u32(control + NOTICE_STATE) + u32(1))
    code.emit(b"\xfc")  # Engine C++ calls require a clear direction flag.
    # cdecl DoDialog(title, text, Okay=0, observer=null, user_data=0).
    code.emit(b"\x6a\x00\x6a\x00\x6a\x00")
    code.emit(b"\x68" + u32(control + MESSAGE) + b"\x68" + u32(control + TITLE))
    code.jump(b"\xe8", addresses.message_dialog)
    code.emit(b"\x83\xc4\x14\x84\xc0")
    code.jump(b"\x0f\x84", "dialog_failed")
    code.emit(b"\xff\x05" + u32(control + NOTICES_SHOWN))
    code.jump(b"\xe9", "hold")
    code.label("dialog_failed")
    code.emit(b"\xc7\x05" + u32(control + NOTICE_STATE) + u32(0))
    code.jump(b"\xe9", "hold")
    code.label("return_menu")
    code.emit(b"\xc7\x05" + u32(control + NOTICE_STATE) + u32(2))
    code.emit(b"\xfc")
    # Match the vanilla quit path: Suspend(this), End_Game, Display_End_Game_Menu.
    code.emit(b"\x8b\x4c\x24\x18\x8b\x01\xff\x50\x10")
    code.emit(b"\xc6\x05" + u32(addresses.pending_continue) + b"\x00")
    code.jump(b"\xe8", addresses.end_game)
    code.jump(b"\xe8", addresses.end_game_menu)
    code.emit(b"\xc7\x05" + u32(control + BLOCKED) + u32(0))
    code.emit(b"\xff\x05" + u32(control + MENU_RETURNS))
    code.emit(b"\xc7\x05" + u32(control + NOTICE_STATE) + u32(0))
    code.jump(b"\xe9", "hold")


def verify_gate(memory, allow_hook=None, return_to_menu=False):
    game = memory.modules[0]
    require_digest(game.path, STEAM.game_sha256)
    if game.base != 0x400000:
        raise UnsupportedBuild("Unexpected image base for Steam gameplay gate")
    anchors = STEAM.gate_anchors | (STEAM.menu_anchors if return_to_menu else {})
    for rva, expected in anchors.items():
        if memory.read(game.base + rva, len(expected)) != expected:
            raise UnsupportedBuild(f"Gameplay gate anchor differs at RVA {rva:#x}")
    actual = memory.u32(game.base + STEAM.combat_think_slot)
    if actual != game.base + STEAM.combat_think and actual != allow_hook:
        raise UnsupportedBuild("Combat update was changed by another hook; restart Renegade")
    return game


class GameplayGate:
    def __init__(self, memory, return_to_menu=False, forward_target=None, *, tt=False, transition_target=None):
        self.memory = memory
        self.return_to_menu = return_to_menu
        if tt:
            from .tt_profile import verify as verify_tt
            self.addresses = verify_tt(memory, gate=True)
            self.game = memory.modules[0]
            self.field = self.addresses.combat_think_slot
        else:
            self.game = verify_gate(memory, return_to_menu=return_to_menu)
            self.addresses = steam_addresses(self.game.base)
            self.field = self.game.base + STEAM.combat_think_slot
        self.original = self.addresses.combat_think
        self.forward_target = forward_target
        self.transition_target = transition_target
        self.installed = False
        self.control = self.code = None

    def install(self):
        if self.installed:
            raise MemoryError("Gameplay gate already installed")
        self.control = self.memory.allocate()
        self.code = self.control + CODE_OFFSET
        self.memory.write(self.control, b"\0" * 12 + MAGIC + b"\0" * 8)
        self.memory.write(self.control + NOTICE_STATE, b"\0" * 12)
        if self.return_to_menu:
            self.memory.write(self.control + TITLE, (LOCK_TITLE + "\0").encode("utf-16-le"))
            self.memory.write(self.control + MESSAGE, (LOCK_MESSAGE + "\0").encode("utf-16-le"))
        program = build_gate(self.control, self.game.base, self.return_to_menu, self.forward_target,
                             addresses=self.addresses, transition_target=self.transition_target)
        self.memory.write(self.code, program)
        self.memory.make_executable(self.code)
        if self.memory.read(self.code, len(program)) != program:
            raise MemoryError("Gameplay gate code failed read-back verification")
        self.installed = True  # Cleanup must cover uncertain writes.
        try:
            self.memory.replace_readonly_u32(self.field, self.original, self.code)
        except BaseException:
            self.close()
            raise

    def update(self, unlocked_maps):
        if not self.installed or self.memory.u32(self.field) != self.code:
            raise MemoryError("Gameplay gate lost ownership")
        mask = sum(1 << i for i, m in enumerate(MISSIONS) if m.map_name in unlocked_maps)
        if mask:
            self.memory.write_u32(self.control + HEARTBEAT, self.memory.tick_count())
        self.memory.write_u32(self.control + ALLOWED, mask)
        return self.snapshot()

    def snapshot(self):
        mask = self.memory.u32(self.control + BLOCKED)
        if mask & ~0xFFF or mask.bit_count() > 1:
            raise MemoryError("Corrupt gameplay gate state")
        return {
            "blocked_map": next((m.map_name for i, m in enumerate(MISSIONS) if mask & (1 << i)), None),
            "blocked_frames": self.memory.u32(self.control + BLOCKED_FRAMES),
            "allowed_frames": self.memory.u32(self.control + ALLOWED_FRAMES),
            "notices_shown": self.memory.u32(self.control + NOTICES_SHOWN),
            "menu_returns": self.memory.u32(self.control + MENU_RETURNS),
        }

    def close(self):
        if self.installed:
            try:
                self.memory.write_u32(self.control + ALLOWED, 0)
            finally:
                if self.memory.u32(self.field) == self.code:
                    self.memory.replace_readonly_u32(self.field, self.code, self.original)
                else:
                    LOG.warning("Combat update ownership changed; leaving its pointer untouched")
                self.installed = False
        # Keep the allocation until game exit: another thread may already have
        # fetched the old function pointer. Lease expiry also makes crashes safe.


def attach_gate(pid=None, return_to_menu=False):
    errors = []
    for candidate in [pid] if pid is not None else candidates():
        memory = None
        try:
            memory = ProcessMemory(candidate)
            verify_gate(memory, return_to_menu=return_to_menu)
            memory.close()
            memory = ProcessMemory(candidate, writable=True)
            gate = GameplayGate(memory, return_to_menu=return_to_menu)
            gate.install()
            return gate
        except (OSError, ValueError) as error:
            errors.append(str(error))
            if memory:
                memory.close()
    if errors:
        raise MemoryError("; ".join(errors))
    return None


async def watch_gate(client, pid=None, status=None):
    def report(state, detail):
        if status:
            status(state, detail)
    report("waiting", "Waiting for connection and the supported game")
    gate = None
    identity = None
    last_blocked = None
    last_error = None
    try:
        while not client.stop.is_set():
            try:
                if identity is not None and client.identity != identity:
                    raise ValueError("Refusing to reuse gameplay permissions for another AP room")
                if gate is None and client.ready:
                    gate = attach_gate(pid, return_to_menu=True)
                    if gate:
                        identity = client.identity
                        LOG.info("Mission locks attached to process %d", gate.memory.pid)
                if gate:
                    state = gate.update(client.unlocked_maps if client.ready else ())
                    blocked = state["blocked_map"]
                    report("attached" if client.ready else "idle",
                           f"{blocked} locked" if blocked and client.ready else
                           ("Mission locks active" if client.ready else "Disconnected; gate released"))
                    if blocked != last_blocked:
                        if blocked:
                            LOG.warning("%s is locked: combat paused; an in-game message explains the lock. Dismiss it to return to the main menu.", blocked)
                        elif last_blocked:
                            LOG.info("Gameplay gate released %s (unlocked, menu, or inactive client)", last_blocked)
                        last_blocked = blocked
                last_error = None
                await asyncio.sleep(0.1 if gate else 1)
            except OSError as error:
                report("waiting", str(error))
                if str(error) != last_error:
                    LOG.warning("%s", error)
                    last_error = str(error)
                if gate:
                    try:
                        gate.close()
                    finally:
                        gate.memory.close()
                        gate = None
                await asyncio.sleep(3)
    finally:
        if gate:
            try:
                gate.close()
                LOG.info("Original combat update restored")
            finally:
                gate.memory.close()
        report("stopped", "Gameplay gate stopped")

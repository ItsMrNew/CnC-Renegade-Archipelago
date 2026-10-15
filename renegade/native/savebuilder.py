"""Scoped, game-thread-only mission-start save builder for the pinned Steam build.

No remote thread or general command execution. The only jobs are campaign load,
native save, native save reload, and return to menu. An explicit completion-test
opt-in adds a guarded call through the mission callback. Both callbacks are restored.
"""
import time
from .profile import STEAM, UnsupportedBuild, require_digest
from .gate import verify_gate
from .memory import MemoryError, ProcessMemory, candidates
from .thunk import Code, u32, LEASE_MS
from ..data import MISSIONS

BASE = 0x400000
MENU_THINK = 0x2A920
MENU_SLOT = 0x3EE3B8
INIT_SP = 0x50F0
REPLAY = 0x1FC90
START_GAME = 0x4690
SAVE_GAME = 0x122640
COMMANDO_SAVE = 0x442F08
CAMPAIGN_STATE = 0x4446B0
FLOW_COUNT = 0x4446A8
FLOW_POINTER = 0x44469C
BACKDROP_STATE = 0x4446B4
BACKDROP_COUNT = 0x444690
BACKDROP_POINTER = 0x444684
STAR = 0x4566F0
AUTOSAVE = 0x45679A

REQUEST, BUSY, HEARTBEAT, RESULT, FRAMES, FREEZE = range(0, 24, 4)
LEVEL_STATE, BACKDROP, STAR_RESULT = 24, 28, 32
EXPECTED_CALLBACK = 36
DIFFICULTY = 40  # Builder request, not the game's stored difficulty.
GAME_DIFFICULTY = 0x421704  # CombatManager::DifficultyLevel, anchored by REPLAY.
DIFFICULTIES = {0: "Recruit", 1: "Soldier", 2: "Commando"}
MAP, FILENAME = 128, 256
LOAD, SAVE, EXIT, RELOAD = 1, 2, 3, 4
COMPLETE = 5  # Explicit test-only opt-in; never enabled by the save-pack builder.

ANCHORS = {
    MENU_THINK: bytes.fromhex("568bf18b460885c0746d8b06ff5020"),
    INIT_SP: bytes.fromhex("51a1242f84005333db3bc30f848b000000"),
    REPLAY: bytes.fromhex("c705b0468400f5ffffffe8c17bfeff8b4c24048b4424086a006aff51a304178200e8da49feff83c40cc3"),
    SAVE_GAME: bytes.fromhex("81ec140c0000538b9c241c0c00005657"),
    0x1F875: bytes.fromhex("8b0da8468400493bc10f8da40300008b159c46840040a3b0468400bfac9e8100"),
    0x35EE: bytes.fromhex("8b178bcfff522084c00f84c7020000"),  # Think after Combat_Keyboard
    0x365F: bytes.fromhex("a1f066850085c074748b480485c9746d"),  # TheStar reference + object
    STEAM.end_game: STEAM.menu_anchors[STEAM.end_game],
    STEAM.end_game_menu: STEAM.menu_anchors[STEAM.end_game_menu],
}


def build_dispatcher(control, address, original, combat, completion_test=False):
    c = Code(address)
    c.emit(b"\x9c\x60")
    c.emit(b"\x83\x3d" + u32(control + BUSY) + b"\x00")
    c.jump(b"\x0f\x85", "hold")  # Loading/shutdown can reenter mode processing.
    c.emit(b"\xff\x15" + u32(BASE + STEAM.tick_iat))
    c.emit(b"\x2b\x05" + u32(control + HEARTBEAT) + b"\x3d" + u32(LEASE_MS))
    c.jump(b"\x0f\x87", "forward")
    c.emit(b"\xa1" + u32(control + REQUEST))
    c.emit(b"\x85\xc0")
    c.jump(b"\x0f\x84", "idle")
    c.emit(b"\x83\xf8" + bytes([5 if completion_test else 4]))
    c.jump(b"\x0f\x87", "forward")
    c.emit(b"\xc7\x05" + u32(control + BUSY) + u32(1))
    c.emit(b"\xc7\x05" + u32(control + REQUEST) + u32(0))
    c.emit(b"\xfc")
    if completion_test:
        c.emit(b"\x83\xf8\x05")
        c.jump(b"\x0f\x84", "complete")
    c.emit(b"\x83\xf8\x01")
    c.jump(b"\x0f\x84", "load")
    c.emit(b"\x83\xf8\x02")
    c.jump(b"\x0f\x84", "save")
    c.emit(b"\x83\xf8\x03")
    c.jump(b"\x0f\x84", "exit_game")
    # Reload a generated native save through the normal game loader.
    c.jump(b"\xe8", BASE + INIT_SP)
    c.emit(b"\x6a\x00\x6a\xff\x68" + u32(control + FILENAME))
    c.jump(b"\xe8", BASE + START_GAME)
    c.emit(b"\x83\xc4\x0c")
    c.jump(b"\xe9", "loaded")
    c.label("load")
    c.emit(b"\x83\x3d" + u32(control + DIFFICULTY) + b"\x02")
    c.jump(b"\x0f\x87", "failed")
    c.jump(b"\xe8", BASE + INIT_SP)
    c.emit(b"\xff\x35" + u32(control + DIFFICULTY) + b"\x68" + u32(control + MAP))
    c.jump(b"\xe8", BASE + REPLAY)
    c.emit(b"\x83\xc4\x08")
    # Replay supplies a fresh mission loadout, but the saved campaign state
    # must be its real Level entry so victory continues the normal campaign.
    c.emit(b"\xa1" + u32(control + LEVEL_STATE) + b"\xa3" + u32(BASE + CAMPAIGN_STATE))
    c.emit(b"\xa1" + u32(control + BACKDROP) + b"\xa3" + u32(BASE + BACKDROP_STATE))
    c.label("loaded")
    c.emit(b"\xc6\x05" + u32(BASE + AUTOSAVE) + b"\x00")
    c.emit(b"\xc7\x05" + u32(control + FRAMES) + u32(0))
    c.emit(b"\xc7\x05" + u32(control + FREEZE) + u32(1))
    c.jump(b"\xe9", "done")
    if completion_test:
        c.label("complete")
        c.emit(b"\x83\x3d" + u32(BASE + STEAM.game_mode) + b"\x01")
        c.jump(b"\x0f\x85", "failed")
        c.emit(b"\x83\x3d" + u32(control + FREEZE) + b"\x01")
        c.jump(b"\x0f\x85", "failed")
        c.emit(b"\xa1" + u32(BASE + STAR) + b"\x85\xc0")
        c.jump(b"\x0f\x84", "failed")
        c.emit(b"\x83\x78\x04\x00")
        c.jump(b"\x0f\x84", "failed")
        c.emit(b"\xa1" + u32(control + EXPECTED_CALLBACK) + b"\x85\xc0")
        c.jump(b"\x0f\x84", "failed")
        c.emit(b"\x3b\x05" + u32(BASE + STEAM.commands + STEAM.mission_command_offset))
        c.jump(b"\x0f\x85", "failed")
        # Call through the real ScriptCommands table, including the AP observer.
        # The original game callback still runs and sets PendingCampaignContinue.
        c.emit(b"\x6a\x01\xff\xd0\x83\xc4\x04")
        c.jump(b"\xe9", "done")
    c.label("save")
    c.emit(b"\xa1" + u32(BASE + STAR) + b"\x85\xc0")
    c.jump(b"\x0f\x84", "failed")
    c.emit(b"\x8b\x40\x04\xa3" + u32(control + STAR_RESULT) + b"\x85\xc0")
    c.jump(b"\x0f\x84", "failed")
    c.emit(b"\x6a\x00\x68" + u32(BASE + COMMANDO_SAVE) + b"\x68" + u32(control + FILENAME))
    c.jump(b"\xe8", BASE + SAVE_GAME)
    c.emit(b"\x83\xc4\x0c")
    c.jump(b"\xe9", "done")
    c.label("exit_game")
    c.emit(b"\xc6\x05" + u32(BASE + STEAM.pending_continue) + b"\x00")
    c.jump(b"\xe8", BASE + STEAM.end_game)
    c.jump(b"\xe8", BASE + STEAM.end_game_menu)
    c.emit(b"\xc7\x05" + u32(control + FREEZE) + u32(0))
    c.jump(b"\xe9", "done")
    c.label("failed")
    c.emit(b"\xc7\x05" + u32(control + RESULT) + u32(2))
    c.jump(b"\xe9", "finish")
    c.label("done")
    c.emit(b"\xc7\x05" + u32(control + RESULT) + u32(1))
    c.label("finish")
    c.emit(b"\xc7\x05" + u32(control + BUSY) + u32(0))
    c.jump(b"\xe9", "hold")
    c.label("idle")
    if combat:
        c.emit(b"\x83\x3d" + u32(control + FREEZE) + b"\x00")
        c.jump(b"\x0f\x84", "forward")
        c.emit(b"\x8b\x4c\x24\x18\x83\x79\x04\x00")
        c.jump(b"\x0f\x85", "forward")
        c.emit(b"\x83\x3d" + u32(control + FRAMES) + b"\x03")
        c.jump(b"\x0f\x82", "startup")
        c.emit(b"\xa1" + u32(BASE + STAR) + b"\x85\xc0")
        c.jump(b"\x0f\x84", "startup")
        c.emit(b"\x83\x78\x04\x00")
        c.jump(b"\x0f\x85", "hold")
        c.label("startup")
        c.emit(b"\xff\x05" + u32(control + FRAMES))
        c.emit(b"\xc6\x05" + u32(BASE + AUTOSAVE) + b"\x00")
        c.emit(b"\x61\x9d")
        # Run the original Think after its keyboard handler. During generation
        # the launcher console may have focus; Combat_Keyboard would suspend
        # the mission before the player spawns. Reproduce the exact stack/frame
        # setup, then continue at the second Is_Active check. No game code patch.
        c.emit(bytes.fromhex("83ec1c578bf9897c240c"))
        c.jump(b"\xe9", BASE + 0x35EE)
    c.label("forward")
    c.emit(b"\x61\x9d")
    c.jump(b"\xe9", BASE + original)
    c.label("hold")
    c.emit(b"\x61\x9d\xc3")
    program = c.finish()
    if len(program) > 2048:
        raise ValueError("Save builder callback too large")
    return program


def cstring(memory, pointer, limit=100):
    raw = bytearray()
    for offset in range(limit):
        ch = memory.read(pointer + offset, 1)
        if ch == b"\0":
            return raw.decode("ascii")
        raw += ch
    raise MemoryError("Unterminated campaign string")


class SaveBuilder:
    def __init__(self, memory, completion_test=False):
        self.memory = memory
        self.completion_test = completion_test
        verify_gate(memory)  # Refuse to run alongside the AP gameplay gate.
        # End_Game can clear Scripts.dll's Commands pointer at the main menu.
        # This builder does not call the script table, so observer readiness is
        # not a prerequisite. Still pin the scripts binary and reject an owned
        # completion callback instead of competing with an AP observer.
        scripts = next((m.path for m in memory.modules if m.name.lower() == "scripts.dll"),
                       memory.modules[0].path.parent / "Scripts.dll")
        require_digest(scripts, STEAM.scripts_sha256)
        callback = memory.u32(BASE + STEAM.commands + STEAM.mission_command_offset)
        if callback not in (0, BASE + STEAM.mission_complete):
            raise UnsupportedBuild("Mission observer is still attached; close the AP client")
        for rva, expected in ANCHORS.items():
            if memory.read(BASE + rva, len(expected)) != expected:
                raise UnsupportedBuild(f"Save builder anchor differs at {rva:#x}")
        if memory.u32(BASE + MENU_SLOT) != BASE + MENU_THINK:
            raise UnsupportedBuild("Menu callback already hooked")
        self.control = None
        self.owned = []
        self.campaign = self.campaign_entries()

    def campaign_entries(self):
        count = self.memory.u32(BASE + FLOW_COUNT)
        array = self.memory.u32(BASE + FLOW_POINTER)
        if not 12 <= count <= 200:
            raise UnsupportedBuild("Campaign flow is not initialized; open the main menu")
        entries = {}
        for index in range(count):
            value = cstring(self.memory, self.memory.u32(array + index * 4))
            if value.startswith("Level "):
                entries[value[6:].upper()] = index
        if any(m.map_name + ".MIX" not in entries for m in MISSIONS):
            raise UnsupportedBuild("Campaign does not contain all twelve stock levels")
        return entries

    def install(self):
        self.control = self.memory.allocate()
        self.memory.write(self.control, b"\0" * 512)
        pairs = [(MENU_SLOT, MENU_THINK, 4096, False),
                 (STEAM.combat_think_slot, STEAM.combat_think, 6144, True)]
        for slot, original, offset, combat in pairs:
            program = build_dispatcher(self.control, self.control + offset, original, combat, self.completion_test)
            self.memory.write(self.control + offset, program)
            if self.memory.read(self.control + offset, len(program)) != program:
                raise MemoryError("Save builder code read-back failed")
        self.memory.make_executable(self.control + 4096)
        try:
            for slot, original, offset, combat in pairs:
                self.owned.append((BASE + slot, BASE + original, self.control + offset))
                self.memory.replace_readonly_u32(BASE + slot, BASE + original, self.control + offset)
        except BaseException:
            self.close()
            raise

    def beat(self):
        for slot, original, code in self.owned:
            if self.memory.u32(slot) != code:
                raise MemoryError("Save builder lost callback ownership")
        self.memory.write_u32(self.control + HEARTBEAT, self.memory.tick_count())

    def wait(self, condition, timeout=180):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                raise TimeoutError("Native save-builder operation timed out")
            self.beat()
            time.sleep(0.05)

    def prepare(self, mission, filename, difficulty=1):
        if type(difficulty) is not int or difficulty not in DIFFICULTIES:
            raise ValueError("Difficulty must be Recruit (0), Soldier (1), or Commando (2)")
        if not filename.startswith("save\\ap_start_") or any(x in filename for x in ("..", "/", ":")):
            raise ValueError("Save builder only writes its own save filenames")
        if len(filename.encode("ascii")) > 100:
            raise ValueError("Save filename too long")
        self.memory.write(self.control + MAP, (mission.map_name + ".mix\0").encode())
        self.memory.write(self.control + FILENAME, (filename + "\0").encode())
        self.memory.write_u32(self.control + DIFFICULTY, difficulty)
        self.memory.write_u32(self.control + LEVEL_STATE, self.campaign[mission.map_name + ".MIX"])
        count = self.memory.u32(BASE + BACKDROP_COUNT)
        array = self.memory.u32(BASE + BACKDROP_POINTER)
        if count > 200:
            raise UnsupportedBuild("Invalid campaign backdrop table")
        backdrop = 0
        for i in range(count):
            if self.memory.u32(array + i * 28) == int(mission.map_name[1:]):
                backdrop = i
        self.memory.write_u32(self.control + BACKDROP, backdrop)

    def job(self, command):
        if command not in (LOAD, SAVE, EXIT, RELOAD) and not (self.completion_test and command == COMPLETE):
            raise ValueError("Unsupported save-builder job")
        if self.memory.u32(self.control + BUSY) or self.memory.u32(self.control + REQUEST):
            raise MemoryError("Save builder already has a pending operation")
        self.beat()
        self.memory.write_u32(self.control + RESULT, 0)
        self.memory.write_u32(self.control + REQUEST, command)
        self.wait(lambda: self.memory.u32(self.control + RESULT) != 0)
        self.wait(lambda: self.memory.u32(self.control + BUSY) == 0)
        if self.memory.u32(self.control + RESULT) != 1:
            raise RuntimeError("Native operation rejected: player or expected callback not available")

    def settle(self):
        self.wait(lambda: self.memory.u32(self.control + FRAMES) >= 3 and self.player_object())

    def player_object(self):
        reference = self.memory.u32(BASE + STAR)
        return self.memory.u32(reference + 4) if reference else 0

    def difficulty_level(self):
        return self.memory.u32(BASE + GAME_DIFFICULTY)

    def close(self):
        if self.control:
            self.memory.write_u32(self.control + REQUEST, 0)
            self.memory.write_u32(self.control + FREEZE, 0)
            self.memory.write_u32(self.control + HEARTBEAT, (self.memory.tick_count() - 10000) & 0xFFFFFFFF)
        errors = []
        for slot, original, code in reversed(self.owned):
            try:
                if self.memory.u32(slot) == code:
                    self.memory.replace_readonly_u32(slot, code, original)
            except OSError as error:
                errors.append(str(error))
        self.owned.clear()
        if errors:
            raise MemoryError("; ".join(errors))
        # Retain allocation until process exit for any callback already fetched.


def attach_builder(pid=None, completion_test=False):
    processes = [pid] if pid else candidates()
    if len(processes) != 1:
        raise RuntimeError("Start exactly one supported Steam Renegade instance")
    memory = ProcessMemory(processes[0], writable=True)
    try:
        builder = SaveBuilder(memory, completion_test=completion_test)
        builder.install()
        return builder
    except BaseException:
        memory.close()
        raise

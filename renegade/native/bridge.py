"""Lifecycle for the native campaign mission observer."""
import asyncio
import logging
from ..data import MISSIONS, mission_for_map
from .inspect import telemetry, verify
from .memory import MemoryError, ProcessMemory, candidates
from .profile import STEAM, UnsupportedBuild
from .thunk import ALLOWED, CODE_OFFSET, COMPLETED, HEARTBEAT, MAGIC, build_thunk, steam_addresses

LOG = logging.getLogger("Renegade.Native")


class MissionHook:
    def __init__(self, memory, *, tt=False):
        self.memory = memory
        if tt:
            from .tt_profile import verify as verify_tt
            self.addresses = verify_tt(memory)
            self.game = memory.modules[0]
            self.callback_field = self.addresses.commands + STEAM.mission_command_offset
        else:
            self.game = verify(memory)
            self.addresses = steam_addresses(self.game.base)
            self.callback_field = self.game.base + STEAM.commands + STEAM.mission_command_offset
        self.control = None
        self.code = None
        self.installed = False
        self.original = self.addresses.mission_complete

    def install(self):
        if self.installed:
            raise MemoryError("Hook is already installed")
        self.control = self.memory.allocate()
        self.code = self.control + CODE_OFFSET
        self.memory.write(self.control, b"\0" * 12 + MAGIC)
        program = build_thunk(self.control, self.game.base, addresses=self.addresses)
        self.memory.write(self.code, program)
        self.memory.make_executable(self.code)
        if self.memory.read(self.code, len(program)) != program:
            raise MemoryError("Bridge code failed read-back verification")
        # Recheck immediately before replacing the single aligned pointer.
        if self.memory.u32(self.callback_field) != self.original:
            raise UnsupportedBuild("Mission callback changed while attaching")
        # Mark intent first so a failed/uncertain write still triggers cleanup.
        self.installed = True
        try:
            self.memory.write_u32(self.callback_field, self.code)
            if self.memory.u32(self.callback_field) != self.code:
                raise MemoryError("Mission callback replacement could not be verified")
        except BaseException:
            self.close()
            raise

    def update(self, unlocked_maps):
        if not self.installed or self.memory.u32(self.callback_field) != self.code:
            raise MemoryError("Mission observer is no longer attached")
        allowed = sum(1 << i for i, m in enumerate(MISSIONS) if m.map_name in unlocked_maps)
        # Disarm first when disconnected. Otherwise renew the lease before arming.
        if not allowed:
            self.memory.write_u32(self.control + ALLOWED, 0)
        else:
            self.memory.write_u32(self.control + HEARTBEAT, self.memory.tick_count())
            self.memory.write_u32(self.control + ALLOWED, allowed)
        return self.captured()

    def captured(self):
        """Read latched events even if a level load already reset the callback."""
        mask = self.memory.u32(self.control + COMPLETED)
        if mask & ~0xFFF:
            raise MemoryError("Corrupt mission observer state")
        return tuple(m.map_name for i, m in enumerate(MISSIONS) if mask & (1 << i))

    def close(self):
        if self.installed:
            try:
                self.memory.write_u32(self.control + ALLOWED, 0)
            finally:
                if self.memory.u32(self.callback_field) == self.code:
                    self.memory.write_u32(self.callback_field, self.original)
                    if self.memory.u32(self.callback_field) != self.original:
                        raise MemoryError("Original mission callback restoration failed; restart Renegade")
                else:
                    LOG.warning("Callback ownership changed; leaving the other pointer untouched")
                self.installed = False
        # Intentionally retain 8 KiB until game exit. An in-flight callback may
        # have loaded the old pointer before restoration. Freeing it races that
        # game thread. A client crash is also safe: lease expiry disables capture
        # and the wrapper always tail-calls the original game function.


def attach(pid=None):
    errors = []
    for candidate in [pid] if pid is not None else candidates():
        memory = None
        try:
            memory = ProcessMemory(candidate)
            verify(memory)
            memory.close()
            memory = ProcessMemory(candidate, writable=True)
            hook = MissionHook(memory)  # Includes a second profile/runtime check.
            hook.install()
            return hook
        except (OSError, ValueError) as error:
            errors.append(str(error))
            if memory:
                memory.close()
    if errors:
        raise MemoryError("; ".join(errors))
    return None


async def watch(client, pid=None, status=None):
    def report(state, detail):
        if status:
            status(state, detail)
    report("waiting", "Waiting for connection and a supported campaign")
    hook = None
    last_error = None
    identity = None
    pending = set()
    last_map = None
    next_telemetry = 0
    try:
        while not client.stop.is_set():
            try:
                if identity is not None and client.identity != identity:
                    raise ValueError("Refusing to reuse captured victories for another AP room")
                if client.capture_ready:
                    for map_name in tuple(pending):
                        try:
                            accepted = client.mission_finished(map_name, True)
                            if accepted or mission_for_map(map_name).check_id() in client.progress.completed:
                                pending.remove(map_name)
                        except OSError as error:
                            LOG.error("Could not save %s; retaining the captured victory for retry: %s", map_name, error)
                if hook is None and client.capture_ready:
                    hook = attach(pid)
                    if hook:
                        identity = client.identity
                        LOG.info("Native mission observer attached to process %d", hook.memory.pid)
                if hook:
                    if client.identity != identity:
                        raise ValueError("Refusing to reuse a native observer for another AP room")
                    # Level loading can rebuild ScriptCommands before our next
                    # poll. Drain our old memory block before checking ownership
                    # so a victory is not discarded during that transition.
                    pending.update(hook.captured())
                    pending.update(hook.update(client.capture_maps))
                    report("attached" if client.ready else "idle",
                           f"Process {hook.memory.pid}" if client.ready else "Reconnecting; checks saved locally")
                    now = asyncio.get_running_loop().time()
                    if now >= next_telemetry:
                        next_telemetry = now + 1
                        try:
                            snapshot = telemetry(hook.memory)
                            current_map = (snapshot["mode"], snapshot["map_name"])
                            if current_map != last_map:
                                LOG.info("Game state: mode=%s, map=%s", *current_map)
                                last_map = current_map
                        except (OSError, ValueError):
                            pass  # A concurrent map load must not discard latched events.
                last_error = None
                await asyncio.sleep(0.1 if hook else 1)
            except OSError as error:
                report("waiting", str(error))
                if str(error) != last_error:
                    LOG.warning("%s", error)
                    last_error = str(error)
                if hook:
                    try:
                        hook.close()
                    except OSError:
                        LOG.warning("Game exited or hook cleanup could not be verified; restart the game before reattaching")
                    finally:
                        hook.memory.close()
                        hook = None
                await asyncio.sleep(3)
    finally:
        try:
            if hook:
                try:
                    hook.close()
                    LOG.info("Original Renegade mission callback restored")
                finally:
                    try:
                        pending.update(hook.captured())
                    finally:
                        hook.memory.close()
        finally:
            from .final_capture import persist_final_captures
            persist_final_captures(client, identity, victories=pending)
        report("stopped", "Observer stopped")

"""Load mission-start saves and force native victories in a disposable AP room.

This validates the native callback/observer/network path, not mission objectives.
Never connects to an existing room or writes client progress outside this run.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import MISSIONS, mission_for_map
from renegade.native.bridge import watch
from renegade.native.inspect import telemetry
from renegade.native.gate import verify_gate
from renegade.native.profile import STEAM
from renegade.native.savebuilder import (attach_builder, BASE, RELOAD, EXIT, COMPLETE,
    BUSY, MENU_SLOT, MENU_THINK, EXPECTED_CALLBACK)
from renegade.native.thunk import CODE_OFFSET, MAGIC, ALLOWED, COMPLETED
from tools.create_save_pack import digest
from tools.live_test import generate

LOG = logging.getLogger("CompletionTest")


class AuditClient(RenegadeClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []
        self.goal_confirmed = False

    def mission_finished(self, map_name, success):
        accepted = super().mission_finished(map_name, success)
        self.events.append(dict(map_name=map_name, success=success, accepted=accepted))
        return accepted

    async def handle(self, websocket, packet):
        await super().handle(websocket, packet)
        if (self.identity and packet.get("cmd") == "PrintJSON" and packet.get("type") == "Goal"
                and (packet.get("team"), packet.get("slot")) == self.identity[1:]):
            self.goal_confirmed = True


def status(directory, **values):
    values.update(directory=str(directory), updated=datetime.now(timezone.utc).isoformat())
    path = ROOT / ".research" / "completion-status.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def validate_saves(manifest, save_dir, selected):
    if manifest.get("game_sha256") != STEAM.game_sha256:
        raise ValueError("Save pack does not match the supported game")
    found = {}
    for mission in selected:
        entries = [x for x in manifest["missions"] if x["map_name"] == mission.map_name and x.get("difficulty", 1) == 1]
        if len(entries) != 1:
            raise ValueError("Expected one verified save for " + mission.map_name)
        entry = entries[0]
        name = entry["filename"]
        if (not name.startswith("ap_start_") or not name.endswith(".sav")
                or any(c in name for c in ("/", "\\", ":", ".."))):
            raise ValueError("Invalid mission-start save filename")
        if not entry.get("reload_verified") or digest(save_dir / name) != entry["sha256"]:
            raise ValueError("Installed save differs from the verified pack: " + name)
        found[mission.map_name] = name
    return found


def observer_code(memory, mission):
    field = BASE + STEAM.commands + STEAM.mission_command_offset
    code = memory.u32(field)
    if code in (0, BASE + STEAM.mission_complete):
        return None
    control = code - CODE_OFFSET
    if memory.read(control + 12, len(MAGIC)) != MAGIC:
        raise RuntimeError("Unexpected completion callback; refusing to trigger it")
    if not memory.u32(control + ALLOWED) & (1 << (mission.number - 1)):
        return None
    return code


async def native_call(function, *args):
    # A cancellation must not abandon a game-thread operation still in flight.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def exercise(ap, seed, directory, manifest, selected, pid=None):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    builder = observer = network = None
    originals = {}
    outputs = []
    active = False
    restored = False
    client = AuditClient(f"localhost:{port}", "Havoc", state_dir=directory / "state")
    backup = directory / "original-saves"
    backup.mkdir()
    with (directory / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen([str(ap / "ArchipelagoServer.exe"), str(seed),
            "--host", "127.0.0.1", "--port", str(port), "--disable_save", "--password", ""],
            stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        network = asyncio.create_task(client.network())
        async def until(condition, timeout=45):
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Disposable server stopped")
                for task in (network, observer):
                    if task and task.done():
                        task.result()
                        raise RuntimeError("Client or observer stopped unexpectedly")
                if builder:
                    builder.beat()
                result = condition()
                if result:
                    return result
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("Timed out waiting for native capture or AP acknowledgement")
                await asyncio.sleep(0.05)

        try:
            await until(lambda: client.ready)
            if len(client.unlocked_maps) != 12 or client.acknowledged:
                raise RuntimeError("Expected a fresh all-missions-open test slot")
            builder = attach_builder(pid, completion_test=True)
            memory = builder.memory
            save_dir = memory.modules[0].path.parent / "Data" / "save"
            names = validate_saves(manifest, save_dir, selected)
            for path in save_dir.glob("*.sav"):
                shutil.copy2(path, backup / path.name)
                originals[path.name] = digest(path)
            (directory / "backup-manifest.json").write_text(json.dumps(originals, indent=2), encoding="utf-8")
            LOG.info("Backed up %d saves. Leave the game alone; all loads and test victories are automatic.", len(originals))
            expected = set()
            # One fresh reload after all missions proves deduplication across hook lifetimes.
            for index, mission in enumerate((*selected, selected[0])):
                replay = index == len(selected)
                name = names[mission.map_name]
                status(directory, stage="loading", mission=mission.map_name, replay=replay, completed=len(outputs))
                LOG.info("%s %s from %s", "Replay test:" if replay else "Testing", mission.label, name)
                builder.prepare(mission, "save\\" + name)
                active = True
                await native_call(builder.job, RELOAD)
                await native_call(builder.settle)
                snapshot = telemetry(memory)
                if (snapshot["mode"] != 1 or mission_for_map(snapshot["map_name"]) != mission
                        or snapshot["source_name"].replace("save\\", "") != name
                        or snapshot["pending_campaign_continue"]):
                    raise RuntimeError("Loaded game state does not match the requested fresh save")
                before_items = len(client.inventory.items)
                before_checks = set(client.acknowledged)
                client.events.clear()
                observer = asyncio.create_task(watch(client, memory.pid))
                code = await until(lambda: observer_code(memory, mission))
                if memory.u32(code - CODE_OFFSET + COMPLETED):
                    raise RuntimeError("Unexpected victory before the test trigger")
                if before_checks != expected or client.acknowledged != expected:
                    raise RuntimeError("Unexpected checks from loading a save")
                memory.write_u32(builder.control + EXPECTED_CALLBACK, code)
                status(directory, stage="triggering_native_victory", mission=mission.map_name, replay=replay, completed=len(outputs))
                await native_call(builder.job, COMPLETE)
                if not memory.read(BASE + STEAM.pending_continue, 1)[0]:
                    raise RuntimeError("The original game callback did not set campaign continuation")
                captured = memory.u32(code - CODE_OFFSET + COMPLETED)
                if captured != 1 << (mission.number - 1):
                    raise RuntimeError("Native observer captured the wrong mission")
                await until(lambda: client.events)
                if any(e["map_name"] != mission.map_name or e["success"] is not True for e in client.events):
                    raise RuntimeError("Observer delivered an unexpected completion event")
                checks = {mission.check_id(c) for c in range(1, 4)}
                if replay:
                    # Allow a normal network flush window before comparing rewards.
                    end = asyncio.get_running_loop().time() + 3.5
                    await until(lambda: asyncio.get_running_loop().time() >= end)
                    if (any(e["accepted"] for e in client.events) or client.acknowledged != before_checks
                            or len(client.inventory.items) != before_items):
                        raise RuntimeError("Replayed victory produced duplicate checks or rewards")
                else:
                    expected |= checks
                    await until(lambda: client.acknowledged == expected and len(client.inventory.items) == 12 + len(expected))
                    if not any(e["accepted"] for e in client.events):
                        raise RuntimeError("Native victory was not accepted by the client")
                observer.cancel()
                try:
                    await observer
                except asyncio.CancelledError:
                    pass
                observer = None
                if memory.u32(BASE + STEAM.commands + STEAM.mission_command_offset) != BASE + STEAM.mission_complete:
                    raise RuntimeError("Mission callback restoration failed")
                await native_call(builder.job, EXIT)
                active = False
                outputs.append(dict(map_name=mission.map_name, filename=name, replay=replay,
                    runtime=snapshot, native_capture_mask=captured, original_callback_ran=True,
                    checks=sorted(checks), acknowledged_total=len(client.acknowledged),
                    received_total=len(client.inventory.items), mission_callback_restored=True))
                (directory / "missions.json").write_text(json.dumps(outputs, indent=2), encoding="utf-8")
                LOG.info("PASS %s%s: %d checks acknowledged, %d total items", mission.map_name,
                         " replay (no duplicates)" if replay else "", len(client.acknowledged), len(client.inventory.items))
            if len(selected) == 12:
                await until(lambda: client.goal_confirmed)
            report = dict(stage="passed", forced_native_victories=True, natural_objectives_tested=False,
                completed=len(selected), checks=sorted(client.acknowledged), missions=outputs,
                duplicate_replay_verified=True, goal_confirmed_by_server=client.goal_confirmed,
                received_items=[i.as_dict() for i in client.inventory.items])
        finally:
            try:
                if observer:
                    observer.cancel()
                    try:
                        await observer
                    except asyncio.CancelledError:
                        pass
            finally:
                try:
                    if builder:
                        try:
                            await native_call(builder.wait, lambda: memory.u32(builder.control + BUSY) == 0)
                            if active:
                                await native_call(builder.job, EXIT)
                            builder.close()
                            verify_gate(memory)
                            if memory.u32(BASE + MENU_SLOT) != BASE + MENU_THINK:
                                raise RuntimeError("Menu callback restoration failed")
                            if memory.u32(BASE + STEAM.commands + STEAM.mission_command_offset) != BASE + STEAM.mission_complete:
                                raise RuntimeError("Mission callback restoration failed")
                            restored = True
                            LOG.info("All native callbacks restored and verified")
                        finally:
                            memory.close()
                        for name, sha in originals.items():
                            path = save_dir / name
                            if not path.exists() or digest(path) != sha:
                                shutil.copy2(backup / name, path)
                            if digest(path) != sha:
                                raise RuntimeError("Original save restoration failed: " + name)
                        LOG.info("All %d original saves preserved byte-for-byte", len(originals))
                finally:
                    network.cancel()
                    await asyncio.gather(network, return_exceptions=True)
                    if server.poll() is None:
                        try:
                            await asyncio.to_thread(server.communicate, b"/exit\n", timeout=5)
                        except subprocess.TimeoutExpired:
                            server.terminate()
                            await asyncio.to_thread(server.wait, timeout=5)
    report.update(callbacks_restored=restored, original_saves_preserved=True, local_server_stopped=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archipelago-dir", type=Path, default=Path("C:/ProgramData/Archipelago"))
    parser.add_argument("--pack", type=Path, help="Directory containing the generated save-pack manifest")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--first-only", action="store_true")
    args = parser.parse_args()
    directory = ROOT / ".research" / ("completion-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(directory / "client.log", encoding="utf-8")])
    try:
        pack = args.pack or Path(json.loads((ROOT / ".research/savepack-status.json").read_text(encoding="utf-8"))["pack"])
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        status(directory, stage="generating")
        LOG.info("Generating disposable AP room. Test victories are forced; no manual mission completion needed.")
        seed = generate(args.archipelago_dir.resolve(), directory, starting_missions=12)
        selected = MISSIONS[:1] if args.first_only else MISSIONS
        report = asyncio.run(exercise(args.archipelago_dir.resolve(), seed, directory, manifest, selected, args.pid))
        (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        status(directory, **report)
        LOG.info("PASS: %d saved missions, %d checks, replay deduplication, restored callbacks and saves", report["completed"], len(report["checks"]))
    except (Exception, KeyboardInterrupt) as error:
        status(directory, stage="stopped" if isinstance(error, KeyboardInterrupt) else "failed", error=str(error))
        LOG.exception("Completion test stopped: %s", error)
        raise


if __name__ == "__main__":
    main()

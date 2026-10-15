"""Disposable two-player AP test: saved mission lock -> remote item -> playable.

Sakura is an automated local test slot. It submits one generated location check;
Havoc receives the real server-delivered clearance. No real mission is completed
by this script, and no existing AP room or progress directory is used.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import CLEARANCE, ITEM_IDS, mission_for_map
from renegade.native.gate import attach_gate, verify_gate
from renegade.native.inspect import telemetry
from renegade.native.memory import ProcessMemory, candidates
from tools.live_test import generate

LOG = logging.getLogger("UnlockTest")
TARGET = "M01"


class TestSender(RenegadeClient):
    __test__ = False
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scouted = None

    async def handle(self, websocket, packet):
        if packet.get("cmd") == "LocationInfo":
            items = packet.get("locations")
            if not isinstance(items, list) or any(
                not isinstance(i, dict) or i.get("location") not in self.locations
                or any(type(i.get(k)) is not int for k in ("item", "location", "player")) for i in items):
                raise ValueError("Invalid test location scout reply")
            self.scouted = items
        await super().handle(websocket, packet)

    async def flush_once(self, websocket):
        await super().flush_once(websocket)
        if self.ready and self.scouted is None:
            self.scouted = []  # Send only once; full reply arrives asynchronously.
            await self.send(websocket, {"cmd": "LocationScouts", "locations": sorted(self.locations), "create_as_hint": 0})
        if self.ready and getattr(self, "test_location", None) is not None:
            location = self.test_location
            self.test_location = None
            await self.send(websocket, {"cmd": "LocationChecks", "locations": [location]})


def choose_location(sender, receiver):
    return next((i["location"] for i in sender.scouted or []
                 if i["item"] == ITEM_IDS[CLEARANCE] and i["player"] == receiver.identity[2]), None)


def saved_target(snapshot):
    try:
        return (snapshot["mode"] == 1 and mission_for_map(snapshot["map_name"]).map_name == TARGET
                and (snapshot.get("source_name") or "").lower().endswith(".sav"))
    except (ValueError, KeyError):
        return False


class Scenario:
    """Require a saved M01 lock/menu cycle and fresh post-delivery gameplay."""
    def __init__(self):
        self.phase = "waiting_for_locked_save"
        self.locked_save = None
        self.baseline = None
        self.locked_frames = 0
        self.notice_count = 0
        self.return_count = 0

    def observe(self, ready, unlocked, state, snapshot):
        if not ready:
            self.baseline = None
            return None
        if self.phase == "waiting_for_locked_save":
            if saved_target(snapshot) and state["blocked_map"] == TARGET and state["notices_shown"]:
                if TARGET in unlocked:
                    raise RuntimeError("The test mission was already unlocked before its lock was observed")
                self.locked_save = snapshot.copy()
                self.locked_frames = state["blocked_frames"]
                self.notice_count = state["notices_shown"]
                self.return_count = state["menu_returns"]
                self.phase = "waiting_for_menu_return"
        if self.phase == "waiting_for_menu_return" and state["menu_returns"] > self.return_count:
            self.phase = "waiting_for_clearance"
            return "send_clearance"
        if self.phase == "waiting_for_clearance" and TARGET in unlocked:
            self.phase = "waiting_for_unlocked_reload"
            self.baseline = None
        if self.phase == "waiting_for_unlocked_reload":
            if (saved_target(snapshot) and TARGET in unlocked and state["blocked_map"] is None
                    and snapshot["source_name"].lower() == self.locked_save["source_name"].lower()):
                if self.baseline is None:
                    self.baseline = state["allowed_frames"]
                if ((state["allowed_frames"] - self.baseline) & 0xFFFFFFFF) >= 60:
                    self.phase = "passed"
                    return "passed"
            else:
                self.baseline = None
        return None


def status(directory, **values):
    values.update(directory=str(directory), updated=datetime.now(timezone.utc).isoformat())
    target = ROOT / ".research" / "unlock-status.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)


def native_pid():
    matches = []
    errors = []
    for pid in candidates():
        memory = None
        try:
            memory = ProcessMemory(pid)
            verify_gate(memory, return_to_menu=True)
            matches.append(pid)
        except (OSError, ValueError) as error:
            errors.append(str(error))
        finally:
            if memory:
                memory.close()
    if len(matches) != 1:
        raise RuntimeError("Start exactly one supported Steam game with matching privileges. " + "; ".join(errors))
    return matches[0]


async def exercise(ap, seed, directory, pid, duration=600, network_only=False):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    gate = None
    report = None
    with (directory / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen([str(ap / "ArchipelagoServer.exe"), str(seed),
            "--host", "127.0.0.1", "--port", str(port), "--disable_save", "--password", ""],
            stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        havoc = RenegadeClient(f"localhost:{port}", "Havoc", state_dir=directory / "state")
        sakura = TestSender(f"localhost:{port}", "Sakura", state_dir=directory / "state")
        tasks = [asyncio.create_task(c.network()) for c in (havoc, sakura)]
        try:
            async def run():
                nonlocal gate
                deadline = asyncio.get_running_loop().time() + duration
                location = None
                identities = None
                sent = False
                scenario = Scenario()
                previous_phase = None
                while asyncio.get_running_loop().time() < deadline:
                    if server.poll() is not None:
                        raise RuntimeError("Disposable AP server exited; inspect server.log")
                    for task in tasks:
                        if task.done():
                            task.result()
                            raise RuntimeError("AP client stopped before the test completed")
                    if identities and (havoc.identity, sakura.identity) != identities:
                        raise RuntimeError("AP room identity changed during test")
                    if havoc.ready and sakura.ready and location is None:
                        location = choose_location(sakura, havoc)
                        if sakura.scouted and location is None:
                            raise RuntimeError("This seed has no Havoc clearance in Sakura's locations")
                        if location is not None:
                            if tuple(havoc.unlocked_maps) != ("M13",):
                                raise RuntimeError("Expected only M13 unlocked at test start")
                            identities = (havoc.identity, sakura.identity)
                            LOG.info("Two-player room ready. Sakura location %d contains Havoc's next clearance.", location)
                            if not network_only:
                                gate = attach_gate(pid, return_to_menu=True)
                                if gate is None:
                                    raise RuntimeError("Native game disappeared")
                                LOG.info("Gate attached. Load AP Start 02 - Rescue and Retribution; press Okay on the lock popup, then load that same save again.")
                    if location is not None:
                        action = None
                        snapshot = None
                        state = None
                        if gate:
                            state = gate.update(havoc.unlocked_maps)
                            snapshot = telemetry(gate.memory)
                            action = scenario.observe(havoc.ready, havoc.unlocked_maps, state, snapshot)
                            if scenario.phase != previous_phase:
                                previous_phase = scenario.phase
                                status(directory, stage=scenario.phase, target=TARGET, location=location,
                                       game=snapshot, gate=state)
                                if scenario.phase == "waiting_for_unlocked_reload":
                                    LOG.info("CLEARANCE RECEIVED FROM SAKURA. Load AP Start 02 again; it should now be playable.")
                        if not sent and (network_only or action == "send_clearance"):
                            LOG.info("Automated test slot Sakura submits one generated location check; no native victory is simulated for Havoc.")
                            sakura.test_location = location
                            sakura.changed.set()
                            sent = True
                        delivered = next((item for item in havoc.inventory.items if item.item == ITEM_IDS[CLEARANCE]
                                          and item.location == location and item.player == sakura.identity[2]), None)
                        if sent and delivered and location in sakura.acknowledged and TARGET in havoc.unlocked_maps:
                            if network_only or action == "passed" or scenario.phase == "passed":
                                if havoc.acknowledged:
                                    raise RuntimeError("Unexpected Havoc mission checks during unlock-only test")
                                return dict(stage="passed", network_only=network_only,
                                            target=TARGET, sender="Sakura", receiver="Havoc",
                                            automated_sender_location=location, received_item=delivered.as_dict(),
                                            unlocked_maps=havoc.unlocked_maps, havoc_checks=[],
                                            locked_save=scenario.locked_save, final_game=snapshot, final_gate=state,
                                            native_lock_and_reload_verified=not network_only,
                                            no_havoc_victory_simulated=True)
                    await asyncio.sleep(0.1)
                raise TimeoutError("No complete saved-mission lock / remote clearance / unlocked reload cycle observed")
            report = await run()
        finally:
            try:
                if gate:
                    try:
                        gate.close()
                        verify_gate(gate.memory, return_to_menu=True)
                        LOG.info("Original combat callback restored and verified")
                    finally:
                        gate.memory.close()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if server.poll() is None:
                    try:
                        server.communicate(b"/exit\n", timeout=5)
                    except subprocess.TimeoutExpired:
                        server.terminate()
                        server.wait(timeout=5)
    report["original_callback_restored"] = not network_only
    report["local_server_stopped"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archipelago-dir", type=Path, default=Path("C:/ProgramData/Archipelago"))
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--network-only", action="store_true", help="Check the real two-player delivery path without opening game memory")
    args = parser.parse_args()
    directory = ROOT / ".research" / ("unlock-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(directory / "client.log", encoding="utf-8")])
    try:
        pid = None if args.network_only else native_pid()
        status(directory, stage="generating", network_only=args.network_only, pid=pid)
        LOG.info("Generating a disposable two-player room. Existing saves and AP rooms are not used.")
        seed = generate(args.archipelago_dir.resolve(), directory, multiworld=True)
        report = asyncio.run(exercise(args.archipelago_dir.resolve(), seed, directory, pid, args.duration, args.network_only))
        report["pid"] = pid
        (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        status(directory, **report)
        LOG.info("PASS: %s", "real two-player item delivery" if args.network_only else "saved mission locked, real remote clearance received, same saved mission playable")
    except (Exception, KeyboardInterrupt) as error:
        status(directory, stage="stopped" if isinstance(error, KeyboardInterrupt) else "failed", error=str(error))
        LOG.exception("Unlock test stopped: %s", error)
        raise


if __name__ == "__main__":
    main()

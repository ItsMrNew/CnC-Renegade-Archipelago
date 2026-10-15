"""Short local gameplay-gate experiment; no AP room or completion checks.

Run at Renegade's privilege level. Enter any campaign mission and keep the game
focused. The test pauses its combat updates for ten seconds, releases the gate,
and restores the original vtable pointer. It does not modify saves/game files.
"""
import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.data import MISSIONS, mission_for_map
from renegade.native.gate import attach_gate, verify_gate
from renegade.native.inspect import telemetry

LOG = logging.getLogger("GateTest")


def save_status(directory, **values):
    values.update(directory=str(directory), updated=datetime.now(timezone.utc).isoformat())
    path = ROOT / ".research" / ("menu-status.json" if directory.name.startswith("menu-") else "gate-status.json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return values


def exercise(directory, pid=None, duration=300):
    gate = attach_gate(pid)
    if gate is None:
        raise RuntimeError("Start the supported Steam game first")
    report = None
    restored = False
    try:
        LOG.info("Gameplay gate attached. Load any campaign mission, then keep Renegade focused.")
        LOG.info("Combat will pause for ten seconds, then resume. Esc should still open the menu.")
        LOG.info("This local gate test sends no Archipelago checks. You do not need to finish the mission.")
        save_status(directory, stage="waiting_for_campaign_gameplay", pid=gate.memory.pid)
        all_maps = tuple(m.map_name for m in MISSIONS)
        deadline = time.monotonic() + duration
        target = None
        blocked_at = None
        released_at = None
        before_allowed = None
        before_blocked = 0
        initial = None
        while time.monotonic() < deadline:
            snapshot = telemetry(gate.memory)
            try:
                mission = mission_for_map(snapshot["map_name"]) if snapshot["map_name"] else None
            except ValueError:
                mission = None  # Tutorial and multiplayer are outside this test.
            if blocked_at is None:
                target = mission.map_name if mission and snapshot["mode"] == 1 else None
            permissions = all_maps if not target or released_at else tuple(m for m in all_maps if m != target)
            state = gate.update(permissions)
            now = time.monotonic()
            if blocked_at is None and target and state["blocked_map"] == target:
                blocked_at = now
                before_blocked = state["blocked_frames"]
                initial = snapshot
                save_status(directory, stage="holding_locked_gameplay", pid=gate.memory.pid,
                            target=target, **state)
                LOG.info("%s is paused for ten seconds. Menu input remains enabled.", target)
            elif blocked_at is not None and released_at is None and now - blocked_at >= 10:
                if state["blocked_frames"] - before_blocked < 30:
                    # The game can suspend while unfocused or at the menu. Wait
                    # for actual blocked updates rather than passing on a timer.
                    time.sleep(0.1)
                    continue
                if not mission or mission.map_name != target:
                    raise RuntimeError("Mission changed during the gate test; rerun on a single mission")
                before_allowed = state["allowed_frames"]
                gate.update(all_maps)
                released_at = now
                save_status(directory, stage="waiting_for_resumed_gameplay", pid=gate.memory.pid,
                            target=target, **state)
                LOG.info("%s clearance released. Return to gameplay if the menu is open.", target)
            elif released_at is not None and state["allowed_frames"] - before_allowed >= 30:
                if not mission or mission.map_name != target or snapshot["mode"] != 1:
                    raise RuntimeError("Mission changed before resume could be verified")
                report = dict(stage="passed", pid=gate.memory.pid, target=target,
                              paused_seconds=released_at - blocked_at,
                              blocked_updates=state["blocked_frames"] - before_blocked,
                              resumed_updates=state["allowed_frames"] - before_allowed,
                              initial_game_state=initial, final_game_state=snapshot,
                              ap_integration_test=False, menu_behavior_requires_player_confirmation=True)
                break
            time.sleep(0.1)
        if report is None:
            raise TimeoutError("No complete pause/resume cycle observed within the time limit")
    finally:
        try:
            gate.close()
            verify_gate(gate.memory)
            restored = True
            LOG.info("Original combat update restored and verified")
        finally:
            gate.memory.close()
    report["original_combat_update_restored"] = restored
    (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    save_status(directory, **report)
    return report


def exercise_menu(directory, pid=None, duration=300):
    gate = attach_gate(pid, return_to_menu=True)
    if gate is None:
        raise RuntimeError("Start the supported Steam game first")
    report = None
    try:
        LOG.info("Menu gate attached. Load a campaign mission and keep Renegade focused.")
        LOG.info("Expect an in-game Mission locked popup over paused gameplay. Select OK to return to the main menu.")
        LOG.info("This is a local test: no AP checks, save writes, or mission completion required.")
        save_status(directory, stage="waiting_for_campaign_gameplay", pid=gate.memory.pid)
        all_maps = tuple(m.map_name for m in MISSIONS)
        deadline = time.monotonic() + duration
        target = None
        announced = False
        while time.monotonic() < deadline:
            state = gate.snapshot()
            if state["menu_returns"]:
                report = dict(stage="passed", pid=gate.memory.pid, target=target,
                              **state, ap_integration_test=False,
                              visible_popup_and_menu_require_player_confirmation=True)
                break
            if not announced:
                snapshot = telemetry(gate.memory)
                try:
                    mission = mission_for_map(snapshot["map_name"]) if snapshot["map_name"] else None
                except ValueError:
                    mission = None
                target = mission.map_name if mission and snapshot["mode"] == 1 else None
            permissions = all_maps if not target else tuple(m for m in all_maps if m != target)
            state = gate.update(permissions)
            if state["notices_shown"] and not announced:
                announced = True
                save_status(directory, stage="waiting_for_popup_dismissal", pid=gate.memory.pid, target=target, **state)
                LOG.info("Mission locked popup created. Select OK in Renegade to return to the main menu.")
            time.sleep(0.1)
        if report is None:
            raise TimeoutError("No complete popup/menu-return cycle observed within the time limit")
    finally:
        try:
            gate.close()
            verify_gate(gate.memory, return_to_menu=True)
            LOG.info("Original combat update restored and verified")
        finally:
            gate.memory.close()
    report["original_combat_update_restored"] = True
    (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    save_status(directory, **report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--duration", type=int, default=300)
    parser.add_argument("--menu", action="store_true", help="Test the in-game lock popup and return to main menu")
    args = parser.parse_args()
    directory = ROOT / ".research" / (("menu-" if args.menu else "gate-") + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(directory / "client.log", encoding="utf-8")])
    try:
        if args.menu:
            exercise_menu(directory, args.pid, args.duration)
            LOG.info("PASS: popup created, engine menu-return functions executed, original pointer restored.")
        else:
            exercise(directory, args.pid, args.duration)
            LOG.info("PASS: locked combat updates suppressed, unlocked updates forwarded, original pointer restored.")
    except (Exception, KeyboardInterrupt) as error:
        save_status(directory, stage="stopped" if isinstance(error, KeyboardInterrupt) else "failed", error=str(error))
        LOG.error("Gate test stopped: %s", error)
        raise


if __name__ == "__main__":
    main()

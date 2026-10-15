"""Create a disposable local AP room and observe one REAL Renegade victory.

Does not launch/control the game or simulate victories. Run with the same
privileges as Renegade; the native observer temporarily changes game memory.
Exits after the first acknowledged mission or after the time limit.
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import GAME, MISSIONS
from renegade.native.bridge import watch
from renegade.native.inspect import diagnose
from tools.build import build
from tools.ap_install import installed_world

LOG = logging.getLogger("LiveTest")


def status(**values):
    path = ROOT / ".research" / "live-status.json"
    values["updated"] = datetime.now(timezone.utc).isoformat()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def generate(ap, directory, multiworld=False, starting_missions=1, secondary_objectives=False, death_link=False, start_inventory=None, mission_unlocks='progressive', effect_weights=None, weapon_unlocks=False, primary_objectives=False, checks_per_mission=3, data_discs=False):
    if type(starting_missions) is not int or not 1 <= starting_missions <= 12:
        raise ValueError("starting_missions must be between 1 and 12")
    if mission_unlocks not in ('progressive','individual'):
        raise ValueError('Invalid mission unlock mode')
    if type(weapon_unlocks) is not bool:
        raise ValueError('Invalid weapon unlock mode')
    weight_keys = ('armour_loss','butterfingers','concrete_boots','disco_havoc','moon_boots',
                   'bullet_hose','vampiric_rounds','hologram_havoc')
    if effect_weights and any(k not in weight_keys or type(v) is not int or not 0 <= v <= 100 for k,v in effect_weights.items()):
        raise ValueError('Invalid effect weights')
    payload = build().read_bytes()
    players = directory / "players"
    players.mkdir()
    (players / "Havoc.yaml").write_text(
        f'name: Havoc\ngame: "{GAME}"\n"{GAME}":\n'
        f'  checks_per_mission: {checks_per_mission}\n  starting_missions: {starting_missions}\n'
        f'  primary_objectives: {str(primary_objectives).lower()}\n'
        f'  data_discs: {str(data_discs).lower()}\n'
        f'  mission_unlocks: {mission_unlocks}\n'
        f'  weapon_unlocks: {str(weapon_unlocks).lower()}\n'
        f'  secondary_objectives: {str(secondary_objectives).lower()}\n  death_link: {str(death_link).lower()}\n'
        + (f'  start_inventory: {json.dumps(start_inventory)}\n  trap_percentage: 0\n' if start_inventory else '')
        + ''.join(f'  {k}_weight: {v}\n' for k,v in (effect_weights or {}).items()), encoding="utf-8")
    if multiworld:
        # A disposable automated sender; all its missions start open.
        (players / "Sakura.yaml").write_text(
            f'name: Sakura\ngame: "{GAME}"\n"{GAME}":\n'
            '  checks_per_mission: 1\n  starting_missions: 12\n  secondary_objectives: false\n  primary_objectives: false\n'
            f'  death_link: {str(death_link).lower()}\n', encoding="utf-8")
    with installed_world(ap, payload):
        result = subprocess.run([str(ap / "ArchipelagoGenerate.exe"),
            "--player_files_path", str(players), "--outputpath", str(directory),
            "--seed", "937302", "--spoiler", "1"], input="\n", capture_output=True,
            text=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (directory / "generation.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        packages = list(directory.glob("AP_*.zip"))
        if result.returncode or len(packages) != 1:
            raise RuntimeError(f"Generation failed; inspect {directory / 'generation.log'}")
        with zipfile.ZipFile(packages[0]) as archive:
            members = [name for name in archive.namelist() if name.endswith(".archipelago")]
            if len(members) != 1:
                raise RuntimeError("Expected exactly one generated multidata file")
            seed = directory / "test.archipelago"
            seed.write_bytes(archive.read(members[0]))
        return seed


async def exercise(ap, seed, directory, duration, pid):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    report = None
    with (directory / "server.log").open("w", encoding="utf-8") as server_log:
        server = subprocess.Popen([str(ap / "ArchipelagoServer.exe"), str(seed),
            "--host", "127.0.0.1", "--port", str(port), "--disable_save", "--password", ""],
            stdin=subprocess.PIPE, stdout=server_log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        client = RenegadeClient(f"localhost:{port}", "Havoc", state_dir=directory / "state")
        tasks = [asyncio.create_task(client.network()), asyncio.create_task(watch(client, pid))]
        try:
            async def until_victory():
                announced = False
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Local server exited; inspect server.log")
                    for task in tasks:
                        if task.done():
                            task.result()
                            raise RuntimeError("Client or native observer stopped early")
                    if client.ready and not announced:
                        announced = True
                        status(stage="waiting_for_real_victory", directory=str(directory), pid=pid)
                        LOG.info("Room ready. Wait for 'Native mission observer attached', then play the opening mission (M13).")
                    if client.acknowledged:
                        # Keep listening until the AP items for those checks arrive.
                        if len(client.inventory.items) >= 1 + len(client.acknowledged):
                            return {"stage": "passed", "directory": str(directory), "pid": pid,
                                    "checks": sorted(client.acknowledged),
                                    "received_items": [i.as_dict() for i in client.inventory.items],
                                    "unlocked_maps": client.unlocked_maps,
                                    "completed_missions": [m.map_name for m in MISSIONS if m.check_id() in client.acknowledged]}
                    await asyncio.sleep(0.2)
            report = await asyncio.wait_for(until_victory(), duration)
        finally:
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            cleanup_errors = [str(r) for r in results if isinstance(r, Exception)]
            if server.poll() is None:
                try:
                    server.communicate(b"/exit\n", timeout=5)
                except subprocess.TimeoutExpired:
                    server.terminate()
                    server.wait(timeout=5)
            if cleanup_errors:
                raise RuntimeError("Native cleanup/client error: " + "; ".join(cleanup_errors))
    final = diagnose(pid)
    if not any(p.get("supported") for p in final["processes"]):
        raise RuntimeError("Could not verify original callback restoration; restart Renegade")
    report["original_callback_restored"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archipelago-dir", type=Path, default=Path("C:/ProgramData/Archipelago"))
    parser.add_argument("--duration", type=int, default=1200)
    args = parser.parse_args()
    directory = ROOT / ".research" / ("live-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(directory / "client.log", encoding="utf-8")])
    try:
        diagnostic = diagnose()
        (directory / "diagnostic.json").write_text(json.dumps(diagnostic, indent=2), encoding="utf-8")
        matches = [p for p in diagnostic["processes"] if p.get("supported")]
        if len(matches) != 1:
            raise RuntimeError("Start exactly one supported Steam game with matching privileges. " + json.dumps(diagnostic["processes"]))
        pid = matches[0]["pid"]
        status(stage="generating", directory=str(directory), pid=pid)
        LOG.info("Generating a disposable room; this can take about a minute with installed APWorlds.")
        seed = generate(args.archipelago_dir.resolve(), directory)
        report = asyncio.run(exercise(args.archipelago_dir.resolve(), seed, directory, args.duration, pid))
        (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        status(**report)
        LOG.info("PASS: real mission checks acknowledged, items received, original callback restored.")
    except (Exception, KeyboardInterrupt) as error:
        status(stage="stopped" if isinstance(error, KeyboardInterrupt) else "failed", directory=str(directory), error=str(error))
        LOG.error("Test stopped: %s", error)
        raise


if __name__ == "__main__":
    main()

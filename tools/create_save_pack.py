"""Generate and reload-test mission-start saves at all campaign difficulties in the stock Steam game."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.data import MISSIONS
from renegade.native.savebuilder import (attach_builder, LOAD, SAVE, EXIT, RELOAD,
                                        BASE, BUSY, CAMPAIGN_STATE, MENU_SLOT, MENU_THINK, DIFFICULTIES)
from renegade.native.savefiles import inspect_save, relabel, saved_difficulty
from renegade.native.inspect import telemetry
from renegade.native.gate import verify_gate
from renegade.native.profile import STEAM

LOG = logging.getLogger("SavePack")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def status(directory, **values):
    values.update(directory=str(directory), updated=datetime.now(timezone.utc).isoformat())
    path = ROOT / ".research" / "savepack-status.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def generate(directory, selected, pid=None, difficulties=(1,)):
    if not difficulties or len(set(difficulties)) != len(difficulties) or any(type(d) is not int or d not in DIFFICULTIES for d in difficulties):
        raise ValueError("Choose distinct supported difficulties")
    jobs = [(difficulty, mission) for difficulty in difficulties for mission in selected]
    builder = attach_builder(pid)
    memory = builder.memory
    game_dir = memory.modules[0].path.parent
    save_dir = game_dir / "Data" / "save"
    backup = directory / "original-saves"
    backup.mkdir()
    original = {}
    outputs = []
    active = False
    cleanup_complete = False
    try:
        for path in save_dir.glob("*.sav"):
            shutil.copy2(path, backup / path.name)
            original[path.name] = digest(path)
        (directory / "backup-manifest.json").write_text(json.dumps(original, indent=2), encoding="utf-8")
        LOG.info("Backed up %d existing saves. Keep Renegade open; the builder loads and saves each mission automatically.", len(original))
        LOG.info("Please leave the game alone while it works. No AP client should be running.")
        token = directory.name.removeprefix("savepack-")
        pack = ROOT / "dist" / ("Renegade-Mission-Starts-" + token)
        (pack / "saves").mkdir(parents=True)
        for difficulty, mission in jobs:
            difficulty_name = DIFFICULTIES[difficulty]
            filename = f"ap_start_{token}_{difficulty_name}_{mission.number:02d}_{mission.map_name}.sav"
            target = save_dir / filename
            if target.exists():
                raise FileExistsError(f"Refusing to overwrite {target}")
            builder.prepare(mission, "save\\" + filename, difficulty=difficulty)
            status(directory, stage="loading", mission=mission.label, difficulty=difficulty_name, completed=len(outputs), total=len(jobs), pack=str(pack))
            LOG.info("Loading %s — %s", mission.label, difficulty_name)
            builder.job(LOAD)
            active = True
            builder.settle()
            if builder.difficulty_level() != difficulty:
                raise RuntimeError("Loaded mission difficulty differs from request")
            builder.job(SAVE)
            if not target.is_file():
                raise RuntimeError(f"The engine did not create the expected save: {target}")
            raw = target.read_bytes()
            info = inspect_save(raw)
            if info["map_name"] != mission.map_name:
                raise RuntimeError(f"Saved map mismatch: {info['map_name']} != {mission.map_name}")
            if saved_difficulty(raw) != difficulty:
                raise RuntimeError("Saved difficulty differs from request")
            labeled = relabel(raw, "AP Start " + difficulty_name + " " + mission.label)
            if inspect_save(labeled)["world_sha256"] != info["world_sha256"]:
                raise RuntimeError("Relabeling changed the saved world")
            target.write_bytes(labeled)
            builder.job(EXIT)
            active = False
            status(directory, stage="reload_verification", mission=mission.label, difficulty=difficulty_name, completed=len(outputs), total=len(jobs), pack=str(pack))
            LOG.info("Reloading %s to verify it", mission.map_name)
            builder.job(RELOAD)
            active = True
            builder.settle()
            if builder.difficulty_level() != difficulty:
                raise RuntimeError("Reloaded difficulty differs from saved difficulty")
            state = memory.u32(BASE + CAMPAIGN_STATE)
            expected_state = builder.campaign[mission.map_name + ".MIX"]
            if state != expected_state:
                raise RuntimeError(f"Reloaded campaign state {state} differs from {expected_state}")
            snapshot = telemetry(memory)
            if snapshot["mode"] != 1 or not builder.player_object():
                raise RuntimeError("Reload did not produce a single-player mission with a player object")
            builder.job(EXIT)
            active = False
            destination = pack / "saves" / filename
            shutil.copy2(target, destination)
            info = inspect_save(destination.read_bytes())
            outputs.append(dict(number=mission.number, mission=mission.title, filename=filename,
                                campaign_state=state, difficulty=difficulty, difficulty_name=difficulty_name,
                                saved_difficulty_verified=True, reload_difficulty_verified=True,
                                reload_verified=True, runtime=snapshot, **info))
            LOG.info("Verified %s (%s bytes)", mission.label, info["bytes"])
            status(directory, stage="verified", mission=mission.label, difficulty=difficulty_name, completed=len(outputs), total=len(jobs), pack=str(pack))
        manifest = dict(created=datetime.now(timezone.utc).isoformat(),
                        difficulty=DIFFICULTIES[difficulties[0]] if len(difficulties) == 1 else "Multiple",
                        difficulties=[DIFFICULTIES[d] for d in difficulties],
                        game_sha256=STEAM.game_sha256,
                        missions=outputs, scope="Engine-generated fresh mission loadouts, normal campaign continuation; no AP progress.")
        (pack / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (pack / "README.txt").write_text(
            "Renegade mission-start save pack\n\n"
            "Stock Steam Renegade. Difficulty is included in each save name. Each save was written by the game\n"
            "at mission start and successfully reloaded with a player object and the\n"
            "correct campaign position and difficulty. Fresh mission loadouts; no carried-over inventory\n"
            "from a full campaign playthrough. No Archipelago checks or items are stored.\n\n"
            "These saves are already installed in this machine's Renegade Data\\save folder.\n"
            "Choose Load Game and look for AP Start Recruit, Soldier, or Commando, then the mission number.\n"
            "To install elsewhere, back up existing saves and copy the saves/*.sav files\n"
            "into the stock game's Data\\save folder without overwriting existing files.\n\n"
            "For AP lock testing, start the AP native client with --gate-missions, then\n"
            "load the desired AP Start save. Locked missions should show the lock popup.\n"
            "The tutorial is excluded. Full mission playthroughs are not validated.\n",
            encoding="utf-8")
        difficulty_archives = {}
        for difficulty in difficulties:
            name = DIFFICULTIES[difficulty]
            entries = [entry for entry in outputs if entry["difficulty"] == difficulty]
            single = dict(manifest, difficulty=name, difficulties=[name], missions=entries)
            archive_path = pack.parent / (pack.name + "-" + name + ".zip")
            with zipfile.ZipFile(archive_path, "x", zipfile.ZIP_DEFLATED) as out:
                out.writestr("manifest.json", json.dumps(single, indent=2) + "\n")
                out.write(pack / "README.txt", "README.txt")
                for entry in entries:
                    out.write(pack / "saves" / entry["filename"], "saves/" + entry["filename"])
            difficulty_archives[name] = str(archive_path)
        archive = pack.with_suffix(".zip")
        with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as out:
            for path in sorted(pack.rglob("*")):
                if path.is_file():
                    out.write(path, path.relative_to(pack))
        return dict(stage="passed", completed=len(outputs), total=len(jobs), pack=str(pack), archive=str(archive), difficulty_archives=difficulty_archives, missions=outputs)
    finally:
        try:
            # Do not race a synchronous native loader/save if interrupted.
            builder.wait(lambda: memory.u32(builder.control + BUSY) == 0)
            if active:
                builder.job(EXIT)
            builder.close()
            verify_gate(memory)
            if memory.u32(BASE + MENU_SLOT) != BASE + MENU_THINK:
                raise RuntimeError("Menu callback restoration was not verified")
            cleanup_complete = True
            LOG.info("Original menu and combat callbacks restored and verified")
        finally:
            memory.close()
            if cleanup_complete:
                for name, sha in original.items():
                    path = save_dir / name
                    if not path.exists() or digest(path) != sha:
                        shutil.copy2(backup / name, path)
                    if digest(path) != sha:
                        raise RuntimeError(f"Could not restore original save {name}")
                LOG.info("All %d original saves preserved byte-for-byte", len(original))
            else:
                LOG.error("Cleanup was not verified. Original save backup remains at %s", backup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--first-only", action="store_true", help="Generate/reload the opening mission before a full run")
    parser.add_argument("--difficulty", choices=("all", "recruit", "soldier", "commando"), default="all")
    args = parser.parse_args()
    directory = ROOT / ".research" / ("savepack-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(directory / "client.log", encoding="utf-8")])
    try:
        status(directory, stage="starting")
        difficulties = tuple(DIFFICULTIES) if args.difficulty == "all" else (next(d for d, n in DIFFICULTIES.items() if n.lower() == args.difficulty),)
        report = generate(directory, MISSIONS[:1] if args.first_only else MISSIONS, args.pid, difficulties)
        report.update(callbacks_restored=True, original_saves_preserved=True)
        (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        status(directory, **report)
        LOG.info("PASS: %d mission-start saves created, reload-tested and packed: %s", report["completed"], report["archive"])
    except (Exception, KeyboardInterrupt) as error:
        status(directory, stage="failed", error=str(error))
        LOG.exception("Save pack stopped: %s", error)
        raise


if __name__ == "__main__":
    main()

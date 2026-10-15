"""Test the packaged world against installed AP generation and actual local servers.

Temporarily installs ONLY this APWorld; refuses to overwrite an existing package.
Creates synthetic victories. No game files are opened or modified.
"""
import argparse
import asyncio
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import GAME
from tools.build import build


async def play(server_exe, seed, directory, slots, checks, starting):
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    log_path = directory / "server.log"
    clients = [RenegadeClient(f"localhost:{port}", slot, state_dir=directory / "state") for slot in slots]
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(server_exe), str(seed), "--host", "127.0.0.1",
            "--port", str(port), "--disable_save", "--password", "", "--auto_shutdown", "120"],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        tasks = [asyncio.create_task(client.network()) for client in clients]
        try:
            async def exercise():
                while not all(c.goal_sent and len(c.inventory.items) == checks * 12 + starting for c in clients):
                    if process.poll() is not None:
                        raise AssertionError("AP server exited early")
                    for task in tasks:
                        if task.done():
                            task.result()
                            raise AssertionError("Client exited early")
                    for client in clients:
                        for map_name in client.unlocked_maps:
                            client.mission_finished(map_name, True)
                    await asyncio.sleep(0.1)
                await asyncio.sleep(0.5)
            await asyncio.wait_for(exercise(), 35)
            for client in clients:
                assert len(client.acknowledged) == checks * 12
                assert client.progress.completed == client.locations
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if process.poll() is None:
                try:
                    process.communicate(b"/exit\n", timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
    contents = log_path.read_text(encoding="utf-8", errors="replace")
    assert contents.count("has completed their goal") >= len(slots), contents[-3000:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archipelago-dir", type=Path, required=True)
    args = parser.parse_args()
    ap = args.archipelago_dir.resolve()
    package = build()
    target = ap / "custom_worlds" / "renegade.apworld"
    if target.exists() or (ap / "custom_worlds" / "renegade").exists():
        raise SystemExit("An existing Renegade world is installed; use a separate AP install for this smoke test")
    target.parent.mkdir(exist_ok=True)
    payload = package.read_bytes()
    # Exclusive creation avoids racing another installer.
    with target.open("xb") as handle:
        handle.write(payload)
    results = []
    try:
        output = ROOT / ".research" / "smoke"
        output.mkdir(parents=True, exist_ok=True)
        for name, checks, starting, slots in [
            ("default", 3, 1, ["Havoc"]),
            ("minimum", 1, 1, ["Havoc"]),
            ("open", 5, 12, ["Havoc"]),
            ("multiworld", 3, 1, ["Havoc", "Sakura"]),
        ]:
            with tempfile.TemporaryDirectory(prefix="renegade-ap-") as temporary:
                directory = Path(temporary)
                players = directory / "players"
                players.mkdir()
                for slot in slots:
                    (players / f"{slot}.yaml").write_text(
                        f'name: {slot}\ngame: "{GAME}"\n"{GAME}":\n'
                        f'  checks_per_mission: {checks}\n  starting_missions: {starting}\n'
                        '  secondary_objectives: false\n  death_link: false\n', encoding="utf-8")
                generated = subprocess.run([str(ap / "ArchipelagoGenerate.exe"),
                    "--player_files_path", str(players), "--outputpath", str(directory),
                    "--seed", "937301", "--spoiler", "1"], input="\n", capture_output=True,
                    text=True, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                (output / f"{name}-generation.log").write_text(generated.stdout + generated.stderr, encoding="utf-8")
                packages = list(directory.glob("AP_*.zip"))
                if generated.returncode or len(packages) != 1:
                    raise AssertionError(f"Generation failed; see {output / (name + '-generation.log')}")
                with zipfile.ZipFile(packages[0]) as archive:
                    members = [n for n in archive.namelist() if n.endswith(".archipelago")]
                    assert len(members) == 1
                    seed = directory / "test.archipelago"
                    seed.write_bytes(archive.read(members[0]))
                try:
                    asyncio.run(play(ap / "ArchipelagoServer.exe", seed, directory, slots, checks, starting))
                finally:
                    if (directory / "server.log").exists():
                        (output / f"{name}-server.log").write_bytes((directory / "server.log").read_bytes())
                results.append({"case": name, "players": len(slots), "checks_per_player": checks * 12, "result": "passed"})
                print(f"PASS: {name}: generated, delivered items, acknowledged checks, recorded goal", flush=True)
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    finally:
        if target.exists() and target.read_bytes() == payload:
            target.unlink()
        else:
            print("Temporary APWorld changed externally; leaving it untouched", file=sys.stderr)


if __name__ == "__main__":
    main()

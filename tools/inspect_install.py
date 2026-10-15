"""Read-only build fingerprint. This does not certify a native adapter as compatible."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def inspect(directory):
    if not directory.is_dir():
        raise ValueError(f"Game directory does not exist: {directory}")
    files = {}
    for name in ("Game.exe", "Game.dat", "Renegade.exe", "game2.exe", "Scripts.dll", "Scripts2.dll"):
        path = directory / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        machine = None
        if len(data) >= 64 and data[:2] == b"MZ":
            offset = struct.unpack_from("<I", data, 0x3C)[0]
            if offset + 6 <= len(data) and data[offset:offset + 4] == b"PE\0\0":
                machine = hex(struct.unpack_from("<H", data, offset + 4)[0])
        files[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "pe_machine": machine}
    version = directory / "ttversion.txt"
    maps = [name for name in ["M13"] + [f"M{n:02d}" for n in range(1, 12)]
            if (directory / "Data" / f"{name}.mix").is_file()]
    return {"directory": str(directory.resolve()), "native_adapter_supported": False,
            "files": files, "campaign_maps_present": maps,
            "tt_version": version.read_text(encoding="utf-8").strip() if version.is_file() else None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game_directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.game_directory), indent=2))

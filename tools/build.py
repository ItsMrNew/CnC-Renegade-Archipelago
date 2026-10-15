"""Package the world with a copy of the canonical client/world contract."""
import json
import argparse
import hashlib
import io
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def client_bundle(executable):
    """Bundle only the standalone client and its distribution notices."""
    executable = Path(executable)
    if not executable.is_file():
        raise FileNotFoundError('Build the launcher first with python -m tools.build_launcher, '
                                'or use --world-only for a development world without a client.')
    contents = {'Renegade Archipelago.exe': executable.read_bytes()}
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        contents[name] = (ROOT / name).read_bytes()
    for path in sorted((ROOT / 'assets/licenses').iterdir()):
        if path.is_file():
            contents['licenses/' + path.name] = path.read_bytes()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    payload = stream.getvalue()
    manifest = {'payload_sha256': hashlib.sha256(payload).hexdigest(),
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}}
    return payload, manifest


def build(executable=None, *, world_only=False, output=None):
    bundle = None if world_only else client_bundle(executable or ROOT / 'dist/Renegade Archipelago.exe')
    world = ROOT / "worlds" / "renegade"
    (world / "data.py").write_bytes((ROOT / "renegade" / "data.py").read_bytes())
    output = Path(output or ROOT / 'dist/renegade.apworld')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Ship the recommended player presets beside the APWorld, outside the
    # active Players folder so the two alternatives do not add extra slots.
    presets = ROOT / "dist" / "recommended-yamls"
    presets.mkdir(parents=True, exist_ok=True)
    for name in ("Recommended YAML Renegade - Death Link.yaml", "Recommended YAML Renegade.yaml"):
        (presets / name).write_bytes((ROOT / "recommended-yamls" / name).read_bytes())
    manifest = json.loads((world / "archipelago.json").read_text(encoding="utf-8"))
    # These are APContainer format versions, not the native client protocol.
    manifest.update(version=7, compatible_version=7)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("archipelago.json", json.dumps(manifest, indent=2) + "\n")
        for path in sorted(world.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                name = 'renegade/' + path.relative_to(world).as_posix()
                if path.name == '__init__.py' and path.parent == world and bundle:
                    archive.writestr(name, path.read_bytes() +
                        b'\nfrom .launcher_client import register as _register_launcher_client\n_register_launcher_client()\n')
                else:
                    archive.write(path, name)
        if bundle:
            payload, client_manifest = bundle
            archive.writestr('renegade/launcher_bundle/client.zip', payload, compress_type=zipfile.ZIP_STORED)
            archive.writestr('renegade/launcher_bundle/manifest.json', json.dumps(client_manifest, indent=2))
            archive.write(ROOT / 'assets/Renegade.png', 'renegade/launcher_bundle/icon.png')
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, help='Standalone EXE to embed (defaults to dist)')
    parser.add_argument('--output', type=Path, help='APWorld output path')
    parser.add_argument('--world-only', action='store_true', help='Development world without bundled launcher')
    args = parser.parse_args()
    print(build(args.executable, world_only=args.world_only, output=args.output))

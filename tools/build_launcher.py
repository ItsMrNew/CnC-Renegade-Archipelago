"""Build a standalone Windows GUI client containing both supported adapters."""
from pathlib import Path
import argparse
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(output_dir=None):
    output_dir = Path(output_dir or ROOT / 'dist').resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ('Renegade.ico', 'mission-starts.zip'):
        if not (ROOT / 'assets' / name).is_file():
            raise FileNotFoundError('Missing release asset: assets/' + name)
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
        '--name', 'Renegade Archipelago', '--icon', str(ROOT / 'assets' / 'Renegade.ico'),
        '--add-data', str(ROOT / 'assets') + ';assets',
        '--add-data', str(ROOT / 'LICENSE') + ';.',
        '--add-data', str(ROOT / 'THIRD_PARTY_NOTICES.md') + ';.',
        '--collect-submodules', 'websockets',
        '--distpath', str(output_dir), '--workpath', str(ROOT / 'build' / 'launcher'),
        '--specpath', str(ROOT / 'build'), str(ROOT / 'RenegadeDesktop.py'),
    ], cwd=ROOT, check=True)
    executable = output_dir / 'Renegade Archipelago.exe'
    from tools.build import build as build_world
    world = build_world(executable, output=output_dir / 'renegade.apworld')
    with zipfile.ZipFile(output_dir / 'Renegade-Archipelago-Windows.zip', 'w', zipfile.ZIP_DEFLATED) as release:
        release.write(executable, executable.name)
        release.write(world, world.name)
        release.write(ROOT / 'docs' / 'windows-quickstart.md', 'README.md')
        release.write(ROOT / 'RELEASE_NOTES.md', 'RELEASE_NOTES.md')
        release.write(ROOT / 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md')
        release.write(ROOT / 'LICENSE', 'LICENSE')
        for folder in ('recommended-yamls', 'assets/licenses'):
            for path in sorted((ROOT / folder).iterdir()):
                if path.is_file():
                    release.write(path, path.relative_to(ROOT).as_posix())
    return executable


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, help='Alternate destination while an older EXE is running')
    print(build(parser.parse_args().output_dir))

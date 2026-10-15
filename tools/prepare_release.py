"""Validate and export release downloads and a clean source repository. Never publishes."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ('Renegade Archipelago.exe', 'renegade.apworld', 'Renegade-Archipelago-Windows.zip')
PUBLIC_DOCS = {'windows-quickstart.md', 'items.md', 'weapon-unlocks.md', 'data-discs.md',
               'launcher-packaging.md', 'release-checklist.md', 'release-review-2026-10-16.md'}
EXCLUDED_PARTS = {'__pycache__', '.pytest_cache', '.ruff_cache', 'build', 'dist', '.git', '.venv'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def zip_tree(folder, output):
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob('*')):
            relative = path.relative_to(folder)
            if (path.is_file() and not path.is_symlink() and
                    not EXCLUDED_PARTS.intersection(relative.parts) and path.suffix not in ('.pyc', '.pyo')):
                archive.write(path, relative.as_posix())


def write_checksums(folder):
    paths = sorted(p for p in folder.iterdir() if p.is_file() and p.name != 'SHA256SUMS.txt')
    (folder / 'SHA256SUMS.txt').write_text(
        ''.join(f'{digest(p)}  {p.name}\n' for p in paths), encoding='utf-8')


def validate_bundle(launcher_dir):
    """Refuse mixed builds or stale player docs before creating an export."""
    launcher_dir = Path(launcher_dir)
    exe = (launcher_dir / ARTIFACTS[0]).read_bytes()
    def same(actual, expected, name):
        if actual != expected:
            raise ValueError('Release content mismatch: ' + name)
    with zipfile.ZipFile(launcher_dir / 'renegade.apworld') as world:
        payload = world.read('renegade/launcher_bundle/client.zip')
        manifest = json.loads(world.read('renegade/launcher_bundle/manifest.json'))
        same(hashlib.sha256(payload).hexdigest(), manifest['payload_sha256'], 'APWorld payload')
        with zipfile.ZipFile(io.BytesIO(payload)) as client:
            same(client.read(ARTIFACTS[0]), exe, 'APWorld EXE')
            for name, expected in manifest['files'].items():
                same(hashlib.sha256(client.read(name)).hexdigest(), expected, name)
            for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
                same(client.read(name), (ROOT / name).read_bytes(), name)
            for path in (ROOT / 'assets/licenses').iterdir():
                if path.is_file():
                    same(client.read('licenses/' + path.name), path.read_bytes(), path.name)
        same(world.read('renegade/data.py'), (ROOT / 'renegade/data.py').read_bytes(), 'world data')
        for path in (ROOT / 'worlds/renegade').rglob('*'):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            name = 'renegade/' + path.relative_to(ROOT / 'worlds/renegade').as_posix()
            expected = path.read_bytes()
            if name == 'renegade/__init__.py':
                expected += b'\nfrom .launcher_client import register as _register_launcher_client\n_register_launcher_client()\n'
            same(world.read(name), expected, name)
    with zipfile.ZipFile(launcher_dir / ARTIFACTS[2]) as windows:
        for name in ARTIFACTS[:2]:
            same(windows.read(name), (launcher_dir / name).read_bytes(), 'Windows ZIP: ' + name)
        expected_files = {'README.md': ROOT / 'docs/windows-quickstart.md'}
        expected_files.update({name: ROOT / name for name in ('RELEASE_NOTES.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md')})
        for folder in ('recommended-yamls', 'assets/licenses'):
            expected_files.update({p.relative_to(ROOT).as_posix(): p for p in (ROOT / folder).iterdir() if p.is_file()})
        for name, path in expected_files.items():
            same(windows.read(name), path.read_bytes(), 'Windows ZIP: ' + name)


def source_candidates():
    if (ROOT / '.git').exists():
        raw = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT)
        return {Path(name) for name in raw.decode().split('\0') if name}
    return {p.relative_to(ROOT) for p in ROOT.rglob('*') if p.is_file()}


def mod_source(relative):
    roots = {'.github', 'assets', 'docs', 'players', 'recommended-yamls', 'renegade', 'tests', 'tools', 'worlds'}
    top = {'.gitignore', '.gitattributes', 'LICENSE', 'README.md', 'RELEASE_NOTES.md', 'THIRD_PARTY_NOTICES.md',
           'RenegadeClient.py', 'RenegadeDesktop.py', 'requirements.txt', 'requirements-dev.txt',
           'requirements-build.txt', 'Run Renegade Archipelago.cmd', 'Run Renegade TT Archipelago.cmd'}
    if EXCLUDED_PARTS.intersection(relative.parts) or relative.suffix in ('.pyc', '.pyo', '.log', '.dmp', '.tmp'):
        return False
    if relative.parts[0] not in roots and relative.as_posix() not in top:
        return False
    if relative.parts[0] == 'docs' and relative.name not in PUBLIC_DOCS:
        return False
    if relative.parts[0] == 'tools' and 'tracker' in relative.name.lower() and relative.name != 'tracker_assets.py':
        return False
    if relative.parts[0] == 'tests' and relative.name.startswith('test_tracker'):
        return False
    return True


def export(launcher_dir, output, *, include_tracker=False):
    launcher_dir, output = Path(launcher_dir).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a fresh output directory; existing releases are preserved')
    validate_bundle(launcher_dir)
    main = output / 'main'
    main.mkdir(parents=True)
    repos = output / 'repositories'
    mod_repo = repos / 'renegade-archipelago'
    mod_repo.mkdir(parents=True)
    candidates = source_candidates()
    main_sources = []
    for relative in sorted(candidates):
        source = ROOT / relative
        if not mod_source(relative) or not source.is_file() or source.is_symlink():
            continue
        target = mod_repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        main_sources.append(relative.as_posix())
    dev = mod_repo / 'requirements-dev.txt'
    dev.write_text(dev.read_text().replace('-r requirements-tracker.txt\n', ''), encoding='utf-8')
    for name in ARTIFACTS:
        shutil.copy2(launcher_dir / name, main / name)
    shutil.copy2(ROOT / 'RELEASE_NOTES.md', main / 'RELEASE_NOTES.md')
    shutil.copy2(ROOT / 'docs/windows-quickstart.md', main / 'INSTALL.md')
    for yaml in (ROOT / 'recommended-yamls').glob('*.yaml'):
        shutil.copy2(yaml, main / yaml.name)
    zip_tree(mod_repo, main / 'Renegade-Archipelago-Source.zip')
    write_checksums(main)
    if include_tracker:
        export_tracker(candidates, output, repos)
    (output / 'preparation.json').write_text(json.dumps(dict(
        status='prepared; not published', main_source_files=len(main_sources),
        tracker_exported=include_tracker, private_state_included=False,
        main_exe_sha256=digest(main / ARTIFACTS[0])), indent=2) + '\n', encoding='utf-8')
    return output


def export_tracker(candidates, output, repos):
    tracker = output / 'poptracker'
    tracker.mkdir()
    tracker_repo = repos / 'renegade-poptracker'
    tracker_repo.mkdir()
    tracker_sources = [p for p in candidates if p.parts[0]=='tracker' or
        (p.parts[0]=='tests' and p.name.startswith('test_tracker'))]
    tracker_sources += [Path(p) for p in (
        'LICENSE','.gitignore','.gitattributes','requirements.txt','requirements-tracker.txt',
        'Run Renegade PopTracker.cmd','Run Renegade PopTracker Marker.cmd',
        'renegade/__init__.py','renegade/data.py','renegade/check_hints.py','renegade/tracker_bridge.py',
        'renegade/native/__init__.py','renegade/native/savefiles.py',
        'tools/build_tracker.py','tools/build_tracker_runtime.py','tools/run_tracker.py',
        'tools/tracker_assets.py','tools/tracker_maps.py','tools/tracker_guide.py',
        'tools/tracker_navigation.py','tools/tracker_photos.py','tools/tracker_runtime_test.py')]
    for relative in tracker_sources:
        source=ROOT/relative
        if source.is_file():
            target=tracker_repo/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
    (tracker_repo/'requirements-dev.txt').write_text('-r requirements.txt\n-r requirements-tracker.txt\npytest>=8,<10\n')
    shutil.copy2(ROOT/'tracker/repository-README.md',tracker_repo/'README.md')
    shutil.copy2(ROOT/'tracker/THIRD_PARTY_NOTICES.md',tracker_repo/'THIRD_PARTY_NOTICES.md')

    zip_tree(tracker_repo, tracker / 'Renegade-PopTracker-Source.zip')
    # Runtime allowlist deliberately omits portable-config (room credentials and
    # tracker state), logs, caches, developer worktrees and unrelated packs.
    runtime=ROOT/'PopTracker-Marker'
    info=json.loads((runtime/'build-info.json').read_text())
    checks={'poptracker.exe':'executable_sha256','player-marker.patch':'patch_sha256',
            'poptracker-source.tar.xz':'source_sha256'}
    for name,key in checks.items():
        if digest(runtime/name)!=info[key]:
            raise ValueError('Runtime provenance mismatch: '+name)
    if (runtime/'player-marker.patch').read_bytes()!=(ROOT/'tracker/runtime/player-marker.patch').read_bytes():
        raise ValueError('Runtime patch differs from source repository')
    from tools.build_tracker import VERSION
    pack=ROOT/'dist'/f'Renegade-PopTracker-{VERSION}-local-maps.zip'
    with zipfile.ZipFile(pack) as archive:
        cat=json.loads(archive.read('catalog.json'))
        if len(cat['locations'])!=190:
            raise ValueError('Unexpected tracker catalogue')
    shutil.copy2(pack,tracker/pack.name)
    with zipfile.ZipFile(tracker/'Renegade-PopTracker-Windows.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for name in ('poptracker.exe','LICENSE','asset-licenses.txt','PLUGIN LICENSE ADDENDUM.md',
                     'portable.txt','build-info.json','player-marker.patch','poptracker-source.tar.xz'):
            archive.write(runtime/name,name)
        archive.write(ROOT/'tracker/runtime/README.md','README-runtime.md')
        archive.write(ROOT/'tracker/repository-README.md','README.md')
        archive.write(ROOT/'tracker/THIRD_PARTY_NOTICES.md','THIRD_PARTY_NOTICES.md')
        archive.write(ROOT/'LICENSE','LICENSE-pack-MIT')
        archive.write(pack,'packs/'+pack.name)
        for path in sorted((runtime/'assets').rglob('*')):
            if path.is_file():archive.write(path,path.relative_to(runtime).as_posix())
    write_checksums(tracker)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launcher-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--include-tracker', action='store_true', help='Also export the independent companion repository and runtime')
    args = parser.parse_args()
    print(export(args.launcher_dir, args.output, include_tracker=args.include_tracker))

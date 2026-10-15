"""Release exports must be self-contained, consistent and free of local state."""
from pathlib import Path
import shutil
import zipfile

import pytest

from tools import build, prepare_release as release


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    source = release.ROOT
    root = tmp_path / 'source'
    for folder in ('worlds', 'assets/licenses', 'recommended-yamls'):
        shutil.copytree(source / folder, root / folder, ignore=shutil.ignore_patterns('__pycache__'))
    for name in ('renegade/data.py', 'assets/Renegade.png', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
                 'README.md', 'RELEASE_NOTES.md', 'docs/windows-quickstart.md', 'tools/prepare_release.py'):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, root / name)
    (root / 'requirements-dev.txt').write_text('pytest>=8,<10\n-r requirements-tracker.txt\n')
    for name in ('.state/room.json', 'tracker/secret.json', 'logs/desktop.log',
                 '.research/session.json', 'docs/old-review.md', 'tools/launchers/Run Tracker.cmd'):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text('private-fixture')
    monkeypatch.setattr(build, 'ROOT', root)
    monkeypatch.setattr(release, 'ROOT', root)
    dist = root / 'dist'
    dist.mkdir()
    (dist / release.ARTIFACTS[0]).write_bytes(b'MZ-test-client')
    build.build(dist / release.ARTIFACTS[0])
    with zipfile.ZipFile(dist / release.ARTIFACTS[2], 'w') as archive:
        for name in release.ARTIFACTS[:2]:
            archive.write(dist / name, name)
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'RELEASE_NOTES.md'):
            archive.write(root / name, name)
        archive.write(root / 'docs/windows-quickstart.md', 'README.md')
        for folder in ('recommended-yamls', 'assets/licenses'):
            for path in (root / folder).iterdir():
                archive.write(path, path.relative_to(root).as_posix())
    return root, dist, tmp_path / 'release'


def test_mod_export_needs_no_companion_and_checksums_every_download(candidate, monkeypatch):
    root, dist, destination = candidate
    monkeypatch.setattr(release, 'export_tracker', lambda *args: pytest.fail('Companion export was not requested'))
    release.export(dist, destination)
    assert not (destination / 'poptracker').exists()
    with zipfile.ZipFile(destination / 'main/Renegade-Archipelago-Source.zip') as archive:
        assert 'tools/prepare_release.py' in archive.namelist()
        assert b'requirements-tracker' not in archive.read('requirements-dev.txt')
        assert not any(b'private-fixture' in archive.read(name) for name in archive.namelist())
    checksum_path = destination / 'main/SHA256SUMS.txt'
    entries = [line.split('  ', 1) for line in checksum_path.read_text().splitlines()]
    assert {name for _, name in entries} == {p.name for p in checksum_path.parent.iterdir() if p != checksum_path}
    assert all(release.digest(checksum_path.parent / name) == value for value, name in entries)
    with pytest.raises(ValueError, match='fresh output'):
        release.export(dist, destination)


@pytest.mark.parametrize('name', [
    'dist/Renegade Archipelago.exe', 'LICENSE', 'docs/windows-quickstart.md',
    'worlds/renegade/Options.py', 'renegade/data.py',
])
def test_export_rejects_mixed_or_stale_release_before_writing(candidate, name):
    root, dist, destination = candidate
    with (root / name).open('ab') as stream:
        stream.write(b'\nchanged')
    with pytest.raises(ValueError, match='mismatch'):
        release.export(dist, destination)
    assert not destination.exists()


@pytest.mark.parametrize('name', ['.state/room.json', 'logs/desktop.log', 'tracker/init.lua',
                                 'tools/launchers/Run Tracker.cmd', 'tests/__pycache__/test.pyc'])
def test_source_policy_excludes_local_and_companion_files(name):
    assert not release.mod_source(Path(name))

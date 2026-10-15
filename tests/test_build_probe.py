"""Unknown-build diagnostics must remain read-only and fail closed."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import probe_game_build as probe
from renegade.native.profile import STEAM, UnsupportedBuild


def test_anchor_comparison_reports_partial_and_unreadable_ranges():
    def read(address, size):
        if address == 30:
            raise OSError('unmapped page')
        return {10: b'ab', 20: b'zz'}[address]

    result = probe.compare_anchors(read, {'test': {10: b'ab', 20: b'cd', 30: b'ef'}})['test']
    assert (result['matched'], result['total']) == (1, 3)
    assert result['anchors'][1]['actual'] == '7a7a'
    assert result['anchors'][2]['error'] == 'unmapped page'


@pytest.mark.parametrize('known', [False, True])
def test_disk_fingerprints_never_claim_runtime_support(tmp_path, monkeypatch, known):
    for name in ('Game.dat', 'Scripts.dll'):
        (tmp_path / name).write_bytes(b'MZ')
    hashes = {'Game.dat': STEAM.game_sha256, 'Scripts.dll': STEAM.scripts_sha256}
    monkeypatch.setattr(probe, 'image_info', lambda path, anchors: {
        'name': path.name, 'sha256': hashes[path.name] if known else 'unknown'})
    result = probe.installation(tmp_path)
    assert result['stock_fingerprints_match'] is known
    assert result['status'] == ('stock-files-recognized-live-validation-required' if known
                                else 'unsupported-build-no-hooks')


def test_pe_candidate_only_reads_signature(tmp_path, monkeypatch):
    image = tmp_path / 'Game.dat'
    image.write_bytes(b'MZ' + b'\0' * 4096)
    monkeypatch.setattr(Path, 'read_bytes', lambda _: pytest.fail('read entire executable'))
    assert probe.is_pe_candidate(image)
    assert not probe.is_pe_candidate(tmp_path)
    assert not probe.is_pe_candidate(tmp_path / 'absent.dll')


@pytest.fixture
def live_memory(tmp_path, monkeypatch):
    class ReadOnlyMemory:
        def __init__(self, pid, writable=False):
            assert writable is False
            self.writable = writable
            self.closed = False
            self.modules = [SimpleNamespace(path=tmp_path / 'Game.exe', name='Game.exe',
                                            base=0x400000, size=4096)]

        def read(self, address, size):
            assert address == 0x400010
            return b'ab'

        def close(self):
            self.closed = True

    memory = ReadOnlyMemory(1)
    monkeypatch.setattr('renegade.native.memory.ProcessMemory', lambda pid: memory)
    monkeypatch.setattr(probe, 'digest', lambda path: 'unknown')
    monkeypatch.setattr(probe, 'steam_anchors', lambda: {'test': {16: b'ab'}})
    return memory


def test_live_probe_reports_stock_refusal_without_installing_hooks(tmp_path, monkeypatch, live_memory):
    def refuse(memory):
        raise UnsupportedBuild('Unknown executable fingerprint')
    monkeypatch.setattr('renegade.native.inspect.verify', refuse)
    result = probe.live_inventory(1, tmp_path)
    assert result['writable'] is False
    assert result['steam_anchors_in_memory']['test']['matched'] == 1
    assert result['stock_adapter_validation'] == {
        'passed': False, 'reason': 'Unknown executable fingerprint'}
    assert live_memory.closed


def test_live_probe_rejects_process_from_other_installation(tmp_path, live_memory):
    with pytest.raises(ValueError, match='outside the requested installation'):
        probe.live_inventory(1, tmp_path / 'other-installation')
    assert live_memory.closed

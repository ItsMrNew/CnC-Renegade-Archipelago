from types import SimpleNamespace
import struct

import pytest

from tools import probe_tt_runtime as tt


@pytest.fixture
def memory(tmp_path):
    modules = [SimpleNamespace(name=name, path=tmp_path / name, base=base)
               for name, base in zip(tt.HASHES, (0x400000, 0x60000000, 0x61000000, 0x62000000))]
    by_name = {m.name: m for m in modules}
    scripts, band = by_name['scripts.dll'].base, by_name['bandtest.dll'].base
    values = {
        scripts + 0x17C70: bytes.fromhex('83ec08558b6c24108b4500a3') + struct.pack('<I', scripts + tt.COMMANDS_POINTER),
        scripts + tt.COMMANDS_POINTER: struct.pack('<I', band + tt.COMMANDS_TABLE),
        band + tt.COMMANDS_TABLE: struct.pack('<II', 0x330, 174),
        band + tt.COMMANDS_TABLE + 0x1AC: struct.pack('<I', 0x400000 + tt.MISSION_COMPLETE),
        0x400000 + tt.MISSION_COMPLETE: bytes.fromhex('8b44240450e86672f4ff59c3'),
    }
    for base, offsets in [(scripts, (0x2B33D0, 0x2B34D8, 0x2B35E0)),
                          (band, (0x31FA40, 0x31FB48, 0x31FC50))]:
        for offset in offsets:
            values[base + offset] = b'Test path\0'.ljust(260, b'\0')
    reads = []

    def read(address, size):
        reads.append(address)
        return values[address][:size]

    return SimpleNamespace(modules=modules, read=read, reads=reads, values=values,
                           u32=lambda addr: struct.unpack('<I', read(addr, 4))[0])


def digest(path):
    return tt.HASHES[path.name]


def test_verified_diagnostics_do_not_claim_adapter_support(memory):
    result = tt.inspect(memory, digest)
    assert result['layout_verified']
    assert result['native_adapter_supported'] is False
    assert result['memory_written'] is False
    assert result['commands']['owner'] == 'bandtest.dll'
    assert result['commands']['version'] == 174


def test_unknown_binary_is_rejected_before_memory_read(memory):
    result = tt.inspect(memory, lambda _: 'wrong hash')
    assert result['status'] == 'unrecognized-binaries'
    assert memory.reads == []


def test_module_from_another_install_is_rejected(memory, tmp_path):
    memory.modules[2].path = tmp_path / 'other-install' / 'scripts.dll'
    result = tt.inspect(memory, digest)
    assert result['mismatches'] == ['scripts.dll']
    assert memory.reads == []


@pytest.mark.parametrize('field', ['initializer', 'table', 'version', 'callback', 'code'])
def test_changed_live_layout_is_rejected(memory, field):
    addresses = {'initializer': 0x61017C70,
                 'table': 0x61000000 + tt.COMMANDS_POINTER,
                 'version': 0x60000000 + tt.COMMANDS_TABLE,
                 'callback': 0x60000000 + tt.COMMANDS_TABLE + 0x1AC,
                 'code': 0x400000 + tt.MISSION_COMPLETE}
    address = addresses[field]
    memory.values[address] = b'\0' * len(memory.values[address])
    result = tt.inspect(memory, digest)
    assert result['status'] == 'live-layout-mismatch'
    assert result['layout_verified'] is False
    assert result['native_adapter_supported'] is False


def test_missing_scripts_is_reported_without_memory_read(memory):
    memory.modules = memory.modules[:2]
    result = tt.inspect(memory, digest)
    assert result['status'] == 'modules-not-loaded'
    assert memory.reads == []

"""Disconnect must preserve the final native events, even after stop is set."""
import asyncio
from types import SimpleNamespace

import pytest

from renegade.client import RenegadeClient
from renegade.data import MISSIONS, disc_id, objective_id
from renegade.native import bridge, services
from renegade.native.memory import MemoryError
from test_core import Socket
from test_discs import config
from test_objective_hook import Memory
from test_primary_objectives import connect
from test_services import CONTROL


def test_steam_victory_arriving_during_disconnect_is_durable(tmp_path, monkeypatch):
    async def run():
        client = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path)
        await connect(client, Socket(), config())
        events = set()
        closed = []
        def close():
            events.add('M13')
        hook = SimpleNamespace(memory=SimpleNamespace(pid=1, close=lambda: closed.append(True)),
                               captured=lambda: events, update=lambda maps: events, close=close)
        def attach(pid):
            client.stop.set()
            client.connected = False
            return hook
        monkeypatch.setattr(bridge, 'attach', attach)
        monkeypatch.setattr(bridge, 'telemetry', lambda memory: {'mode': 1, 'map_name': 'M13'})
        await bridge.watch(client)
        expected = {MISSIONS[0].check_id(i) for i in range(1, 6)}
        assert client.progress.completed == expected
        assert closed == [True]
        # A new client must recover the outbox, rather than depending on RAM.
        reconnected = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path)
        await connect(reconnected, Socket(), config())
        assert reconnected.progress.completed == expected
    asyncio.run(run())


def test_steam_final_objective_and_disc_survive_cancel(tmp_path, monkeypatch):
    from renegade.native import discs
    async def run():
        client = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path)
        await connect(client, Socket(), config())
        memory = Memory()
        closed, receipts = [], []
        memory.pid = 1
        memory.close = lambda: closed.append(True)
        runtime = SimpleNamespace(memory=memory, control=CONTROL)
        runtime.discs = SimpleNamespace(control=CONTROL+0x10000)
        runtime.rewards = SimpleNamespace(update=lambda *args: receipts.append(args[-1]))
        def close():
            memory.write_u32(CONTROL+services.CAPTURED+4, 16)
            memory.write_u32(runtime.discs.control+discs.CAPTURED+4, 1)
        def update(*args):
            client.stop.set()
            raise asyncio.CancelledError
        runtime.close, runtime.update = close, update
        monkeypatch.setattr(services, 'attach_services', lambda *a, **kw: runtime)
        with pytest.raises(asyncio.CancelledError):
            await services.watch_services(client)
        expected = {objective_id('M01', 109), disc_id('M01', 116992)}
        assert client.progress.completed == expected
        assert receipts == [False] and closed == [True]
        reconnected = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path)
        await connect(reconnected, Socket(), config())
        assert reconnected.progress.completed == expected
    asyncio.run(run())


@pytest.mark.parametrize('failed', ['discs', 'weapons', 'launcher', 'objective_hook', 'traps', 'gate'])
def test_steam_cleanup_continues_after_independent_failure(failed):
    runtime = object.__new__(services.NativeServices)
    runtime.memory, runtime.control = Memory(), CONTROL
    closed = []
    def close(name):
        closed.append(name)
        if name == failed:
            raise MemoryError('cleanup failed: '+name)
    names = ('discs', 'weapons', 'launcher', 'bonuses', 'objective_hook', 'traps', 'gate')
    for name in names:
        setattr(runtime, name, SimpleNamespace(close=lambda name=name: close(name)))
    with pytest.raises(MemoryError, match=failed):
        runtime.close()
    assert closed == list(names)


def test_bodyguard_cleanup_keeps_its_game_callback_alive():
    runtime = object.__new__(services.NativeServices)
    runtime.memory, runtime.control = Memory(), CONTROL
    closed = []
    def close(name):
        closed.append(name)
        if name == 'bonuses':
            raise MemoryError('bodyguard needs a game frame')
    for name in ('discs', 'weapons', 'launcher', 'bonuses', 'objective_hook', 'traps', 'gate'):
        setattr(runtime, name, SimpleNamespace(close=lambda name=name: close(name)))
    with pytest.raises(MemoryError, match='bodyguard'):
        runtime.close()
    assert closed == ['discs', 'weapons', 'launcher', 'bonuses', 'objective_hook']


def test_partial_attachment_cleanup_preserves_original_error(monkeypatch):
    closed = []
    def initialize(self, *args):
        self.objective_hook = SimpleNamespace(close=lambda: closed.append(True))
        raise ValueError('attachment failed')
    monkeypatch.setattr(services.NativeServices, '_initialize', initialize)
    with pytest.raises(ValueError, match='attachment failed'):
        services.NativeServices(Memory(), ('seed', 0, 1))
    assert closed == [True]

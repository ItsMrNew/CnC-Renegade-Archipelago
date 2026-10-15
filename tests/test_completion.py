import asyncio
import json
from types import SimpleNamespace

import pytest

from renegade.data import MISSIONS, ITEM_IDS, CLEARANCE, REPORT
from renegade.native.savebuilder import BASE, MENU_SLOT, MENU_THINK, RELOAD, EXIT, COMPLETE
from renegade.native.profile import STEAM
from tools import completion_test as runner
from test_core import Socket, authenticate, received


@pytest.mark.parametrize('failure', [None, 'load', 'callback'])
def test_runner_network_ack_replay_and_cleanup(monkeypatch, tmp_path, failure):
    """Fake native execution/transport; the runner must still require its evidence."""
    game = tmp_path / 'game'
    saves = game / 'Data' / 'save'
    saves.mkdir(parents=True)
    mission = MISSIONS[0]
    name = 'ap_start_test_01_M13.sav'
    (saves / name).write_bytes(b'original save')
    manifest = dict(game_sha256=STEAM.game_sha256, missions=[dict(map_name='M13', filename=name,
        reload_verified=True, sha256=runner.digest(saves / name))])
    run = tmp_path / 'run'
    run.mkdir()
    state = dict(captured=0, pending=False, active=False, observer=False, closed=False)
    field = BASE + STEAM.commands + STEAM.mission_command_offset
    code = 0x10001000
    class Memory:
        pid = 1
        modules = [SimpleNamespace(path=game / 'Game.dat')]
        def u32(self, address):
            if address == field:
                return code if state['observer'] else BASE + STEAM.mission_complete
            if address == code - runner.CODE_OFFSET + runner.COMPLETED:
                return state['captured']
            if address == BASE + MENU_SLOT:
                return BASE + MENU_THINK
            return 0
        def read(self, address, size):
            assert address == BASE + STEAM.pending_continue
            return bytes([state['pending']])
        def write_u32(self, *args): pass
        def close(self): state['closed'] = True
    class Builder:
        memory = Memory()
        control = 0x20000000
        def prepare(self, *args): pass
        def beat(self): pass
        def wait(self, condition): assert condition()
        def settle(self): pass
        def job(self, command):
            if command == RELOAD:
                (saves / name).write_bytes(b'engine changed original')
                state.update(active=True, captured=0, pending=False)
                if failure == 'load':
                    raise RuntimeError('load failed')
            elif command == COMPLETE:
                state.update(captured=1, pending=failure != 'callback')
            elif command == EXIT:
                state.update(active=False, pending=False)
        def close(self): state['builder_restored'] = True
    class Server:
        stopped = False
        def poll(self): return 0 if self.stopped else None
        def communicate(self, *args, **kwargs): self.stopped = True
    server = Server()
    class Client(runner.AuditClient):
        async def network(self):
            sock = Socket()
            await authenticate(self, sock, starting=12)
            await self.handle(sock, dict(cmd='ReceivedItems', index=0, items=received(12)))
            while True:
                new = self.progress.completed - self.acknowledged
                if new:
                    await self.handle(sock, dict(cmd='RoomUpdate', checked_locations=sorted(new)))
                    await self.handle(sock, dict(cmd='ReceivedItems', index=len(self.inventory.items),
                        items=[dict(item=ITEM_IDS[REPORT], location=n, player=1, flags=0) for n in sorted(new)]))
                await asyncio.sleep(0.001)
    async def fake_watch(client, pid):
        state['observer'] = True
        try:
            while not state['captured']:
                await asyncio.sleep(0.001)
            client.mission_finished('M13', True)
            await asyncio.Event().wait()
        finally:
            state['observer'] = False
    monkeypatch.setattr(runner, 'AuditClient', Client)
    monkeypatch.setattr(runner, 'watch', fake_watch)
    monkeypatch.setattr(runner, 'observer_code', lambda *args: code if state['observer'] else None)
    monkeypatch.setattr(runner, 'attach_builder', lambda *args, **kwargs: Builder())
    monkeypatch.setattr(runner, 'verify_gate', lambda memory: None)
    monkeypatch.setattr(runner.subprocess, 'Popen', lambda *args, **kwargs: server)
    monkeypatch.setattr(runner, 'status', lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, 'telemetry', lambda memory: dict(mode=1, map_name='M13.lsd',
        source_name=name, pending_campaign_continue=False))
    if failure:
        with pytest.raises(RuntimeError, match='load failed|original game callback'):
            asyncio.run(runner.exercise(tmp_path, tmp_path / 'seed', run, manifest, MISSIONS[:1]))
    else:
        result = asyncio.run(runner.exercise(tmp_path, tmp_path / 'seed', run, manifest, MISSIONS[:1]))
        assert result['completed'] == 1
        assert len(result['checks']) == 3
        assert result['duplicate_replay_verified']
        assert len(result['received_items']) == 15
        assert len(result['missions']) == 2
    assert server.stopped
    assert state['closed'] and state['builder_restored'] and not state['observer'] and not state['active']
    assert (saves / name).read_bytes() == b'original save'


def test_server_goal_confirmation_requires_matching_slot(tmp_path):
    async def run():
        client = runner.AuditClient('localhost:1', 'Havoc', state_dir=tmp_path)
        client.identity = ('seed', 0, 1)
        await client.handle(Socket(), dict(cmd='PrintJSON', type='Goal', team=0, slot=2))
        assert not client.goal_confirmed
        await client.handle(Socket(), dict(cmd='PrintJSON', type='Goal', team=0, slot=1))
        assert client.goal_confirmed
    asyncio.run(run())


@pytest.mark.parametrize('name', ['../ap_start_bad.sav', 'ap_start_../bad.sav', 'C:ap_start_bad.sav'])
def test_save_manifest_cannot_escape_save_directory(tmp_path, name):
    manifest = dict(game_sha256=STEAM.game_sha256,
        missions=[dict(map_name='M13', filename=name, reload_verified=True, sha256='bad')])
    with pytest.raises(ValueError, match='filename'):
        runner.validate_saves(manifest, tmp_path, MISSIONS[:1])

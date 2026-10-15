"""The disposable probe must choose another player's non-effect item."""
import pytest
from types import SimpleNamespace
from renegade.data import ITEM_IDS, HEALTH_SUPPLY, MOON_BOOTS
from tools.cross_build_test import cross_reward, launcher_ready


def test_cross_reward_ignores_self_effects_and_unknown_locations():
    items = [dict(item=ITEM_IDS[HEALTH_SUPPLY], player=1, location=1),
             dict(item=ITEM_IDS[MOON_BOOTS], player=2, location=2),
             dict(item=ITEM_IDS[HEALTH_SUPPLY], player=2, location=3),
             dict(item=ITEM_IDS[HEALTH_SUPPLY], player=2, location=4)]
    assert cross_reward(items, 2, {1, 2, 4}) == items[3]
    with pytest.raises(ValueError, match='no suitable'):
        cross_reward(items, 2, {1, 2})


def test_steam_reload_waits_for_post_exit_menu_snapshot():
    client = SimpleNamespace(ready=True, mission_launch=None)
    assert not launcher_ready(client, 'steam')
    client.mission_launch = dict(menu=False, busy=False, can_queue=False)
    assert not launcher_ready(client, 'steam')
    client.mission_launch['menu'] = True
    assert launcher_ready(client, 'steam')
    client.mission_launch['busy'] = True
    assert not launcher_ready(client, 'steam')
    client.mission_launch['busy'] = False
    client.ready = False
    assert not launcher_ready(client, 'steam')


def test_both_backends_can_queue_when_their_loader_advertises_support():
    client = SimpleNamespace(ready=True, mission_launch=dict(menu=False, busy=False, can_queue=True))
    assert launcher_ready(client, 'tt')
    assert launcher_ready(client, 'steam')


def test_preflight_failure_replaces_previous_pass_without_attaching(tmp_path, monkeypatch):
    import json
    import sys
    from tools import cross_build_test as tool
    research = tmp_path/'.research'; research.mkdir()
    status = research/'cross-build-status.json'
    status.write_text('{"status":"passed"}')
    monkeypatch.setattr(tool, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['cross_build_test.py'])
    def fail(*args):
        raise RuntimeError('Wrong executable; use Steam Play')
    monkeypatch.setattr(tool, 'discover', fail)
    monkeypatch.setattr(tool, 'generate', lambda *args, **kwargs: pytest.fail('Must not generate after failed preflight'))
    with pytest.raises(SystemExit) as result:
        tool.main()
    assert result.value.code == 1
    report = json.loads(status.read_text())
    assert report['status'] == 'error' and report['native_hooks_used'] is False
    assert 'Wrong executable' in report['error']
    assert json.loads((next(research.glob('cross-build-*/result.json'))).read_text()) == report

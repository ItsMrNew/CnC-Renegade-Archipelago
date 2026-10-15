"""The live unlock test must observe the full sequence, not just a permission bit."""
from types import SimpleNamespace

import pytest

from renegade.data import CLEARANCE, ITEM_IDS
from tools.unlock_test import Scenario, choose_location, saved_target


SAVE = dict(mode=1, map_name="M01.lsd", source_name="save\\ap_start_02_M01.sav")
STATE = dict(blocked_map="M01", blocked_frames=80, allowed_frames=12,
             notices_shown=1, menu_returns=0)


def awaiting_reload():
    scenario = Scenario()
    assert scenario.observe(True, ("M13",), STATE, SAVE) is None
    assert scenario.phase == "waiting_for_menu_return"
    menu = dict(STATE, blocked_map=None, menu_returns=1)
    assert scenario.observe(True, ("M13",), menu, dict(mode=0)) == "send_clearance"
    assert scenario.observe(True, ("M13",), menu, dict(mode=0)) is None
    assert scenario.phase == "waiting_for_clearance"
    scenario.observe(True, ("M13", "M01"), menu, dict(mode=0))
    assert scenario.phase == "waiting_for_unlocked_reload"
    return scenario, menu


def test_real_sequence_requires_fresh_updates_of_same_save():
    scenario, state = awaiting_reload()
    unlocked = ("M13", "M01")
    assert scenario.observe(True, unlocked, state, SAVE) is None
    assert scenario.observe(True, unlocked, dict(state, allowed_frames=71), SAVE) is None
    assert scenario.observe(True, unlocked, dict(state, allowed_frames=72), SAVE) == "passed"


@pytest.mark.parametrize("snapshot", [dict(SAVE, mode=0), dict(SAVE, map_name="M13.mix"),
    dict(SAVE, source_name="M01.mix"), dict(SAVE, source_name="save\\different.sav")])
def test_other_sources_cannot_pass(snapshot):
    scenario, state = awaiting_reload()
    scenario.observe(True, ("M13", "M01"), state, snapshot)
    scenario.observe(True, ("M13", "M01"), dict(state, allowed_frames=1000), snapshot)
    assert scenario.phase == "waiting_for_unlocked_reload"
    assert scenario.baseline is None


def test_disconnect_resets_gameplay_evidence():
    scenario, state = awaiting_reload()
    scenario.observe(True, ("M13", "M01"), state, SAVE)
    scenario.observe(False, (), dict(state, allowed_frames=1000), SAVE)
    assert scenario.baseline is None
    assert scenario.observe(True, ("M13", "M01"), dict(state, allowed_frames=1100), SAVE) is None
    assert scenario.observe(True, ("M13", "M01"), dict(state, allowed_frames=1160), SAVE) == "passed"


def test_unlocked_before_lock_is_rejected():
    with pytest.raises(RuntimeError, match="already unlocked"):
        Scenario().observe(True, ("M13", "M01"), STATE, SAVE)


def test_missing_lock_cannot_send_clearance():
    scenario = Scenario()
    assert scenario.observe(True, ("M13",), dict(STATE, menu_returns=10, notices_shown=0), SAVE) is None
    assert scenario.phase == "waiting_for_locked_save"


def test_allowed_counter_wrap():
    scenario, state = awaiting_reload()
    scenario.observe(True, ("M13", "M01"), dict(state, allowed_frames=0xFFFFFFF0), SAVE)
    assert scenario.observe(True, ("M13", "M01"), dict(state, allowed_frames=44), SAVE) == "passed"


def test_scout_selects_clearance_for_receiver_not_sender():
    clearance = ITEM_IDS[CLEARANCE]
    sender = SimpleNamespace(scouted=[dict(item=clearance, location=1, player=2),
        dict(item=clearance + 1, location=2, player=1), dict(item=clearance, location=3, player=1)])
    receiver = SimpleNamespace(identity=("seed", 0, 1))
    assert choose_location(sender, receiver) == 3
    sender.scouted = None
    assert choose_location(sender, receiver) is None


@pytest.mark.parametrize("snapshot", [{}, dict(SAVE, map_name="Tutorial.mix"),
    dict(SAVE, source_name=None), dict(SAVE, mode=0)])
def test_saved_target_rejects_menu_tutorial_and_unsaved_map(snapshot):
    assert not saved_target(snapshot)

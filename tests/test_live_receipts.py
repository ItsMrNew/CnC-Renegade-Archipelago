"""Diagnostics must distinguish reservations and presses for other supplies."""
from types import SimpleNamespace
from renegade.data import SUPPLIES, ITEM_IDS
from tools.tt_effect_services_test import applied_supply_receipts


def test_key_test_waits_for_resolution_and_for_the_expected_supply():
    progress=SimpleNamespace(supply_spent={0,1},
        received=[SimpleNamespace(item=ITEM_IDS[n]) for n in SUPPLIES],
        activations={'1':{'status':'pending'}})
    assert applied_supply_receipts(progress,SUPPLIES[1],set())==set()
    assert applied_supply_receipts(progress,SUPPLIES[0],set())=={0}
    del progress.activations['1']
    assert applied_supply_receipts(progress,SUPPLIES[1],set())=={1}
    assert applied_supply_receipts(progress,SUPPLIES[1],{1})==set()


def test_refunded_or_uncertain_receipt_cannot_pass_a_key_test():
    progress=SimpleNamespace(supply_spent={0},
        received=[SimpleNamespace(item=ITEM_IDS[SUPPLIES[0]])],
        activations={'0':{'status':'uncertain'}})
    assert applied_supply_receipts(progress,SUPPLIES[0],set())==set()
    progress.supply_spent.clear();progress.activations.clear()
    assert applied_supply_receipts(progress,SUPPLIES[0],set())==set()

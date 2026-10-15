import asyncio
import json
from unittest.mock import patch

import pytest

from renegade.client import IncompatibleRoom, RenegadeClient, server_url
from renegade.data import (ALL_LOCATIONS, CLEARANCE, ITEM_IDS, MISSIONS, location_ids,
                           mission_for_map, slot_data, validate_slot_data)
from renegade.inventory import Inventory, InventorySyncRequired
from renegade.inventory import ReceivedItem
from renegade.state import Progress


def test_inventory_counts_follow_replacement_append_and_rejected_packets():
    inventory = Inventory([ReceivedItem(1, -2, 1, 0)])
    assert inventory.count(1) == 1 and inventory.count(999) == 0
    def item(code):
        return dict(item=code, location=-2, player=1, flags=0)
    inventory.apply(0, [item(2), item(2), item(3)])
    assert [inventory.count(i) for i in (1, 2, 3)] == [0, 2, 1]
    inventory.apply(3, [item(3), item(1)])
    assert [inventory.count(i) for i in (1, 2, 3)] == [1, 2, 2]
    before = inventory.items
    with pytest.raises(ValueError):
        inventory.apply(0, [item(4), dict(item=4)])
    with pytest.raises(InventorySyncRequired):
        inventory.apply(2, [item(4)])
    assert inventory.items == before and inventory.count(4) == 0
    assert [inventory.count(i) for i in (1, 2, 3)] == [1, 2, 2]
    inventory.apply(0, [])
    assert not inventory.items and all(inventory.count(i) == 0 for i in range(5))


class Socket:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.extend(json.loads(data))


def received(count):
    return [{"item": ITEM_IDS[CLEARANCE], "location": -2, "player": 1, "flags": 1}] * count


async def authenticate(client, socket, checks=3, starting=1, checked=()):
    await client.handle(socket, {"cmd": "RoomInfo", "seed_name": "seed-test"})
    await client.handle(socket, {"cmd": "Connected", "team": 0, "slot": 1,
        "slot_data": slot_data(checks, starting), "checked_locations": list(checked),
        "missing_locations": sorted(location_ids(checks) - set(checked))})


@pytest.mark.parametrize("checks", range(1, 6))
@pytest.mark.parametrize("starting", range(1, 13))
def test_slot_contract(checks, starting):
    config = slot_data(checks, starting)
    assert validate_slot_data(config) == config
    assert len(config["location_ids"]) == 12 * checks
    assert config["mission_order"] == ["M13"] + [f"M{n:02d}" for n in range(1, 12)]


def test_ids_and_map_normalization():
    assert len(ALL_LOCATIONS) == len(set(ALL_LOCATIONS.values())) == 190
    assert mission_for_map("m13.mix") == MISSIONS[0]
    assert mission_for_map("M11.lsd") == MISSIONS[-1]
    for invalid in ("M00_Tutorial", "C&C_Field.mix", "M12", "M01.mix.exe", "../M01", None):
        with pytest.raises(ValueError):
            mission_for_map(invalid)


@pytest.mark.parametrize("key,value", [("checks_per_mission", True), ("checks_per_mission", 0),
    ("starting_missions", 13), ("protocol_version", True), ("goal", "final_mission"),
    ("location_ids", []), ("mission_order", ["M01"])])
def test_bad_contract(key, value):
    data = slot_data()
    data[key] = value
    with pytest.raises(ValueError):
        validate_slot_data(data)


def test_inventory_resync():
    inventory = Inventory()
    with pytest.raises(InventorySyncRequired):
        inventory.apply(1, received(1))
    inventory.apply(0, received(2))
    inventory.apply(2, received(1))
    assert inventory.count(ITEM_IDS[CLEARANCE]) == 3
    with pytest.raises(InventorySyncRequired):
        inventory.apply(9, received(1))
    assert not inventory.synchronized
    inventory.apply(0, received(1))
    assert inventory.count(ITEM_IDS[CLEARANCE]) == 1


def test_victory_unlock_replay_and_acknowledgement(tmp_path):
    async def run():
        client = RenegadeClient("localhost:1", "Havoc", state_dir=tmp_path)
        socket = Socket()
        assert not client.mission_finished("M13", True)
        await authenticate(client, socket)
        assert not client.mission_finished("M13", True)
        await client.handle(socket, {"cmd": "ReceivedItems", "index": 0, "items": received(1)})
        assert not client.mission_finished("M01", True)
        assert not client.mission_finished("M13", False)
        assert client.mission_finished("M13", True)
        assert not client.mission_finished("M13", True)
        await client.flush_once(socket)
        assert len(socket.sent[-1]["locations"]) == 3
        assert not client.goal_sent
        await client.handle(socket, {"cmd": "ReceivedItems", "index": 0, "items": received(12)})
        for mission in MISSIONS:
            client.mission_finished(mission.map_name, True)
        await client.flush_once(socket)
        assert not client.goal_sent  # Local completions alone are insufficient.
        await client.handle(socket, {"cmd": "RoomUpdate", "checked_locations": sorted(client.locations)})
        await client.flush_once(socket)
        await client.flush_once(socket)
        assert [p for p in socket.sent if p["cmd"] == "StatusUpdate"] == [{"cmd": "StatusUpdate", "status": 30}]
    asyncio.run(run())


def test_restore_waits_for_inventory_and_retries_outbox(tmp_path):
    async def run():
        socket = Socket()
        first = RenegadeClient("localhost:1", "Havoc", state_dir=tmp_path)
        await authenticate(first, socket)
        await first.handle(socket, {"cmd": "ReceivedItems", "index": 0, "items": received(12)})
        first.mission_finished("M13", True)
        second = RenegadeClient("localhost:1", "Havoc", state_dir=tmp_path)
        await authenticate(second, socket)
        assert not second.ready and not second.unlocked_maps
        assert not second.mission_finished("M01", True)
        assert second.progress.completed == first.progress.completed
        await second.flush_once(socket)
        assert socket.sent[-1]["cmd"] == "LocationChecks"
        await second.handle(socket, {"cmd": "ReceivedItems", "index": 0, "items": received(1)})
        assert second.unlocked_maps == ("M13",)
        await second.handle(socket, {"cmd": "ReceivedItems", "index": 4, "items": received(1)})
        assert not second.ready
        assert socket.sent[-2]["cmd"] == "Sync"
        with pytest.raises(IncompatibleRoom):
            await second.handle(socket, {"cmd": "RoomInfo", "seed_name": "different"})
    asyncio.run(run())


def test_progress_isolation_and_atomic_failure(tmp_path):
    config = slot_data()
    progress = Progress(tmp_path, "seed", 0, 1, location_ids(3), config)
    with patch("renegade.state.os.replace", side_effect=OSError("disk error")):
        with pytest.raises(OSError):
            progress.merge({MISSIONS[0].check_id()})
    assert not progress.completed
    assert not list(tmp_path.glob("*.tmp"))
    progress.merge({MISSIONS[0].check_id()})
    for seed, team, slot in (("other", 0, 1), ("seed", 1, 1), ("seed", 0, 2)):
        other = Progress(tmp_path, seed, team, slot, location_ids(3), config)
        assert not other.completed and other.path != progress.path
    progress.path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        Progress(tmp_path, "seed", 0, 1, location_ids(3), config)


@pytest.mark.parametrize("value,expected", [("archipelago.gg:123", "wss://archipelago.gg:123"),
    ("localhost:123", "ws://localhost:123"), ("wss://example.com:1", "wss://example.com:1")])
def test_server_url(value, expected):
    assert server_url(value) == expected


def test_packaged_contract_matches():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert (root / "renegade/data.py").read_bytes() == (root / "worlds/renegade/data.py").read_bytes()

"""Archipelago transport; deliberately independent of executable offsets."""
import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .data import GAME, ITEM_IDS, MISSIONS, SUPPLIES, TRAPS, BUFFS, WEAPON_ITEMS, location_ids, mission_for_map, mission_requirement, validate_slot_data, objective_id, objective_title, objective_kind, VICTORY_PRIMARY_OBJECTIVES, disc_id, disc_title
from .deathlink import DeathLinkState
from .inventory import Inventory, InventorySyncRequired
from .state import Progress
from .notifications import ItemNotifications, item_flag

LOG = logging.getLogger("Renegade")


class IncompatibleRoom(ValueError):
    pass


def server_url(address):
    address = address.strip()
    if not address:
        raise ValueError("A server address is required")
    if "://" not in address:
        host = (urlsplit("//" + address).hostname or "").lower().rstrip(".")
        scheme = "wss" if host == "archipelago.gg" or host.endswith(".archipelago.gg") else "ws"
        address = scheme + "://" + address
    parsed = urlsplit(address)
    if (parsed.scheme not in {"ws", "wss"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError("Use host:port, ws://host:port or wss://host:port")
    _ = parsed.port
    return address


class RenegadeClient:
    def __init__(self, server, slot, password=None, state_dir=Path(".state")):
        self.server = server_url(server)
        self.slot, self.password = slot, password
        self.state_dir = Path(state_dir)
        self.uuid = str(uuid.uuid4())
        self.seed = self.identity = self.config = self.progress = None
        self.locations = frozenset()
        self.inventory = Inventory()
        self.acknowledged = set()
        self.connected = False
        self.goal_sent = False
        self.changed = asyncio.Event()
        self.stop = asyncio.Event()
        self.deathlink = DeathLinkState()
        self.supply_request = None
        self.notifications = ItemNotifications()
        # Only a live, authenticated inventory grants this session's offline
        # capture permissions. A disk cache alone never authorizes gameplay.
        self.capture_permissions = None
        self.weapon_permissions = 0
        self.mission_request = None
        self.mission_confirmation = None
        self.mission_launch = None
        self.attempt = None  # Volatile native failure evidence; never AP completion.
        self.tracker_sample = None
        self.tracker_game_state = 'waiting'
        self.recovery_checked = False

    def supply_indexes(self, name):
        return self.effect_indexes(name) if name in SUPPLIES else []

    def effect_indexes(self, name):
        if not self.ready or not self.config.get("supply_items") or name not in (*SUPPLIES, *TRAPS, *BUFFS):
            return []
        return [i for i, item in enumerate(self.inventory.items)
                if item.item == ITEM_IDS[name] and i not in self.progress.supply_spent]

    def request_supply(self, name):
        if self.supply_request is not None or not self.supply_indexes(name):
            return False
        self.supply_request = name
        LOG.info("%s queued; return to an active unlocked mission to use it", name)
        return True

    def resolve_uncertain_items(self, refund):
        if not self.ready:
            return
        for key, record in tuple(self.progress.activations.items()):
            if record['status'] == 'uncertain':
                self.progress.resolve_activation(int(key), 'refunded' if refund else 'applied')
        LOG.info('Unresolved item activations %s', 'returned to inventory' if refund else 'marked as used')

    @property
    def ready(self):
        return self.connected and self.inventory.synchronized

    @property
    def capture_ready(self):
        return not self.stop.is_set() and (self.ready or self.capture_permissions is not None)

    @property
    def capture_maps(self):
        return (self.unlocked_maps if self.ready else self.capture_permissions or ()) if self.capture_ready else ()

    @property
    def unlocked_maps(self):
        if not self.ready:
            return ()
        mode = self.config.get('mission_unlocks', 'progressive')
        return tuple(m.map_name for m in MISSIONS
                     if self.inventory.count(ITEM_IDS[mission_requirement(m, mode)[0]]) >= mission_requirement(m, mode)[1])

    @property
    def weapon_mask(self):
        """Authenticated session cache survives drops, never authorizes a fresh session."""
        if not self.capture_ready or not self.config.get('weapon_unlocks', False):
            return 0
        return self.weapon_permissions

    def mission_finished(self, map_name, success):
        """Future native adapter entry point. Never call for a load or failure."""
        if success is not True or not self.capture_ready:
            return False
        mission = mission_for_map(map_name)
        if mission.map_name not in self.capture_maps:
            LOG.warning("Ignored completion of locked mission %s", mission.label)
            return False
        checks = {mission.check_id(c) for c in range(1, self.config["checks_per_mission"] + 1)}
        if self.config.get('primary_objectives'):
            checks.update(objective_id(mission.map_name, oid)
                          for oid in VICTORY_PRIMARY_OBJECTIVES.get(mission.map_name, ()))
        if checks <= self.progress.completed:
            return False
        self.progress.merge(checks)  # Persist before sending; safe to retry after disconnect.
        self.changed.set()
        LOG.info("Recorded %s (%d checks)", mission.label, len(checks))
        return True

    def objective_finished(self, map_name, oid):
        if not self.capture_ready or map_name not in self.capture_maps:
            return False
        if not self.config.get(objective_kind(map_name, oid) + '_objectives', False):
            return False
        check = objective_id(map_name, oid)
        if check in self.progress.completed:
            return False
        self.progress.merge({check})
        self.changed.set()
        LOG.info("Recorded %s: %s (objective %d)", map_name, objective_title(map_name, oid), oid)
        return True

    def disc_collected(self, map_name, object_id):
        if not self.capture_ready or not self.config.get('data_discs', False):
            return False
        if map_name not in self.capture_maps:
            return False
        check = disc_id(map_name, object_id)
        if check not in self.locations:
            return False  # Older rooms do not contain the scripted intel reward.
        if check in self.progress.completed:
            return False
        self.progress.merge({check})
        self.changed.set()
        LOG.info('Recorded %s: %s', map_name, disc_title(map_name, object_id))
        return True

    def player_died(self, map_name):
        if self.ready and self.deathlink.local_death(self.slot, map_name):
            self.changed.set()
            return True
        return False

    async def send(self, socket, *messages):
        await socket.send(json.dumps(list(messages)))

    async def handle(self, socket, packet):
        if not isinstance(packet, dict):
            raise IncompatibleRoom("Invalid server command")
        command = packet.get("cmd")
        if command == "RoomInfo":
            seed = packet.get("seed_name")
            if not isinstance(seed, str) or not seed or (self.identity and seed != self.identity[0]):
                raise IncompatibleRoom("Missing or changed seed identity; start a new client for a new room")
            self.seed = seed
            await self.send(socket, {"cmd": "Connect", "game": GAME, "name": self.slot,
                "password": self.password, "uuid": self.uuid,
                "version": {"major": 0, "minor": 6, "build": 7, "class": "Version"},
                "items_handling": 7, "tags": ["AP"], "slot_data": True})
        elif command == "ConnectionRefused":
            raise IncompatibleRoom("Connection refused: " + ", ".join(packet.get("errors", [])))
        elif command == "Connected":
            if not self.seed or any(type(packet.get(key)) is not int for key in ("team", "slot")):
                raise IncompatibleRoom("Invalid connection identity")
            try:
                config = validate_slot_data(packet.get("slot_data"))
            except ValueError as error:
                raise IncompatibleRoom(str(error)) from error
            identity = (self.seed, packet["team"], packet["slot"])
            if self.identity and (identity != self.identity or config != self.config):
                raise IncompatibleRoom("Room identity or configuration changed; restart the client")
            locations = frozenset(config['location_ids'])  # Validated, version-specific catalogue.
            checked = self.parse_checks(packet.get("checked_locations", []), locations)
            missing = self.parse_checks(packet.get("missing_locations", []), locations)
            if checked & missing or checked | missing != locations:
                raise IncompatibleRoom("Room location table does not match slot data")
            progress = Progress(self.state_dir, *identity, locations, config)
            progress.merge(checked)
            self.identity, self.config, self.locations = identity, config, locations
            self.progress, self.acknowledged = progress, checked
            if not self.recovery_checked:
                from .native.recovery import recover_activations
                recover_activations(self)
                self.recovery_checked = True
            self.inventory = Inventory(progress.received)  # Cache is never permission to play.
            self.connected = True
            self.goal_sent = False
            self.deathlink.enabled = config.get("death_link", False)
            self.deathlink.clear_pending()
            if self.deathlink.enabled:
                await self.send(socket, {"cmd": "ConnectUpdate", "tags": ["AP", "DeathLink"]})
            games = self.notifications.connected(packet, self.notifications.settings)
            await self.send(socket, {"cmd": "Sync"}, {"cmd": "GetDataPackage", "games": games})
            self.changed.set()
            LOG.info("Authenticated as %s; %d/%d checks confirmed", self.slot, len(checked), len(locations))
        elif command == "ReceivedItems":
            if not self.connected:
                raise IncompatibleRoom("Items arrived before authentication")
            old_received = self.progress.received
            try:
                received = self.inventory.apply(packet.get("index"), packet.get("items"))
            except InventorySyncRequired:
                await self.send(socket, {"cmd": "Sync"},
                    {"cmd": "LocationChecks", "locations": sorted(self.progress.completed)})
                return
            self.progress.record_inventory(received)
            self.capture_permissions = self.unlocked_maps
            self.weapon_permissions = sum(1 << (i + 1) for i, name in enumerate(WEAPON_ITEMS)
                                          if self.inventory.count(ITEM_IDS[name])) | 1  # Pistol.
            for item in received[len(old_received):]:
                name = next((n for n,i in ITEM_IDS.items() if i == item.item), None)
                if name:
                    LOG.info("Received %s", name, extra={"item_parts": [
                        ('Received ', None), (name, item_flag(item.flags, location=item.location))]})
            self.changed.set()
            LOG.info("Inventory synchronized; %d missions unlocked", len(self.unlocked_maps))
        elif command == "DataPackage" and self.connected:
            self.notifications.data_package(packet)
        elif command == "PrintJSON" and self.connected:
            event = self.notifications.receive(packet)
            if event and self.notifications.accepts(event):
                LOG.info("%s", self.notifications.text(event), extra={"item_parts": self.notifications.message_parts(event)})
        elif command == "RoomUpdate" and self.connected:
            self.notifications.update_players(packet.get("players", []))
            checks = self.parse_checks(packet.get("checked_locations", []), self.locations)
            self.progress.merge(checks)
            self.acknowledged |= checks
            self.changed.set()
        elif command == "Bounced" and self.ready:
            if self.deathlink.receive(packet, self.slot):
                LOG.info("DeathLink received from %s", self.deathlink.incoming[-1]["source"])
        elif command in {"InvalidPacket", "InvalidArguments"}:
            raise IncompatibleRoom(f"Server rejected a packet: {packet}")

    @staticmethod
    def parse_checks(values, allowed):
        if not isinstance(values, list) or any(type(v) is not int or v not in allowed for v in values):
            raise IncompatibleRoom("Invalid location IDs from server")
        return set(values)

    async def flush_once(self, socket):
        if not self.connected:
            return
        pending = self.progress.completed - self.acknowledged
        if pending:
            await self.send(socket, {"cmd": "LocationChecks", "locations": sorted(pending)})
        while self.ready and self.deathlink.enabled and self.deathlink.outgoing:
            packet = self.deathlink.outgoing.popleft()
            await self.send(socket, packet)
            self.deathlink.sent += 1
            LOG.info("DeathLink sent")
        victory_checks = location_ids(self.config["checks_per_mission"])
        if victory_checks <= self.acknowledged and not self.goal_sent:
            await self.send(socket, {"cmd": "StatusUpdate", "status": 30})
            self.goal_sent = True
            LOG.info("All campaign mission checks confirmed; goal sent")

    async def flush(self, socket):
        while True:
            try:
                await asyncio.wait_for(self.changed.wait(), 3)
            except TimeoutError:
                pass
            self.changed.clear()
            await self.flush_once(socket)

    async def receive(self, socket):
        async for message in socket:
            packets = json.loads(message)
            if not isinstance(packets, list):
                raise IncompatibleRoom("Server packets must be a list")
            for packet in packets:
                await self.handle(socket, packet)

    async def network(self):
        while not self.stop.is_set():
            self.connected = False
            self.inventory.synchronized = False
            try:
                async with connect(self.server, open_timeout=10, max_size=16 * 1024 * 1024) as socket:
                    tasks = [asyncio.create_task(self.receive(socket)),
                             asyncio.create_task(self.flush(socket)),
                             asyncio.create_task(self.stop.wait())]
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except (OSError, TimeoutError, WebSocketException) as error:
                LOG.warning("Connection lost or unavailable: %s. Retrying in 3 seconds", error)
            finally:
                self.connected = False
                self.inventory.synchronized = False
                self.deathlink.clear_pending()
            if not self.stop.is_set():
                try:
                    await asyncio.wait_for(self.stop.wait(), 3)
                except TimeoutError:
                    pass

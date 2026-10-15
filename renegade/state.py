"""Seed/team/slot-scoped durable check outbox, adapted from Zero Hour."""
import hashlib
import json
import os
import time
from pathlib import Path
import uuid
from .data import GAME
from .inventory import ReceivedItem


class Progress:
    def __init__(self, directory, seed, team, slot, locations, config):
        self.identity = {"game": GAME, "seed": seed, "team": team, "slot": slot}
        key = hashlib.sha256(json.dumps(self.identity, sort_keys=True).encode()).hexdigest()
        self.path = Path(directory) / f"{key}.json"
        self.locations = frozenset(locations)
        self.config = config
        self.completed = set()
        self.received = ()
        self.supply_spent = set()
        self.activations = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"Invalid saved progress: {self.path}")
            if (data.get("version") != 1 or data.get("identity") != self.identity
                    or data.get("config") != config):
                raise ValueError(f"Progress identity or configuration mismatch: {self.path}")
            completed = data.get("completed")
            if not isinstance(completed, list) or any(
                    type(value) is not int or value not in self.locations for value in completed):
                raise ValueError("Invalid saved check IDs")
            if not isinstance(data.get("received"), list):
                raise ValueError("Invalid saved inventory")
            self.completed = set(completed)
            self.received = tuple(ReceivedItem.parse(item) for item in data["received"])
            spent = data.get("supply_spent", [])
            if not isinstance(spent, list) or any(type(i) is not int or not 0 <= i < len(self.received) for i in spent):
                raise ValueError("Invalid spent supply indexes")
            self.supply_spent = set(spent)
            activations = data.get('activations', {})
            if not isinstance(activations, dict):
                raise ValueError('Invalid activation journal')
            for key, record in activations.items():
                if (not key.isdecimal() or str(int(key)) != key or int(key) not in self.supply_spent
                        or not isinstance(record, dict) or record.get('status') not in ('pending', 'uncertain')
                        or type(record.get('pid')) is not int or type(record.get('control')) is not int
                        or not isinstance(record.get('cookie'), str) or len(record['cookie']) != 32):
                    raise ValueError('Invalid activation journal entry')
                try:
                    bytes.fromhex(record['cookie'])
                except ValueError as error:
                    raise ValueError('Invalid activation receipt cookie') from error
            self.activations = activations

    def merge(self, locations):
        if not set(locations) <= self.locations:
            raise ValueError("Unknown mission checks")
        completed = self.completed | set(locations)
        if completed != self.completed:
            self.save(completed, self.received)

    def record_inventory(self, received):
        if any(i >= len(received) or received[i] != self.received[i] for i in self.supply_spent):
            raise ValueError("Received item stream changed an already-spent reward")
        if tuple(received) != self.received:
            self.save(self.completed, tuple(received))

    def mark_supply(self, index, spent=True):
        from .data import ITEM_IDS, SUPPLIES, TRAPS, BUFFS
        if (type(index) is not int or not 0 <= index < len(self.received)
                or self.received[index].item not in {ITEM_IDS[n] for n in (*SUPPLIES, *TRAPS, *BUFFS)}):
            raise ValueError("Invalid consumable reward index")
        updated = self.supply_spent | {index} if spent else self.supply_spent - {index}
        self.save(self.completed, self.received, updated)

    def reserve_activation(self, index, receipt):
        if index in self.supply_spent:
            raise ValueError('Reward already reserved')
        from .data import ITEM_IDS, SUPPLIES, TRAPS, BUFFS
        if (type(index) is not int or not 0 <= index < len(self.received)
                or self.received[index].item not in {ITEM_IDS[n] for n in (*SUPPLIES, *TRAPS, *BUFFS)}):
            raise ValueError('Invalid consumable activation')
        journal = {**self.activations, str(index): dict(receipt, status='pending')}
        self.save(self.completed, self.received, self.supply_spent | {index}, journal)

    def resolve_activation(self, index, outcome):
        if outcome not in ('applied', 'refunded', 'uncertain') or str(index) not in self.activations:
            raise ValueError('Invalid activation resolution')
        journal = {k: dict(v) for k,v in self.activations.items()}
        if outcome == 'uncertain':
            journal[str(index)]['status'] = outcome
        else:
            del journal[str(index)]
        spent = self.supply_spent - {index} if outcome == 'refunded' else self.supply_spent
        self.save(self.completed, self.received, spent, journal)

    def save(self, completed, received, supply_spent=None, activations=None):
        supply_spent = self.supply_spent if supply_spent is None else supply_spent
        activations = self.activations if activations is None else activations
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + f".{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump({"version": 1, "identity": self.identity, "config": self.config,
                           "completed": sorted(completed),
                           "supply_spent": sorted(supply_spent),
                           "activations": activations,
                           "received": [item.as_dict() for item in received]}, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            # Windows scanners can briefly deny replacement of a just-written
            # progress file. Keep the fully flushed temp and retry briefly;
            # sustained failure still propagates so native evidence is retained.
            for attempt in range(4):
                try:
                    os.replace(temp, self.path)
                    break
                except PermissionError:
                    if attempt == 3:
                        raise
                    time.sleep(.02 * (attempt + 1))
        finally:
            temp.unlink(missing_ok=True)
        self.completed = set(completed)
        self.received = tuple(received)
        self.supply_spent = set(supply_spent)
        self.activations = activations

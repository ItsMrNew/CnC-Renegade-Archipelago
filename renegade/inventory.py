"""Archipelago's authoritative received-item stream, independent of game effects."""
from dataclasses import dataclass
from collections import Counter


class InventorySyncRequired(ValueError):
    pass


@dataclass(frozen=True)
class ReceivedItem:
    item: int
    location: int
    player: int
    flags: int

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Received item must be an object")
        fields = [value.get(key) for key in ("item", "location", "player", "flags")]
        if any(type(field) is not int for field in fields):
            raise ValueError("Received item fields must be integers")
        return cls(*fields)

    def as_dict(self):
        return {"item": self.item, "location": self.location, "player": self.player, "flags": self.flags}


class Inventory:
    def __init__(self, received=()):
        self.items = tuple(received)
        self._counts = Counter(item.item for item in self.items)
        self.synchronized = False

    def apply(self, index, values):
        if type(index) is not int or index < 0 or not isinstance(values, list):
            raise ValueError("Invalid ReceivedItems packet")
        incoming = tuple(ReceivedItem.parse(value) for value in values)
        # Index zero replaces the entire inventory, including on reconnection.
        if index == 0:
            self.items = incoming
            self._counts = Counter(item.item for item in incoming)
            self.synchronized = True
        elif not self.synchronized or index != len(self.items):
            self.synchronized = False
            raise InventorySyncRequired("Item stream is out of sequence")
        else:
            self.items += incoming
            self._counts.update(item.item for item in incoming)
        return self.items

    def count(self, item_id):
        return self._counts[item_id]


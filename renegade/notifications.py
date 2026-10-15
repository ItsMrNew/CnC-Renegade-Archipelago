"""Live AP item-transfer feed, independent of simulation time and inventory replay."""
from collections import deque
import re
import unicodedata

from .data import GAME, ITEM_IDS, ITEM_COLOURS

DEFAULT_NOTIFICATIONS = {'scope': 'all'}
UNKNOWN_ITEM_COLOUR = '#AEB6C2'


def item_flag(flags, *, location=0):
    """Use this transfer's server flags, never a game/name classification table.

    AP console grants use negative locations and can default flags to zero
    without looking up the item's type. That zero is unknown, not known filler.
    """
    if type(flags) is not int or flags < 0 or (location < 0 and flags == 0):
        return None
    return next((flag for flag in (1, 2, 4) if flags & flag), 0 if flags == 0 else None)


def item_colour(flags):
    return ITEM_COLOURS.get(item_flag(flags), UNKNOWN_ITEM_COLOUR)


def validate_settings(value):
    if (not isinstance(value, dict) or set(value) != set(DEFAULT_NOTIFICATIONS)
            or value['scope'] not in ('you', 'all', 'off')):
        raise ValueError('Invalid notification preferences.')
    return dict(value)


def safe_text(text, limit=63):
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'replace').decode()
    text = ' '.join(''.join(ch if ch.isprintable() else ' ' for ch in text).split())
    return text if len(text) <= limit else text[:limit-3] + '...'


class ItemNotifications:
    def __init__(self):
        self.players, self.slots = {}, {}
        self.items = {GAME: {code: name for name, code in ITEM_IDS.items()}}
        self.queue = deque(maxlen=256)
        self.seen = set()
        self.settings = dict(DEFAULT_NOTIFICATIONS)
        self.team, self.slot = 0, 0

    def connected(self, packet, settings):
        self.team, self.slot = packet['team'], packet['slot']
        self.update_players(packet.get('players', []))
        self.slots.update({int(k): v for k, v in packet.get('slot_info', {}).items()})
        self.configure(settings)
        return sorted({v['game'] for v in self.slots.values() if v.get('game')})

    def update_players(self, players):
        for player in players:
            if isinstance(player, dict) and player.get('team') == self.team:
                self.players[player['slot']] = player.get('alias') or player.get('name') or str(player['slot'])

    def data_package(self, packet):
        for game, data in packet.get('data', {}).get('games', {}).items():
            self.items[game] = {code: name for name, code in data.get('item_name_to_id', {}).items()}

    def configure(self, settings):
        settings = validate_settings(settings)
        self.settings = settings
        self.queue = deque((event for event in self.queue if self.accepts(event)), maxlen=256)

    def notice(self, text, flags=0):
        self.queue.append((None, None, text, -2, flags))

    def accepts(self, event):
        if event[0] is None:
            return True
        return self.settings['scope'] == 'all' or (self.settings['scope'] == 'you'
            and (self.slot in (event[0], event[1]) or self.slot in self.slots.get(event[1], {}).get('group_members', [])))

    def receive(self, packet):
        if packet.get('type') is None:
            return self.receive_server_send(packet)
        if packet.get('type') not in ('ItemSend', 'ItemCheat'):
            return
        if packet.get('team', self.team) != self.team:
            return
        item, recipient = packet.get('item'), packet.get('receiving')
        if not isinstance(item, dict) or type(recipient) is not int:
            return
        if any(type(item.get(k)) is not int for k in ('player', 'item', 'location')):
            return
        flags = item_flag(item.get('flags'), location=item['location'])
        event = (item['player'], recipient, item['item'], item['location'], flags)
        # Real locations are unique. Cheat deliveries (-1) can legitimately repeat.
        if item['location'] >= 0:
            if event[:4] in self.seen:
                return
            self.seen.add(event[:4])
        if self.accepts(event):
            self.queue.append(event)
        return event

    def receive_server_send(self, packet):
        """AP 0.6.7 /send[_multiple] lacks ItemCheat metadata (unlike !getitem).

        Parse only the server's exact single-text notice. Do not derive toasts
        from ReceivedItems: that would replay inventory and duplicate transfers.
        """
        if packet.get('cmd') != 'PrintJSON' or packet.get('team', self.team) != self.team:
            return
        parts = packet.get('data')
        if (not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], dict)
                or parts[0].get('type', 'text') != 'text' or not isinstance(parts[0].get('text'), str)):
            return
        match = re.fullmatch(r'Cheat console: sending (?:(\d{1,3}) of )?"(.+)" to (.+)', parts[0]['text'])
        if not match:
            return
        count, item, recipient_name = match.groups()
        count = int(count or '1')
        if not 1 <= count <= 100:
            return
        # get_aliased_name emits either the slot name or "Alias (Slot name)".
        matches = []
        for slot in set(self.slots) | set(self.players):
            original = self.slots.get(slot, {}).get('name')
            alias = self.players.get(slot)
            names = {name for name in (original, alias, f'{alias} ({original})' if alias and original else None) if name}
            if recipient_name in names:
                matches.append(slot)
        if len(matches) != 1:
            return  # Do not attribute an ambiguous player name to the wrong slot.
        recipient = matches[0]
        # This plain-text server message contains no classification metadata.
        event = (0, recipient, item if count == 1 else f'{item} x{count}', -1, None)
        if self.accepts(event):
            self.queue.append(event)
        return event

    def name(self, slot):
        return self.players.get(slot, self.slots.get(slot, {}).get('name', 'Server' if slot == 0 else f'Player {slot}'))

    def message_parts(self, event):
        sender, recipient, item, _, flags = event
        game = self.slots.get(recipient, {}).get('game', GAME if recipient == self.slot else '')
        name = item if isinstance(item, str) else self.items.get(game, {}).get(item, f'Item {item}')
        return [(f'{self.name(sender)} sent ', None), (name, item_flag(flags)),
                (f' to {self.name(recipient)}', None)]

    def log_parts(self, event):
        return [(text, item_colour(flag) if flag is not None else None)
                for text, flag in self.message_parts(event)]

    def text(self, event):
        return event[2] if event[0] is None else ''.join(part for part, _colour in self.log_parts(event))

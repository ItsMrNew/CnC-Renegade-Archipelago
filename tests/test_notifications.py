import asyncio
import struct
from types import SimpleNamespace

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ESP

from renegade.notifications import ItemNotifications, item_flag, item_colour, safe_text, UNKNOWN_ITEM_COLOUR
from renegade.data import GAME, ITEM_IDS, HEALTH_SUPPLY, ARMOUR_TRAP
from renegade.client import RenegadeClient
from renegade.hotkeys import SupplyHotkeys, KEYS, binding_conflicts
from renegade.native import notifications as n, services as s
from test_core import Socket, authenticate, received
from test_services import Engine, CONTROL, BASE


def feed():
    f = ItemNotifications()
    f.connected(dict(team=0, slot=1, players=[dict(team=0, slot=1, alias='Havoc'),
        dict(team=0, slot=2, alias='Sakura'), dict(team=0, slot=3, alias='Link')],
        slot_info={'1': dict(name='Havoc', game=GAME), '2': dict(name='Sakura', game=GAME),
                   '3': dict(name='Link', game='Other Game'),
                   '4': dict(name='Shared', game=GAME, group_members=[1, 2])}), {'scope': 'all'})
    return f


def packet(sender=2, recipient=1, item=None, location=100, kind='ItemSend'):
    return dict(cmd='PrintJSON', type=kind, receiving=recipient,
                item=dict(player=sender, item=item or ITEM_IDS[HEALTH_SUPPLY], location=location, flags=0))


def test_cross_game_names_use_recipient_game_and_alias_updates():
    f = feed()
    f.data_package({'data': {'games': {'Other Game': {'item_name_to_id': {'Hookshot': 42}}}}})
    event = f.receive(packet(recipient=3, item=42))
    assert f.text(event) == 'Sakura sent Hookshot to Link'
    f.update_players([dict(team=0, slot=3, alias='Zelda'), dict(team=1, slot=3, alias='Wrong team')])
    assert f.text(event).endswith('to Zelda')


def test_scopes_group_recipient_and_duplicate_checks():
    f = feed()
    f.configure({'scope': 'you'})
    f.receive(packet(sender=2, recipient=3))
    assert not f.queue
    f.receive(packet(sender=1, recipient=3, location=101))
    f.receive(packet(sender=2, recipient=4, location=102))
    assert len(f.queue) == 2
    assert f.receive(packet(sender=2, recipient=4, location=102)) is None
    f.configure({'scope': 'off'})
    assert not f.queue
    f.notice('Health Supply: applied')
    assert f.text(f.queue[0]) == 'Health Supply: applied'


def test_unclassified_cheats_repeat_without_inventing_a_type():
    f = feed()
    p = packet(item=ITEM_IDS[ARMOUR_TRAP], location=-1, kind='ItemCheat')
    assert f.receive(p)[4] is None
    f.receive(p)
    assert len(f.queue) == 2
    text = dict(cmd='PrintJSON', data=[dict(text='Cheat console: sending 3 of "Armour Loss Trap" to Havoc')])
    event = f.receive(text)
    assert f.text(event) == 'Server sent Armour Loss Trap x3 to Havoc' and event[4] is None
    f.receive(text)
    assert len(f.queue) == 4
    assert f.receive(dict(cmd='PrintJSON', data=[dict(text='Havoc: '+text['data'][0]['text'])])) is None
    assert f.receive({**text, 'team': 1}) is None
    f.slots[3]['name'] = 'Havoc'
    assert f.receive(text) is None  # Ambiguous aliases cannot select a recipient.


def test_server_classification_wins_over_local_temporary_bonus_definitions():
    from renegade.data import BUFFS
    f=feed()
    for name in BUFFS:
        p=packet(item=ITEM_IDS[name],location=-1,kind='ItemCheat');p['item']['flags']=2
        event=f.receive(p)
        assert event[4]==2 and f.message_parts(event)[1]==(name,2)
        assert f.log_parts(event)[1]==(name,'#6D8BE8')
        p['receiving']=3
        assert f.receive(p)[4]==2


@pytest.mark.parametrize('game', ['The Last of Us Part I', 'Unfamiliar Future Game'])
@pytest.mark.parametrize('flags,colour', [(0, '#00EEEE'), (1, '#AF99EF'), (2, '#6D8BE8'),
                                       (4, '#FA8072'), (6, '#6D8BE8'), (7, '#AF99EF')])
def test_any_games_seeded_transfers_use_the_server_type(game, flags, colour):
    f = feed()
    f.slots[3]['game'] = game
    f.data_package({'data': {'games': {game: {
        'item_name_to_id': {'Airhorn Trap': 19710004}}}}})
    p = packet(recipient=3, item=19710004)
    p['item']['flags'] = flags
    event = f.receive(p)
    assert f.log_parts(event)[1] == ('Airhorn Trap', colour)


@pytest.mark.parametrize('flags', [None, False, '4', -1, 8, 0, 4])
@pytest.mark.parametrize('kind', ['ItemSend', 'ItemCheat'])
def test_unclassified_grants_and_invalid_metadata_are_neutral(flags, kind):
    f = feed()
    p = packet(item=ITEM_IDS[ARMOUR_TRAP], location=-1, kind=kind)
    p['item']['flags'] = flags
    if flags is None:
        del p['item']['flags']
    event = f.receive(p)
    expected = 4 if flags == 4 else None
    assert event[4] == expected
    assert f.message_parts(event)[1] == (ARMOUR_TRAP, expected)
    assert item_colour(event[4]) == ('#FA8072' if expected == 4 else UNKNOWN_ITEM_COLOUR)


def test_receipt_log_uses_each_received_items_server_flags(tmp_path, caplog):
    async def run():
        c = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path)
        sock = Socket()
        await authenticate(c, sock)
        values = [dict(item=ITEM_IDS[ARMOUR_TRAP], player=2, location=location, flags=flags)
                  for location, flags in ((100, 1), (101, 2), (102, 4), (103, 0), (-1, 0))]
        with caplog.at_level('INFO', logger='Renegade'):
            await c.handle(sock, dict(cmd='ReceivedItems', index=0, items=values))
            await c.handle(sock, dict(cmd='ReceivedItems', index=0, items=values))
        receipts = [r.item_parts[1] for r in caplog.records if hasattr(r, 'item_parts')]
        assert receipts == [(ARMOUR_TRAP, f) for f in (1, 2, 4, 0, None)]
        assert not c.notifications.queue
    asyncio.run(run())


def test_inventory_reconnect_does_not_replay_hud_notifications(tmp_path):
    async def run():
        c = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path)
        sock = Socket()
        await authenticate(c, sock)
        await c.handle(sock, dict(cmd='ReceivedItems', index=0, items=received(3)))
        assert not c.notifications.queue
        assert any(p['cmd'] == 'GetDataPackage' for p in sock.sent)
        await c.handle(sock, packet())
        assert len(c.notifications.queue) == 1
        c.notifications.queue.clear()
        await authenticate(c, sock)
        await c.handle(sock, dict(cmd='ReceivedItems', index=0, items=received(3)))
        await c.handle(sock, packet())
        assert not c.notifications.queue
    asyncio.run(run())


def test_safe_native_text_and_flag_precedence():
    assert safe_text('One\0two\n100%') == 'One two 100%'
    assert len(safe_text('a'*500, 160)) == 160
    assert item_flag(7) == 1 and item_flag(6) == 2


HUD = 0x12000000


def test_native_display_abi_single_delivery_and_pause_lease_guards():
    e = Engine()
    e.machine.mem_map(HUD, 8192)
    e.machine.mem_write(HUD+n.CODE_OFFSET, n.build_notifications(HUD))
    e.machine.mem_write(CONTROL+s.CODE_OFFSET, s.build_services(CONTROL, notification_target=HUD+n.CODE_OFFSET))
    e.machine.mem_write(BASE+n.DISPLAY_TEXT, b'\xc3')
    calls = []
    def observe(uc, address, size, data):
        if address == BASE+n.DISPLAY_TEXT:
            esp = uc.reg_read(UC_X86_REG_ESP)
            assert bytes(uc.mem_read(esp+4, 8)) == n.u32(HUD+n.TEXT)+n.u32(HUD+n.COLOUR)
            calls.append(address)
    e.machine.hook_add(UC_HOOK_CODE, observe)
    e.put(HUD+n.REQUEST, 1)
    e.set(s.HEARTBEAT, 1)
    e.frame()
    assert not calls
    e.set(s.HEARTBEAT, 9900)
    e.put(s.OBJECTIVE_ARRAY+BASE, 0)  # No objective scanning needed for this assertion.
    e.frame(); e.frame()
    assert len(calls) == 1 and bytes(e.machine.mem_read(HUD+n.REQUEST, 4)) == b'\0'*4
    e.put(HUD+n.REQUEST, 1)
    from test_services import OBJECT
    e.put(OBJECT+4, 1)
    e.frame()
    assert len(calls) == 1


class Memory:
    def __init__(self): self.data = bytearray(8192)
    def read(self, address, size):
        if address == BASE+n.DISPLAY_TEXT: return n.ANCHOR[:size]
        return bytes(self.data[address-HUD:address-HUD+size])
    def write(self, address, data): self.data[address-HUD:address-HUD+len(data)] = data
    def u32(self, address): return struct.unpack('<I', self.read(address, 4))[0]
    def write_u32(self, address, value): self.write(address, n.u32(value))
    def allocate(self): return HUD
    def make_executable(self, address): pass


def test_native_mailbox_waits_for_ack_and_throttles():
    mem = Memory(); now = [0]
    hud = n.NativeNotifications(mem, clock=lambda: now[0])
    c = SimpleNamespace(ready=True, notifications=feed())
    c.notifications.receive(packet())
    c.notifications.receive(packet(location=101))
    hud.update(c)
    assert mem.u32(HUD) == 1 and len(c.notifications.queue) == 1
    packed = struct.unpack('<4f', mem.read(HUD+n.COLOUR, 16))
    assert tuple(round(v*255) for v in packed) == (255, 0, 238, 238)
    before = mem.read(HUD+n.TEXT, 200)
    now[0] = 10
    hud.update(c)
    assert mem.read(HUD+n.TEXT, 200) == before and len(c.notifications.queue) == 1
    mem.write_u32(HUD, 0)
    hud.update(c)
    assert not c.notifications.queue


def test_native_mailbox_uses_neutral_colour_for_unclassified_grant():
    mem = Memory()
    hud = n.NativeNotifications(mem, clock=lambda: 0)
    c = SimpleNamespace(ready=True, notifications=feed())
    c.notifications.receive(packet(item=ITEM_IDS[ARMOUR_TRAP], location=-1, kind='ItemCheat'))
    hud.update(c)
    packed = struct.unpack('<4f', mem.read(HUD+n.COLOUR, 16))
    assert tuple(round(v*255) for v in packed) == (255, 174, 182, 194)


def test_chat_bindings_allowed_other_actions_blocked():
    assert not binding_conflicts('BeginPublicMessage_Primary=F2_Key\nBeginTeamMessage_Primary=F3_Key\nBeginPrivateMessage_Primary=F4_Key\n')
    assert binding_conflicts('Quicksave_Secondary=F2_Key\nHelpScreen_Primary=F4_Key\n') == {KEYS[0], KEYS[2]}


@pytest.mark.parametrize('guard', ['paused', 'background', 'modifier', 'disconnected'])
def test_hotkey_guards_and_held_key_latch(tmp_path, guard):
    path = tmp_path/'input01.cfg'; path.write_text('')
    state = [True, set(), False]
    hotkeys = SupplyHotkeys(1, path, sample=lambda: state)
    calls = []
    c = SimpleNamespace(ready=True, supply_indexes=lambda name: [0],
        request_supply=lambda name: calls.append(name) or True, notifications=feed())
    hotkeys.update(c, True)
    state[1] = {KEYS[0]}
    if guard == 'background': state[0] = False
    if guard == 'modifier': state[2] = True
    if guard == 'disconnected': c.ready = False
    hotkeys.update(c, guard != 'paused')
    state[0] = c.ready = True; state[2] = False
    hotkeys.update(c, True)
    assert not calls
    state[1] = set(); hotkeys.update(c, True)
    state[1] = {KEYS[0]}; hotkeys.update(c, True); hotkeys.update(c, True)
    assert calls == [HEALTH_SUPPLY]


def test_empty_supply_hotkey_does_not_consume_and_reports(tmp_path):
    path = tmp_path/'input01.cfg'; path.write_text('')
    state = [True, set(), False]
    c = SimpleNamespace(ready=True, supply_indexes=lambda name: [], notifications=feed())
    hotkeys = SupplyHotkeys(1, path, sample=lambda: state)
    hotkeys.update(c, True)
    state[1] = {KEYS[0]}; hotkeys.update(c, True)
    assert c.notifications.text(c.notifications.queue[0]) == 'No Health Supply available'

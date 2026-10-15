import asyncio
import struct
from types import SimpleNamespace

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_ESP

from renegade.data import ITEM_IDS, ITEM_FLAGS, WEAPON_ITEMS, slot_data, validate_slot_data
from renegade.native import weapons as w, services as s
from test_core import Socket, received
from test_mission_unlocks import connected
from test_services import Engine, BASE, CONTROL, OBJECT

WEAPON_CONTROL, HEAP = 0x16000000, 0x17000000
BAG, ARRAY, SOLDIER = HEAP, HEAP + 256, OBJECT + 768


class WeaponEngine(Engine):
    def __init__(self, mask=1):
        super().__init__()
        uc = self.machine
        uc.mem_map(WEAPON_CONTROL, 8192)
        uc.mem_map(HEAP, 65536)
        self.memory = SimpleNamespace(write=uc.mem_write)
        w.initialize(self.memory, WEAPON_CONTROL)
        uc.mem_write(WEAPON_CONTROL + w.CODE_OFFSET, w.build_weapons(WEAPON_CONTROL, CONTROL))
        uc.mem_write(CONTROL + s.CODE_OFFSET, s.build_services(CONTROL, weapon_target=WEAPON_CONTROL + w.CODE_OFFSET))
        self.put(WEAPON_CONTROL + w.ENABLED, 1)
        self.put(WEAPON_CONTROL + w.MASK, mask)
        self.put(SOLDIER + 0x7B0, BAG)
        self.put(BAG + 8, ARRAY)
        self.put(BAG + 20, 1)
        self.put(HEAP + 1024 + 0x24, BASE + 0x380500)
        uc.mem_write(BASE + 0x380500, b'\x8b\x41\x04\xc3')  # Get_ID.
        for rva, argc in ((w.ADD_WEAPON,12), (w.REMOVE_WEAPON,4), (w.SELECT_INDEX,4), (w.SELECT_NAME,4)):
            uc.mem_write(BASE + rva, b'\xc2' + struct.pack('<H', argc))
        self.next = HEAP + 2048
        self.grants, self.removals, self.selections = [], [], []
        self.mock_remove = self.mock_select = self.mock_add = True
        self.fail_grant = False
        def observe(machine, address, size, data):
            if address not in {BASE + x for x in (w.ADD_WEAPON,w.REMOVE_WEAPON,w.SELECT_INDEX,w.SELECT_NAME)}:
                return
            assert machine.reg_read(UC_X86_REG_ECX) == BAG
            esp = machine.reg_read(UC_X86_REG_ESP)
            arg = self.get(esp + 4)
            if address == BASE + w.ADD_WEAPON and self.mock_add:
                name = bytes(machine.mem_read(arg, w.NAME_SIZE)).split(b'\0')[0].decode()
                rounds = self.get(esp + 8)
                assert self.get(esp + 12) == 1
                descriptor = next(x for x in w.CATALOGUE if x.preset == name)
                assert rounds == descriptor.rounds & 0xFFFFFFFF
                self.grants.append((descriptor.name, rounds))
                ptr = 0 if self.fail_grant else self.add(descriptor.ids[0], ammo=rounds)
                machine.reg_write(UC_X86_REG_EAX, ptr)
            if address == BASE + w.REMOVE_WEAPON and self.mock_remove:
                assert 0 < arg < self.get(BAG + 20)
                assert self.get(BAG + 28) != arg, 'Destroying selected weapon without Deselect'
                self.removals.append(self.ident(self.entries()[arg]))
                entries = self.entries(); entries.pop(arg)
                self.vector(entries)
            if address == BASE + w.SELECT_INDEX and self.mock_select:
                self.selections.append(arg); self.put(BAG + 28, arg)
            if address == BASE + w.SELECT_NAME and self.mock_select:
                assert bytes(machine.mem_read(arg, 21)) == b'Weapon_Pistol_Player\0'
                for i, ptr in enumerate(self.entries()):
                    if ptr and self.ident(ptr) == 1393:
                        self.put(BAG + 28, i)
                        break
        uc.hook_add(UC_HOOK_CODE, observe)

    def get(self, a): return int.from_bytes(self.machine.mem_read(a,4),'little')
    def entries(self): return [self.get(ARRAY+i*4) for i in range(self.get(BAG+20))]
    def vector(self, entries):
        self.put(BAG+20,len(entries))
        self.machine.mem_write(ARRAY,b''.join(w.u32(x) for x in entries))
    def ident(self, ptr): return self.get(self.get(ptr)+4)
    def ids(self): return [self.ident(x) for x in self.entries() if x]
    def selected(self): return self.entries()[self.get(BAG+28)]
    def add(self, ident, owned=True, ammo=17):
        # A stable fake instance/definition; actual engine callbacks are verified
        # separately against the installed executable by verify_weapon_runtime.py.
        ptr = next((x for x in self.entries() if x and self.ident(x)==ident), None)
        if ptr is None:
            ptr = self.next; self.next += 192
            self.put(ptr, ptr+128); self.put(ptr+128, HEAP+1024); self.put(ptr+132, ident)
            self.vector(self.entries()+[ptr])
        self.machine.mem_write(ptr+0x38,bytes([owned]))
        self.put(ptr+0x50, ammo & 0xFFFFFFFF)
        return ptr


@pytest.mark.parametrize('enabled',[False,True])
def test_weapon_slot_contract_and_ids(enabled):
    cfg=slot_data(3,1,True,False,True,True,True,'individual',enabled)
    assert cfg['protocol_version']==7 and validate_slot_data(cfg)==cfg
    assert len(WEAPON_ITEMS)==22 and len(set(ITEM_IDS.values()))==len(ITEM_IDS)
    assert all('Pistol' not in name and ITEM_FLAGS[name]==2 for name in WEAPON_ITEMS)
    assert len({i for i,_ in w.DEFINITIONS})==len(w.DEFINITIONS)


@pytest.mark.parametrize('bad',[None,0,1,'true',[],{}])
def test_invalid_weapon_contract_rejected(bad):
    cfg=slot_data(3,1,True,False,True,True,True,'progressive',True)
    cfg['weapon_unlocks']=bad
    with pytest.raises(ValueError):validate_slot_data(cfg)


def test_old_rooms_keep_ordinary_weapon_ownership():
    cfg=slot_data(3,1,True,False,True,True,True,'progressive')
    assert validate_slot_data(cfg)==cfg and 'weapon_unlocks' not in cfg
    cfg['weapon_unlocks']=True
    with pytest.raises(ValueError):validate_slot_data(cfg)
    e=WeaponEngine();ptr=e.add(1131);e.put(WEAPON_CONTROL+w.ENABLED,0);e.frame()
    assert e.ids()==[1131] and e.get(ptr+0x50)==17 and not e.grants


def test_inventory_stays_authenticated_across_drop_resync_and_explicit_stop(tmp_path):
    async def run():
        cfg=slot_data(3,1,True,False,True,True,True,'progressive',True)
        c,sock=await connected(tmp_path,cfg)
        assert c.weapon_mask==0
        receipt=received(1)+[dict(item=ITEM_IDS[WEAPON_ITEMS[0]],location=123,player=1,flags=2)]
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=receipt))
        assert c.weapon_mask==3
        c.connected=False;assert c.weapon_mask==3
        c.connected=True
        await c.handle(sock,dict(cmd='ReceivedItems',index=99,items=[]))
        assert not c.ready and c.weapon_mask==3
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=receipt*2))
        assert c.weapon_mask==3  # Duplicates never refill/stack.
        c.stop.set();assert c.weapon_mask==0
        other,_=await connected(tmp_path,cfg)
        assert other.weapon_mask==0  # Disk cache is insufficient.
    asyncio.run(run())


def test_pistol_is_granted_and_selected_and_locked_save_loadout_removed():
    e=WeaponEngine();e.add(1131);e.add(2267);e.put(BAG+28,2);e.frame()
    assert e.ids()==[1393] and e.ident(e.selected())==1393
    assert e.selections==[0] and e.removals==[2267,1131]
    assert e.grants==[('Pistol',0xFFFFFFFF)]
    e.frame();assert len(e.grants)==1


@pytest.mark.parametrize('index,weapon',list(enumerate(w.CATALOGUE)))
def test_each_unlock_grants_only_its_family_with_correct_finite_ammo(index,weapon):
    e=WeaponEngine(1|(1<<index));e.frame()
    assert set(e.ids())=={1393,weapon.ids[0]}
    ptr=next(p for p in e.entries() if p and e.ident(p)==weapon.ids[0])
    e.put(ptr+0x50,0);e.frame();e.frame()
    assert e.get(ptr+0x50)==0  # Empty guns/C4/beacons stay empty.
    assert len(e.grants)==(1 if index==0 else 2)


def test_all_unlocks_load_and_reload_with_fresh_player_inventory():
    e=WeaponEngine(w.ALL_WEAPONS);e.frame()
    assert set(e.ids())=={x.ids[0] for x in w.CATALOGUE}
    assert len(e.grants)==23 and e.get(WEAPON_CONTROL+w.ERRORS)==0
    e.frame();assert len(e.grants)==23
    # Loading a checkpoint/mission replaces the bag; fresh inventory is reconciled.
    e.vector([0]);e.put(BAG+28,0);e.frame()
    assert len(e.grants)==46 and len(e.ids())==23


def test_pickups_are_filtered_preserving_ammo_variants_and_current_selection():
    e=WeaponEngine(3);pistol=e.add(1393);rifle=e.add(409610107,ammo=13)
    e.put(BAG+28,2);e.frame()
    assert e.ids()==[1393,409610107] and e.selected()==rifle and not e.grants
    # A pickup can insert before the selected rifle. Removing it must repair the
    # selected index without deselecting/reloading that authorized rifle.
    rocket=e.add(1131);e.vector([0,pistol,rocket,rifle]);e.put(BAG+28,3);e.frame()
    assert e.selected()==rifle and e.get(rifle+0x50)==13 and not e.selections
    assert e.machine.mem_read(BAG+32,2)==b'\1\1'
    assert e.removals==[1131]


def test_ammo_only_entry_does_not_count_as_owning_the_unlocked_gun():
    e=WeaponEngine(3);e.add(409610031,owned=False);e.frame()
    assert e.grants==[('Pistol',0xFFFFFFFF),('Automatic Rifle',500)]
    assert len(e.ids())==2


@pytest.mark.parametrize('ident,bit',w.DEFINITIONS)
def test_all_faction_and_multiplayer_aliases_share_their_unlock(ident,bit):
    e=WeaponEngine(bit|1);ptr=e.add(ident,ammo=3);e.frame()
    assert ident in e.ids() and e.get(ptr+0x50)==3
    assert not any(name==w.CATALOGUE[bit.bit_length()-1].name for name,_ in e.grants)
    if bit!=1:
        e.put(WEAPON_CONTROL+w.MASK,1);e.frame()
        assert e.ids()==[1393]


def test_unknown_handheld_preset_is_removed_but_other_actors_are_untouched():
    e=WeaponEngine();e.add(987654321)
    e.machine.mem_write(HEAP+0xE000,b'other actor inventory')
    e.frame()
    assert e.ids()==[1393] and e.removals==[987654321]
    assert e.machine.mem_read(HEAP+0xE000,21)==b'other actor inventory'


def test_all_service_features_fit_the_native_code_page():
    program=s.build_services(CONTROL,testing=True,reward_target=0x11001000,
                             notification_target=0x12001000,clock_target=0x13001000,
                             bonus_target=0x14001000,weapon_target=WEAPON_CONTROL+w.CODE_OFFSET)
    assert len(program)<=4096


@pytest.mark.parametrize('reason',['paused','dead','menu','locked','stale','victory','cinematic','no_camera','no_control','callback'])
def test_unsafe_states_do_not_change_inventory(reason):
    e=WeaponEngine();gun=e.add(1131)
    if reason=='paused':e.put(OBJECT+4,1)
    if reason=='dead':e.health(0)
    if reason=='menu':e.put(BASE+s.STEAM.game_mode,0)
    if reason=='locked':e.set(s.ALLOWED,0)
    if reason=='stale':e.set(s.HEARTBEAT,0)
    if reason=='victory':e.machine.mem_write(BASE+s.STEAM.pending_continue,b'\1')
    if reason=='cinematic':e.put(OBJECT+8192+s.CAMERA_HOST,OBJECT)
    if reason=='no_camera':e.put(BASE+s.COMBAT_CAMERA,0)
    if reason=='no_control':e.machine.mem_write(SOLDIER+s.CONTROL_ENABLED,b'\0')
    if reason=='callback':e.put(BASE+s.STEAM.commands+0x204,0)
    e.frame();assert e.ids()==[1131] and not e.grants and not e.removals
    assert e.get(gun+0x50)==17


def test_gameplay_enforcement_continues_offline_with_last_synced_permissions():
    e=WeaponEngine(3);e.set(s.ONLINE,0);e.add(1131);e.frame()
    assert set(e.ids())=={1393,409610031} and e.removals==[1131]


def test_bad_inventory_fails_without_calls_or_out_of_bounds_mutation():
    e=WeaponEngine();e.put(BAG+20,129);e.frame()
    assert e.get(WEAPON_CONTROL+w.ERRORS)==1 and not e.grants and not e.removals
    e.put(BAG+20,1);e.put(BAG+28,0xFFFFFFFF);e.frame()
    assert e.get(WEAPON_CONTROL+w.ERRORS)==2


def test_failed_grant_retries_and_does_not_mark_received_item_spent():
    e=WeaponEngine();e.fail_grant=True;e.frame()
    assert e.ids()==[] and e.get(WEAPON_CONTROL+w.ERRORS)==1
    e.fail_grant=False;e.frame()
    assert e.ids()==[1393]


def test_publication_disables_before_clearing_and_enables_after_mask():
    writes=[];n=w.NativeWeapons.__new__(w.NativeWeapons);n.control=0;n.last_errors=0
    n.memory=SimpleNamespace(write_u32=lambda a,v:writes.append((a,v)),u32=lambda a:0)
    n.update(SimpleNamespace(weapon_mask=3))
    assert writes==[(w.MASK,3),(w.ENABLED,1)]
    writes.clear();n.update(SimpleNamespace(weapon_mask=0))
    assert writes==[(w.ENABLED,0),(w.MASK,0),(w.ENABLED,0)]

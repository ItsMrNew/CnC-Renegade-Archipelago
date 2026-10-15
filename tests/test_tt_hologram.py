"""Execute TT bodyguard lifecycles and native ABI/save ownership boundaries."""
import struct
from types import SimpleNamespace
import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX,UC_X86_REG_EBX,UC_X86_REG_ECX,UC_X86_REG_EDI,UC_X86_REG_ESP,UC_X86_REG_XMM0
from renegade.native import tt_hologram as h,bonuses as b,traps as t,services as s,rewards as r,tt_inventory as inv
from renegade.native.tt_traps import guarded,FRAME_SECONDS
from renegade.native.thunk import CODE_OFFSET,u32
from renegade.native.profile import UnsupportedBuild
from test_tt_inventory import RewardEngine,REWARD,FRAME,WEAPON,HP
from test_tt_discs import PLAYER,CAMERA,MODE,DCTRL
from test_native import CONTROL,OBJECT,STACK,RETURN
from test_tt_native import memory

ALLY=0x24000000
PHYS,EFFECT,PASS,MATERIAL,MAT_DATA,BAG= [ALLY+i for i in (4096,8192,8448,8704,8960,9216)]


class HoloEngine(RewardEngine):
    def __init__(self,band=0x60000000):
        super().__init__(band)
        self.uc.mem_map(ALLY,0x10000)
        self.codes={}
        for i,name in enumerate(('update','damage','name','render','save','clock','movement')):
            code=0x17001000+i*0x10000;self.uc.mem_map(code-4096,8192);self.codes[name]=code
        for name in ('update','damage','name','render'):
            kwargs={} if name=='update' else {'testing':True}
            self.uc.mem_write(self.codes[name],getattr(h,'build_'+name)(self.codes[name],REWARD,CONTROL,self.a,**kwargs))
        self.uc.mem_write(self.codes['save'],b.build_save(self.codes['save'],REWARD,CONTROL,
            commands=self.a.commands,object_list=0x856FFC,save_objects=0x67FC50,counter=REWARD+h.SAVES))
        self.uc.mem_write(self.codes['clock'],t.build_clock(self.codes['clock'],REWARD,CONTROL,
            guard_builder=guarded(self.a),frame_seconds=FRAME_SECONDS))
        t.initialise(SimpleNamespace(write=self.uc.mem_write),REWARD)
        b.initialise(SimpleNamespace(write=self.uc.mem_write),REWARD,self.codes['render'],
                     effect_vtable=[band+x for x in h.EFFECT_VTABLE])
        self.put(REWARD+h.WIDE_NAME,REWARD+b.HOLO_NAME)
        self.fput(FRAME_SECONDS,.25)
        self.uc.mem_write(FRAME+CODE_OFFSET,inv.build_frame(FRAME+CODE_OFFSET,FRAME,CONTROL,self.a,
            WEAPON+CODE_OFFSET,REWARD+CODE_OFFSET,DCTRL+8192,clock_target=self.codes['clock'],
            hologram_target=self.codes['update']))
        self.uc.ctl_remove_cache(FRAME,FRAME+8192)
        self.put(ALLY+12,12345);self.put(ALLY+0x7B0,BAG)
        self.put(PLAYER+t.PLAYER_PHYSICS,PHYS+512);self.put(ALLY+t.PLAYER_PHYSICS,PHYS)
        self.put(EFFECT+0x54,PASS);self.put(PASS+0x18,MATERIAL);self.put(MATERIAL+8,MAT_DATA)
        self.put(EFFECT+8,ALLY+9500);self.put(ALLY+9500,ALLY+9600)
        self.uc.mem_write(ALLY+9600,b'\xc3')
        self.exists=False;self.calls=[];self.placements=[];self.positions=[];self.teleports=[]
        self.spawn_ok=self.weapon_ok=self.effect_ok=self.add_ok=True
        self.saved=[];self.name_arg=None;self.damage_args=None
        self.targets={v:k for k,v in h.bindings(self.a).items()}
        for value in self.targets:self.uc.mem_write(value,b'\xc3')
        for key,argc in ((b.FIND_TELEPORT,12),(b.ADD_WEAPON,12),(b.EFFECT_ADD,8),(b.SET_COLLISION_GROUP,4)):
            self.uc.mem_write(h.bindings(self.a)[key],b'\xc2'+struct.pack('<H',argc))
        self.uc.mem_write(0x6A59B0,h.expected_anchors(self.a)[0x6A59B0]+b'\xc2\x04\0')
        self.uc.mem_write(0x58A530,h.expected_anchors(self.a)[0x58A530])
        self.uc.mem_write(band+0x16DC20,b'\xc2\x08\0')
        self.uc.mem_write(band+0x5240,b'\xc2\x04\0')
        self.uc.mem_write(band+0x260C60,b'\xc2\x08\0')
        self.uc.mem_write(0x67FC50,b'\x31\xc0\xc3') # Failed saves restore the list too.
        def observe(uc,address,size,data):
            esp=uc.reg_read(UC_X86_REG_ESP);ecx=uc.reg_read(UC_X86_REG_ECX)
            arg=lambda i=0:self.get(esp+4+4*i)
            result=lambda v:uc.reg_write(UC_X86_REG_EAX,v)
            key=self.targets.get(address)
            if key==b.CREATE:
                assert arg()==REWARD+b.PRESET and arg(1)==REWARD+b.SPAWN_POSITION
                self.positions.append(struct.unpack('<3f',uc.mem_read(arg(1),12)))
                self.exists=self.spawn_ok;result(ALLY if self.exists else 0)
            elif key==b.FIND:
                assert arg()==12345;result(ALLY if self.exists else 0)
            elif key==b.DESTROY:assert arg()==ALLY
            elif key==b.DELETE_PENDING:self.exists=False
            elif key==b.GET_POSITION:
                assert arg(1)==PLAYER;uc.mem_write(arg(),struct.pack('<3f',10,20,30));result(arg())
            elif key==b.FIND_TELEPORT:
                assert ecx==PHYS+512 and arg(1)==0x3F000000
                self.placements.append(struct.unpack('<3f',uc.mem_read(arg(),12)))
                ok=self.teleports.pop(0) if self.teleports else True
                if ok:uc.mem_write(arg(2),bytes(uc.mem_read(arg(),12)))
                result(int(ok))
            elif key==b.ADD_WEAPON:
                assert ecx==BAG and arg()==REWARD+b.HOLO_WEAPON and arg(1)==0xFFFFFFFF and arg(2)==1
                result(WEAPON if self.weapon_ok else 0)
            elif key==b.SPAWN_EFFECT:self.put(EFFECT+12,1);result(EFFECT if self.effect_ok else 0)
            elif key==b.EFFECT_ADD:assert ecx==PHYS+0x54 and arg()==EFFECT;result(int(self.add_ok))
            elif key==b.ACTION_GOTO:
                assert arg()==ALLY and arg(1)==REWARD+b.ACTION
                assert self.get(REWARD+b.ACTION+44)==PLAYER
                assert self.get(REWARD+b.ACTION)==80
            elif key in (b.HOME,b.AGGRESSION,b.INNATE_ON,b.INNATE_OFF,b.ACTION_RESET,b.CLEAR_WEAPONS,b.SET_TEAM,b.SET_MODEL,b.SELECT_WEAPON):
                assert arg()==ALLY
            if key is not None:self.calls.append(key)
            if address==band+0x16DC20:self.damage_args=(ecx,arg(),arg(1),uc.reg_read(UC_X86_REG_XMM0+2))
            if address==band+0x5240:self.name_arg=arg()
            if address==0x67FC50:
                node=self.get(0x856FFC)
                while node:self.saved.append(self.get(node+4));node=self.get(node)
            # Deliberately clobber SIMD in helpers; the update wrapper protects
            # the combat caller. Damage/name forwarding must preserve inputs.
            if key not in (None,b.SET_COLLISION_GROUP):
                for i in range(8):uc.reg_write(UC_X86_REG_XMM0+i,0)
        self.uc.hook_add(UC_HOOK_CODE,observe)

    def start(self):self.request(13);self.frame()
    def invoke(self,name,this=ALLY,actor=ALLY,args=()):
        self.uc.reg_write(UC_X86_REG_ESP,STACK);self.put(STACK,RETURN)
        self.uc.reg_write(UC_X86_REG_ECX,this);self.uc.reg_write(UC_X86_REG_EBX,actor)
        for i,x in enumerate(args):self.put(STACK+4+i*4,x)
        vectors=[(0x1234567812345678<<64)+(i<<32)+0x3F800000 for i in range(8)]
        for i,v in enumerate(vectors):self.uc.reg_write(UC_X86_REG_XMM0+i,v)
        self.uc.emu_start(self.codes[name],RETURN,count=20000)
        assert self.uc.reg_read(UC_X86_REG_ESP)==STACK+4+{'damage':8,'name':4,'render':8}.get(name,0)
        if name in ('update','damage','name','cleanup','probe'):
            assert all(self.uc.reg_read(UC_X86_REG_XMM0+i)==v for i,v in enumerate(vectors))


@pytest.mark.parametrize('band',[0x60000000,0x71000000])
def test_tt_bodyguard_spawn_loadout_name_material_and_abi(band):
    e=HoloEngine(band);e.start()
    assert e.exists and e.get(REWARD+r.RESULT)==1
    assert e.positions==[(12.5,20,30)] and e.get(PHYS+0x38)==4|b.PHYS_DONT_SAVE
    assert b.ADD_WEAPON in e.calls and b.ACTION_GOTO in e.calls
    assert e.get(EFFECT)==REWARD+b.EFFECT_VTABLE
    assert [e.get(REWARD+b.EFFECT_VTABLE+i*4) for i in range(4)]==[
        band+h.EFFECT_VTABLE[0],band+h.EFFECT_VTABLE[1],e.codes['render'],band+h.EFFECT_VTABLE[3]]
    e.invoke('name',this=OBJECT,args=(OBJECT+100,));assert e.name_arg==REWARD+h.WIDE_NAME
    e.invoke('render',this=EFFECT,args=(OBJECT,PHYS))
    assert struct.unpack('<3f',e.uc.mem_read(MAT_DATA+0x30,12))==pytest.approx((.084,.56,.7))
    e.invoke('update') # Native helpers clobber SSE; wrapper restores all inputs.


@pytest.mark.parametrize('blocked_sides',[0,1,2,3,4])
def test_tt_bodyguard_searches_standing_room_and_refunds_when_surrounded(blocked_sides):
    e=HoloEngine();e.teleports=[False]*blocked_sides+[True];e.start()
    assert len(e.placements)==min(4,blocked_sides+1)
    assert e.exists==(blocked_sides<4)
    assert e.get(REWARD+r.RESULT)==(1 if blocked_sides<4 else 2)
    assert all(x!=(10,20,30) for x in e.placements)


@pytest.mark.parametrize('failure',['spawn_ok','weapon_ok','effect_ok','add_ok'])
def test_tt_partial_spawn_failure_refunds_and_destroys_actor(failure):
    e=HoloEngine();setattr(e,failure,False);e.start()
    assert not e.exists and e.get(REWARD+r.RESULT)==2 and not e.get(REWARD+b.HOLO_PTR)


def test_tt_hologram_repeat_extends_same_actor_and_pause_freezes_ai_and_timer():
    e=HoloEngine();e.start();first=e.fget(REWARD+t.HOLOGRAM_END)
    e.start();assert e.calls.count(b.CREATE)==1 and e.fget(REWARD+t.HOLOGRAM_END)==first+59.75
    e.put(MODE+4,3);before=e.fget(REWARD+t.HOLOGRAM_END)
    for _ in range(20):e.frame()
    assert e.fget(REWARD+t.HOLOGRAM_END)==before and e.calls.count(b.INNATE_OFF)==1
    e.put(MODE+4,0);e.frame();assert b.INNATE_ON in e.calls
    e.fput(REWARD+t.HOLOGRAM_END,.25);e.frame()
    assert not e.exists and not e.get(REWARD+b.HOLO_PTR)


@pytest.mark.parametrize('reason',['disabled','stale','dead','victory','cleanup'])
def test_tt_hologram_cleanup_runs_without_playable_timer(reason):
    e=HoloEngine();e.start()
    field,value={'disabled':(CONTROL+s.ENABLED,0),'stale':(CONTROL+s.HEARTBEAT,0),
        'dead':(HP,0),'victory':(e.a.pending_continue,1),'cleanup':(REWARD+b.CLEANUP,1)}[reason]
    e.put(field,value);e.frame()
    assert not e.exists and not e.get(REWARD+b.HOLO_PTR)


@pytest.mark.parametrize('reason',['generation','missing','reused_id'])
def test_tt_hologram_never_destroys_an_unrelated_object_after_load(reason):
    e=HoloEngine();e.start();e.calls.clear()
    if reason=='generation':e.put(CONTROL+s.GENERATION,e.read(s.GENERATION)+1)
    elif reason=='missing':e.exists=False
    else:e.put(ALLY+12,45678)
    e.invoke('update')
    assert b.DESTROY not in e.calls and not e.get(REWARD+b.HOLO_PTR)


@pytest.mark.parametrize('reason',['none','other_actor','reused_id','load','observer'])
def test_tt_invincibility_and_name_only_apply_to_identified_hologram(reason):
    e=HoloEngine();e.start();actor=ALLY
    if reason=='other_actor':actor=PLAYER
    if reason=='reused_id':e.put(ALLY+12,999)
    if reason=='load':e.put(CONTROL+s.GENERATION,999)
    if reason=='observer':e.put(e.a.commands+0x204,0)
    e.invoke('damage',this=actor+0x72C,actor=actor,args=(123,456))
    assert (e.damage_args is None)==(reason=='none')
    e.invoke('name',this=OBJECT,actor=actor,args=(OBJECT+100,))
    assert e.name_arg==(REWARD+h.WIDE_NAME if reason=='none' else OBJECT+100)


@pytest.mark.parametrize('index',[0,1,2])
def test_tt_save_excludes_only_hologram_and_restores_exact_links_on_failed_save(index):
    e=HoloEngine();e.start();nodes=[ALLY+10000+i*16 for i in range(3)]
    objects=[PLAYER,OBJECT,ALLY];objects[index],objects[2]=objects[2],objects[index]
    e.put(0x856FFC,nodes[0]);e.put(0x857000,nodes[-1])
    for i,node in enumerate(nodes):e.put(node,nodes[i+1] if i<2 else 0);e.put(node+4,objects[i])
    before=bytes(e.uc.mem_read(nodes[0],48));head=e.get(0x856FFC)
    e.invoke('save',args=(OBJECT,))
    assert sorted(e.saved)==sorted([PLAYER,OBJECT]) and e.get(REWARD+h.SAVES)==1
    assert e.get(0x856FFC)==head and e.get(0x857000)==nodes[-1]
    assert bytes(e.uc.mem_read(nodes[0],48))==before and e.exists


def test_tt_physics_save_skips_hologram_but_preserves_player_and_other_flags():
    e=HoloEngine();e.put(PHYS+0x38,0x109003);e.put(PHYS+512+0x38,0x109003)
    e.start()
    assert e.get(REWARD+r.RESULT)==1 and e.get(PHYS+0x38)==0x10D004
    assert e.get(PHYS+512+0x38)==0x109003
    # Execute the fingerprinted native PhysicsScene save predicate. GameObjManager
    # exclusion alone is insufficient: physics is serialized in another traversal.
    entry=e.a.band_base+0xA7084
    e.uc.mem_write(entry,h.expected_anchors(e.a)[entry])
    e.uc.mem_write(entry+10,b'\xb8\x01\0\0\0\xc3') # Would serialize this body.
    e.uc.mem_write(e.a.band_base+0xA70D2,b'\x31\xc0\xc3') # Skip DONT_SAVE body.
    for physics,expected in ((PHYS,0),(PHYS+512,1)):
        e.uc.reg_write(UC_X86_REG_EDI,physics)
        e.uc.reg_write(UC_X86_REG_ESP,STACK);e.put(STACK,RETURN)
        e.uc.emu_start(entry,RETURN,count=100)
        assert e.uc.reg_read(UC_X86_REG_EAX)==expected


def test_tt_hologram_calls_restore_and_foreign_ownership_is_preserved(memory):
    obj=h.NativeHologram(memory,REWARD,CONTROL,memory.a)
    obj.update();obj.update()
    site,old,new=obj.entries[0];memory.write(site,b'\xe8'+u32(123))
    with pytest.raises(OSError):obj.update()
    obj.close()
    assert memory.read(site,5)==b'\xe8'+u32(123)
    assert all(memory.read(a,5)==old for a,old,new in obj.entries[1:])


def test_tt_hologram_rejects_changed_binding_before_allocation(memory):
    memory.write(memory.a.band_base+0x260C60,b'\xcc');before=memory.next_allocation
    with pytest.raises(UnsupportedBuild):h.NativeHologram(memory,REWARD,CONTROL,memory.a)
    assert memory.next_allocation==before


@pytest.mark.parametrize('reason',['paused','menu','multiplayer','cinematic','dead','control',
    'locked','stale','victory','offline','observer'])
def test_tt_hologram_activation_waits_for_playable_state(reason):
    e=HoloEngine();e.request(13)
    fields={'paused':(MODE+4,3),'menu':(e.a.game_mode,0),'multiplayer':(e.a.game_mode,2),
        'cinematic':(CAMERA+s.CAMERA_HOST,OBJECT),'dead':(HP,0),'control':(PLAYER+s.CONTROL_ENABLED,0),
        'locked':(CONTROL+s.ALLOWED,0),'stale':(CONTROL+s.HEARTBEAT,0),
        'victory':(e.a.pending_continue,1),'offline':(CONTROL+s.ONLINE,0),
        'observer':(e.a.commands+0x204,0)}
    address,value=fields[reason];before=e.get(address);e.put(address,value);e.frame()
    assert not e.exists and not e.get(REWARD+r.RESULT) and e.get(REWARD+r.REQUEST)==13
    e.put(address,before);e.frame()
    if e.get(REWARD+r.RESULT)==2:  # Map/control generation changed; refund then requeue.
        assert not e.exists
        e.start()
    assert e.exists and e.get(REWARD+r.RESULT)==1


@pytest.mark.parametrize('action',[1,2])
def test_tt_hologram_test_probe_runs_only_with_live_owned_ally(action):
    from renegade.native.tt_campaign import SAVE,SAVE_SYSTEM
    e=HoloEngine();e.start();code=0x18001000;e.uc.mem_map(code-4096,8192)
    e.uc.mem_write(code,h.build_test_probe(code,REWARD,CONTROL,e.a,e.codes['update']))
    e.codes['probe']=code;e.uc.mem_write(e.a.band_base+SAVE,b'\xc3')
    e.put(e.a.band_base+SAVE_SYSTEM,0x81C648)
    e.uc.mem_write(inv.APPLY_DAMAGE,b'\xc3');seen=[]
    def observe(uc,address,size,data):
        if address not in (e.a.band_base+SAVE,inv.APPLY_DAMAGE):return
        esp=uc.reg_read(UC_X86_REG_ESP)
        seen.append((address,[e.get(esp+i*4) for i in range(1,4 if action==1 else 5)]))
    e.uc.hook_add(UC_HOOK_CODE,observe)
    e.put(REWARD+h.TEST_ACTION,action);e.put(MODE+4,3);e.invoke('probe')
    assert not seen and e.get(REWARD+h.TEST_ACTION)==action
    e.put(MODE+4,0);e.invoke('probe');e.invoke('probe')
    expected=[REWARD+h.TEST_FILENAME,0x81C648,0] if action==1 else [ALLY,0x47C35000,CONTROL+s.WARHEAD,0]
    assert seen==[(e.a.band_base+SAVE if action==1 else inv.APPLY_DAMAGE,expected)]
    assert e.get(REWARD+h.TEST_RESULT)==1 and e.get(REWARD+h.TEST_ACTION)==0


@pytest.mark.parametrize('foreign',[False,True])
def test_tt_deferred_disconnect_owns_separate_handle_and_only_restores_own_callbacks(memory,monkeypatch,foreign):
    from renegade.native import memory as mm
    from renegade.native.tt_campaign import MENU_SLOT,MENU_THINK
    obj=h.NativeHologram(memory,REWARD,CONTROL,memory.a);obj.update()
    menu=memory.a.band_base+MENU_SLOT;memory.write_u32(menu,memory.a.band_base+MENU_THINK)
    memory.write_u32(REWARD+b.HOLO_PTR,ALLY)
    times=iter([0,1]);monkeypatch.setattr(h.time,'monotonic',lambda:next(times))
    obj.close();assert obj.pending_cleanup
    assert all(memory.read(site,5)==new for site,old,new in obj.entries)
    closed=[];memory.close=lambda:closed.append(True)
    monkeypatch.setattr(mm,'ProcessMemory',lambda pid,writable:memory)
    workers=[]
    def thread(*,target,**kwargs):
        workers.append(target);return SimpleNamespace(start=lambda:None)
    monkeypatch.setattr(h.threading,'Thread',thread)
    obj.defer_cleanup();obj.defer_cleanup();assert len(workers)==1 and not closed
    assert memory.u32(menu)!=memory.a.band_base+MENU_THINK
    if foreign:memory.write_u32(menu,999)
    memory.write_u32(REWARD+b.HOLO_PTR,0);workers[0]()
    assert closed and not obj.pending_cleanup
    assert all(memory.read(site,5)==old for site,old,new in obj.entries)
    assert memory.u32(menu)==(999 if foreign else memory.a.band_base+MENU_THINK)
    assert memory.u32(memory.a.combat_think_slot)==memory.a.combat_think


def test_tt_deferred_disconnect_rejects_foreign_frame_without_partial_install(memory,monkeypatch):
    from renegade.native import memory as mm
    from renegade.native.tt_campaign import MENU_SLOT
    obj=h.NativeHologram(memory,REWARD,CONTROL,memory.a);obj.pending_cleanup=True
    memory.write_u32(memory.a.band_base+MENU_SLOT,999)
    closed=[];memory.close=lambda:closed.append(True)
    monkeypatch.setattr(mm,'ProcessMemory',lambda pid,writable:memory)
    with pytest.raises(OSError,match='Another callback'):obj.defer_cleanup()
    assert closed and memory.u32(memory.a.combat_think_slot)==memory.a.combat_think


def test_tt_cleanup_frame_forwards_native_think_after_native_deletion():
    e=HoloEngine();e.start();code=0x18001000;e.uc.mem_map(code-4096,8192)
    e.uc.mem_write(code,h.build_cleanup_frame(code,e.codes['update'],e.a.combat_think))
    e.put(REWARD+b.CLEANUP,1);e.codes['cleanup']=code
    e.destinations.pop(e.a.combat_think)
    e.uc.mem_write(e.a.combat_think,b'\xff\x05'+u32(REWARD+h.TEST_RESULT)+b'\xc3');e.invoke('cleanup')
    assert e.get(REWARD+h.TEST_RESULT)==1
    assert not e.exists and not e.get(REWARD+b.HOLO_PTR)

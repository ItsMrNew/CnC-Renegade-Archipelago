"""TT temporary Volt-rifle bodyguard; instance-only name, protection and material.

GameObjManager saves use TT's list head. Preserve the native traversal and put
the exact link back immediately; the ephemeral actor never enters a checkpoint.
"""
import struct
import logging
import threading
import time
from . import bonuses as h, services as s
from .tt_weapon_effects import save,restore
from .tt_traps import guarded, FRAME_SECONDS
from .thunk import Code,CODE_OFFSET,u32
from .profile import UnsupportedBuild

SAVES, PROTECTED, NAMED, RENDERS, WIDE_NAME = range(2068,2088,4)
TEST_ACTION, TEST_RESULT, TEST_HP_BEFORE, TEST_HP_AFTER = range(2088,2104,4)
TEST_FILENAME = 2128
EFFECT_VTABLE = (0x203810,0x260B20,0x260C60,0x260DE0)


def bindings(a):
    b=a.band_base
    return {h.CREATE:0x7283F0,h.DESTROY:0x728330,h.FIND:0x728350,h.GET_POSITION:0x728090,
        h.SET_TEAM:0x729300,h.SET_MODEL:b+0x231330,h.INNATE_ON:0x72A3E0,h.INNATE_OFF:0x72A3B0,
        h.ACTION_GOTO:b+0xC7AD0,h.ACTION_RESET:b+0xC7A30,h.HOME:0x72A410,h.AGGRESSION:0x72A460,
        h.DELETE_PENDING:0x617E10,h.FIND_TELEPORT:0x65B910,h.SET_COLLISION_GROUP:0x6A59B0,
        h.CLEAR_WEAPONS:b+0x22F780,h.ADD_WEAPON:0x6F3C00,h.SELECT_WEAPON:b+0x231280,
        h.SPAWN_EFFECT:b+0x14FD80,h.EFFECT_ADD:0x5EE320}


def calls(a):
    return ((0x7270C2,0x67FC50,'save'),(a.band_base+0x16877B,a.band_base+0x16DC20,'damage'),
            (a.band_base+0x6CEA5,a.band_base+0x5240,'name'))


def identity(c,control,service,a,reject):
    # Native caller supplies EAX. Never dereference an old cached actor pointer.
    c.emit(b'\x85\xc0');c.jump(b'\x0f\x84',reject)
    c.emit(b'\x3b\x05'+u32(control+h.HOLO_PTR));c.jump(b'\x0f\x85',reject)
    c.emit(b'\x8b\x50\x0c\x3b\x15'+u32(control+h.HOLO_ID));c.jump(b'\x0f\x85',reject)
    c.emit(b'\x8b\x15'+u32(service+s.GENERATION)+b'\x3b\x15'+u32(control+h.HOLO_GENERATION));c.jump(b'\x0f\x85',reject)
    c.emit(b'\x8b\x15'+u32(service+s.OBSERVER)+b'\x85\xd2');c.jump(b'\x0f\x84',reject)
    c.emit(b'\x3b\x15'+u32(a.commands+0x204));c.jump(b'\x0f\x85',reject)


def build_damage(address,control,service,a,*,testing=False):
    c=Code(address);save(c)
    c.emit(b'\x8d\x81'+u32(-0x72c));identity(c,control,service,a,'normal')
    if testing:c.emit(b'\xff\x05'+u32(control+PROTECTED))
    restore(c);c.emit(b'\xc2\x08\x00') # TT scale is XMM2, two stack arguments.
    c.label('normal');restore(c);c.jump(b'\xe9',a.band_base+0x16DC20)
    return c.finish()


def build_name(address,control,service,a,*,testing=False):
    c=Code(address);save(c)
    c.emit(b'\x8b\x44\x24\x10') # HUD target is original EBX.
    identity(c,control,service,a,'normal')
    # TT copies WideStringClass&, not wchar_t*. The original temporary remains
    # owned by its caller; only the copy source changes for this one HUD target.
    c.emit(b'\xc7\x84\x24'+u32(168)+u32(control+WIDE_NAME))
    if testing:c.emit(b'\xff\x05'+u32(control+NAMED))
    c.label('normal');restore(c);c.jump(b'\xe9',a.band_base+0x5240)
    return c.finish()


def build_render(address,control,service,a,*,testing=False):
    c=Code(address);c.emit(b'\x56\x89\xce\xff\x74\x24\x0c\xff\x74\x24\x0c')
    c.jump(b'\xe8',a.band_base+0x260C60)
    # TT's material pass stores VertexMaterial at +0x18 (Steam uses +0x14).
    # Native Render_Push queues this private material; change its emissive
    # colour before the queued draw, retaining the native matched Render_Pop.
    for offset in (8,4,0):
        c.emit(b'\x83\xec\x04\xd9\x46\x44\xd8\x0d'+u32(control+h.HOLO_COLOUR+offset)+b'\xd9\x1c\x24')
    c.emit(b'\x8b\x4e\x54\x8b\x49\x18');c.jump(b'\xe8',0x58A530)
    if testing:c.emit(b'\xff\x05'+u32(control+RENDERS))
    c.emit(b'\x5e\xc2\x08\x00');return c.finish()


def build_update(address,control,service,a):
    return h.build_update(address,control,service,bindings=bindings(a),guard_builder=guarded(a),
        tick_iat=a.tick_iat,pending_continue=a.pending_continue,frame_seconds=FRAME_SECONDS,
        registers=(save,restore))


def build_cleanup_frame(address,update,original):
    """A retired session only removes its ally, then runs the ordinary frame."""
    c=Code(address);save(c);c.jump(b'\xe8',update);restore(c);c.jump(b'\xe9',original)
    return c.finish()


def build_test_probe(address,control,service,a,update):
    """Disposable test only: save a separate file or damage the identified ally."""
    from .tt_campaign import SAVE,SAVE_SYSTEM
    from .tt_inventory import APPLY_DAMAGE,GET_HEALTH
    c=Code(address);save(c);c.jump(b'\xe8',update)
    guarded(a)(c,control,service,'done')
    c.emit(b'\xa1'+u32(control+h.HOLO_PTR));identity(c,control,service,a,'done')
    c.emit(b'\x89\xc3\xa1'+u32(control+TEST_ACTION)+b'\x83\xf8\x01');c.jump(b'\x0f\x85','damage')
    c.emit(b'\xc7\x05'+u32(control+TEST_ACTION)+u32(0))
    c.emit(b'\xfc\x6a\x00\xff\x35'+u32(a.band_base+SAVE_SYSTEM)+b'\x68'+u32(control+TEST_FILENAME))
    c.jump(b'\xe8',a.band_base+SAVE);c.emit(b'\x83\xc4\x0c');c.jump(b'\xe9','complete')
    c.label('damage');c.emit(b'\x83\xf8\x02');c.jump(b'\x0f\x85','done')
    c.emit(b'\xc7\x05'+u32(control+TEST_ACTION)+u32(0)+b'\x53')
    c.jump(b'\xe8',GET_HEALTH);c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(control+TEST_HP_BEFORE))
    c.emit(b'\x6a\x00\x68'+u32(service+s.WARHEAD)+b'\x68'+struct.pack('<f',100000)+b'\x53')
    c.jump(b'\xe8',APPLY_DAMAGE);c.emit(b'\x83\xc4\x10\x53')
    c.jump(b'\xe8',GET_HEALTH);c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(control+TEST_HP_AFTER))
    c.label('complete');c.emit(b'\xff\x05'+u32(control+TEST_RESULT))
    c.label('done');restore(c);c.emit(b'\xc3');return c.finish()


class NativeHologram(h.NativeBonuses):
    def __init__(self,memory,control,service,a,*,testing=False):
        from .tt_inventory import install_code
        self.memory,self.control,self.service=memory,control,service
        self.entries=[]
        self.addresses=a
        self.pending_cleanup=False
        self.cleanup_thread=None
        for address,expected in expected_anchors(a).items():
            if memory.read(address,len(expected))!=expected:
                raise UnsupportedBuild(f'TT hologram anchor differs at {address:#x}')
        for site,target,name in calls(a):
            if memory.read(site,5)!=h.call_bytes(site,target):
                raise UnsupportedBuild(f'Another hook owns the TT hologram {name} call')
        render=memory.allocate()+CODE_OFFSET
        install_code(memory,render,build_render(render,control,service,a,testing=testing))
        h.initialise(memory,control,render,effect_vtable=[a.band_base+rva for rva in EFFECT_VTABLE])
        memory.write(control+h.HOLO_NAME-8,struct.pack('<II',15,14))
        memory.write_u32(control+WIDE_NAME,control+h.HOLO_NAME)
        for site,target,name in calls(a):
            code=memory.allocate()+CODE_OFFSET
            if name=='save':program=h.build_save(code,control,service,commands=a.commands,
                object_list=0x856FFC,save_objects=target,counter=control+SAVES if testing else None)
            else:program=({'damage':build_damage,'name':build_name}[name])(code,control,service,a,testing=testing)
            install_code(memory,code,program)
            self.entries.append((site,h.call_bytes(site,target),h.call_bytes(site,code)))
        self.update_code=memory.allocate()+CODE_OFFSET
        install_code(memory,self.update_code,build_update(self.update_code,control,service,a))
        if testing:
            from .tt_campaign import SAVE,SAVE_SYSTEM,expected_anchors as campaign_anchors
            anchor=campaign_anchors(a)[a.band_base+SAVE]
            if memory.read(a.band_base+SAVE,len(anchor))!=anchor or memory.u32(a.band_base+SAVE_SYSTEM)!=0x81C648:
                raise UnsupportedBuild('TT hologram test save binding differs')
            probe=memory.allocate()+CODE_OFFSET
            install_code(memory,probe,build_test_probe(probe,control,service,a,self.update_code))
            self.update_code=probe

    def restore_calls(self,memory):
        changes=[]
        for site,old,new in self.entries:
            if memory.read(site,5)==new:changes.append((site,new,old))
        if changes:memory.replace_calls(changes)

    def close(self):
        self.memory.write_u32(self.control+h.CLEANUP,1)
        until=time.monotonic()+.25
        while self.memory.u32(self.control+h.HOLO_PTR) and time.monotonic()<until:time.sleep(.01)
        self.pending_cleanup=bool(self.memory.u32(self.control+h.HOLO_PTR))
        if not self.pending_cleanup:self.restore_calls(self.memory)

    def defer_cleanup(self):
        """Keep only cleanup frames until an unfocused/paused game resumes.

        A separate owned handle outlives the disconnected client's handle. No
        rewards, AP checks, leases or timers are driven by these callbacks.
        Native deletion stays on the game thread; code restoration uses the
        existing suspended-process patcher. Never leave an immortal ally behind
        because closing the launcher stopped its combat service first.
        """
        if not self.pending_cleanup or self.cleanup_thread:return
        from .memory import ProcessMemory
        from .tt_inventory import install_code
        from .tt_campaign import MENU_SLOT,MENU_THINK
        mem=ProcessMemory(self.memory.pid,writable=True)
        a=self.addresses;entries=[]
        try:
            for field,original in ((a.combat_think_slot,a.combat_think),
                                   (a.band_base+MENU_SLOT,a.band_base+MENU_THINK)):
                if mem.u32(field)!=original:
                    raise OSError('Another callback owns the TT cleanup frame; resume TT before closing')
                code=mem.allocate()+CODE_OFFSET
                install_code(mem,code,build_cleanup_frame(code,self.update_code,original))
                entries.append((field,original,code))
            for field,original,code in entries:mem.replace_readonly_u32(field,original,code)
        except BaseException:
            for field,original,code in entries:
                if mem.u32(field)==code:mem.replace_readonly_u32(field,code,original)
            mem.close();raise
        def finish():
            try:
                while mem.u32(self.control+h.HOLO_PTR):time.sleep(.05)
                self.restore_calls(mem)
                for field,original,code in entries:
                    if mem.u32(field)==code:mem.replace_readonly_u32(field,code,original)
                self.pending_cleanup=False
                logging.getLogger('Renegade.TT').info('Hologram cleanup finished; TT callbacks restored')
            except OSError as error:
                logging.getLogger('Renegade.TT').warning('TT hologram cleanup ended: %s',error)
            finally:mem.close()
        self.cleanup_thread=threading.Thread(target=finish,name='Renegade TT hologram cleanup',daemon=False)
        self.cleanup_thread.start()
        logging.getLogger('Renegade.TT').info('Hologram cleanup queued; return to TT to finish detaching')


def expected_anchors(a):
    b=a.band_base
    return {
        0x7283F0:bytes.fromhex('8b4c240483ec3085c9750633c083c430c38b4424385651c7'),
        0x728330:bytes.fromhex('8b44240485c074098d48088b4008ff602cc3909090909090'),
        0x728350:bytes.fromhex('8b44240450e8e67ef5ff83c404c390908b44240485c07501'),
        0x728090:bytes.fromhex('8b4c240833d283ec0c3bca75108b44241089108950048950'),
        0x729300:bytes.fromhex('8b4c240485c974158b01ff502485c0740c8b4c24088b1051'),
        b+0x231330:bytes.fromhex('5156578b7c241085ff0f84e10000008b078bcfff50288bf0'),
        0x72A3E0:bytes.fromhex('8b4c240485c9741e8b01ff502485c074158b108bc8ff5244'),
        0x72A3B0:bytes.fromhex('8b4c240485c9741e8b01ff502485c074158b108bc8ff5244'),
        b+0xC7AD0:bytes.fromhex('8b4c240485c9744a8b0156ff502c8bf085f6743d68680100'),
        b+0xC7A30:bytes.fromhex('8b4c240485c9742c8b01ff502c85c07423660f6e88580800'),
        0x72A410:bytes.fromhex('8b4c240485c9743b8b01ff502c85c074328b108bc8ff5244'),
        0x72A460:bytes.fromhex('8b4c240485c974268b01ff502c85c0741d8b108bc8ff5244'),
        0x617E10:bytes.fromhex('a07846850084c0753ea1684685005633f685c07e27a15c46'),
        0x65B910:bytes.fromhex('558bec81ecbc00000056578b7d088bf18d4db08d55b0d907'),
        0x6A59B0:bytes.fromhex('8b81740700008a4c240483e10f8b503883e2f00bca894838'),
        b+0x22F780:bytes.fromhex('51568b74240c85f6741d8b068bceff502c85c074128b068b'),
        0x6F3C00:bytes.fromhex('8b44240c8b542404568bf18b4c240c505152e8c9f6030083'),
        b+0x231280:bytes.fromhex('51568b74240c5785f60f84930000008b068bceff50248bf8'),
        b+0x14FD80:bytes.fromhex('5356576a58ff15')+u32(b+0x2C947C)+bytes.fromhex('83c40485c0740b8bc8e8070b11'),
        0x5EE320:b'\xe9'+u32(a.shared_base+0x3760-0x5EE325),
        b+0x16876A:bytes.fromhex('ff7424248d8b2c07000055f30f1144241c'),
        b+0x16DC20:bytes.fromhex('51a1')+u32(b+0x3207A8)+bytes.fromhex('5680380057f30f115424088bf1744e8b7c24'),
        b+0x6CE5B:bytes.fromhex('8b5c24708b83bc0600008b4870'),
        b+0x6CE9A:bytes.fromhex('8b0151ff501c508d4c2438'),
        b+0x5240:bytes.fromhex('538b5c2408568b35')+u32(b+0x2C96C0)+bytes.fromhex('578b068bf98b0089078b068b'),
        b+0x260C60:bytes.fromhex('558bec83e4f883ec40568b7508578bf9807f42000f843d0100008b4754f30f1047448b4818'),
        b+0x260C7A:bytes.fromhex('8b4754f30f1047448b4818f30f1015'),
        0x58A530:bytes.fromhex('8b41088b542404c6413c018950308b41088b5424088950348b41088b4c240c894838c20c00'),
        b+0x1B1330:bytes.fromhex('558bec83e4f883ec0c5356578b7d0868a52ea8368bcfe845db0e00a1')+u32(b+0x3207D4)+bytes.fromhex('8b580485db7436'),
        0x7270B0:bytes.fromhex('568b74240868a62ea8368bcee85f6debff56'),
        b+0x3207D4:bytes.fromhex('f86f8500'),
        # PhysicsScene's separate dynamic-object save honors PhysClass::DONT_SAVE.
        b+0xA7084:bytes.fromhex('8b4738c1e80ea8017544'),
        b+0x2F02A4:u32(b+0x203810)+u32(b+0x260B20)+u32(b+0x260C60)+u32(b+0x260DE0),
        0x67FC50:b'\xe9'+u32(b+0x1B1330-0x67FC55),
    }

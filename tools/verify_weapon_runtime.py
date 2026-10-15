"""Verify stock definitions and execute installed weapon APIs in an emulator.

Read-only: does not attach to or alter the live game. Requires a previously
extracted stock objects.ddb (default .research/objects.ddb).
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import pefile
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_ESP
from renegade.native.profile import require_digest, STEAM
from renegade.native.savefiles import chunks, fields
from renegade.native import weapons as w
from test_weapons import WeaponEngine, BASE, BAG, HEAP, WEAPON_CONTROL


def definitions(raw):
    result={}
    def walk(data):
        for kind,size,payload in chunks(data):
            if kind==0x100101 and size&0x80000000:
                parts={k:p for k,_,p in chunks(payload)}
                if 0x47D43D47 in parts:
                    meta=dict(fields(list(chunks(parts[0x47D43D47]))[0][2]))
                    values=dict(fields(parts[0x47D43D46]))
                    result[int.from_bytes(meta[1],'little')]=(
                        meta[3].rstrip(b'\0').decode(),
                        int.from_bytes(values[0x1D],'little',signed=True),
                        int.from_bytes(values[0x23],'little',signed=True))
            if size&0x80000000:walk(payload)
    walk(raw)
    return result


def verify(game, objects):
    require_digest(game,STEAM.game_sha256)
    pe=pefile.PE(str(game))
    for rva,expected in w.ANCHORS.items():
        assert pe.get_data(rva,len(expected))==expected,hex(rva)
    presets=definitions(objects.read_bytes())
    for descriptor in w.CATALOGUE:
        name,clip,reserve=presets[descriptor.ids[0]]
        assert name==descriptor.preset
        assert descriptor.rounds==(-1 if reserve==-1 else clip+reserve)
        assert all(ident in presets for ident in descriptor.ids)
        for ident in descriptor.ids:
            alias=presets[ident][0].lower().removeprefix('cnc_')
            for suffix in ('_2max','_special','_nod'):
                alias=alias.removesuffix(suffix)
            assert alias==descriptor.preset.lower(),(descriptor.name,presets[ident][0])
    e=WeaponEngine(w.ALL_WEAPONS);uc=e.machine
    e.mock_remove=e.mock_select=e.mock_add=False
    for rva,size in ((w.ADD_WEAPON,0x26),(0x1B4000,0x15),
                     (w.REMOVE_WEAPON,0x57),(w.SELECT_INDEX,0x59),(w.SELECT_NAME,0x120)):
        uc.mem_write(BASE+rva,pe.get_data(rva,size))
    # Mock only allocation/definition-manager/weapon-animation dependencies;
    # real bag wrappers, deletion/compaction and selection code execute below.
    for rva,code in ((0x22C670,b'\xc3'),(0x179560,b'\xc2\x0c\0'),
                     (0x17E100,b'\xc3'),(0xF1320,b'\xc3'),
                     (0x1808C0,b'\xc3'),(0x1808A0,b'\xc3')):
        uc.mem_write(BASE+rva,code)
    e.put(HEAP+1024+0x30,BASE+0x380520)
    uc.mem_write(BASE+0x380520,b'\x8b\x41\x08\xc3')
    calls=[]
    def observe(machine,address,size,data):
        esp=machine.reg_read(UC_X86_REG_ESP)
        if address==BASE+0x22C670:
            name,kind,twiddle=struct.unpack('<3I',machine.mem_read(esp+4,12))
            assert kind==0xB001 and twiddle==1
            descriptor=next(d for i,d in enumerate(w.CATALOGUE) if name==WEAPON_CONTROL+w.NAMES+i*w.NAME_SIZE)
            e.put(HEAP+0xF000,descriptor.ids[0])
            machine.reg_write(UC_X86_REG_EAX,HEAP+0xF000)
        elif address==BASE+0x179560:
            assert machine.reg_read(UC_X86_REG_ECX)==BAG
            definition,rounds,give=struct.unpack('<3I',machine.mem_read(esp+4,12))
            descriptor=next(d for d in w.CATALOGUE if d.ids[0]==e.get(definition))
            assert give==1 and rounds==descriptor.rounds&0xFFFFFFFF
            ptr=e.add(descriptor.ids[0],ammo=rounds)
            i=w.CATALOGUE.index(descriptor)
            e.put(e.get(ptr)+8,WEAPON_CONTROL+w.NAMES+i*w.NAME_SIZE)
            machine.reg_write(UC_X86_REG_EAX,ptr)
            calls.append(descriptor.name)
    uc.hook_add(UC_HOOK_CODE,observe)
    e.frame()
    assert len(e.ids())==23 and e.ident(e.selected())==1393
    e.frame();assert len(calls)==23
    e.put(BAG+28,22)  # Selected ion beacon, about to be removed.
    e.put(WEAPON_CONTROL+w.MASK,1);e.frame()
    assert e.ids()==[1393] and e.ident(e.selected())==1393
    assert len(calls)==23 and e.get(WEAPON_CONTROL+w.REMOVED)==22
    assert e.get(WEAPON_CONTROL+w.ERRORS)==0
    report=dict(stage='passed',live_game_test=False,weapon_families=23,unlock_items=22,
                aliases_verified=len(w.DEFINITIONS),anchors_verified=len(w.ANCHORS),
                native_named_add_ammo_arguments=True,native_remove_and_selection=True,
                no_repeated_ammo_grant=True)
    print(json.dumps(report,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game',type=Path,default=Path(r'D:\SteamLibrary\steamapps\common\Command & Conquer Renegade\Game.dat'))
    parser.add_argument('--objects',type=Path,default=ROOT/'.research/objects.ddb')
    args=parser.parse_args();verify(args.game,args.objects)

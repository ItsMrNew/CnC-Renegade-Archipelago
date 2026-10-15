import struct
import pytest
from renegade.native import effects_hud as h, traps as t, services as s, tt
from test_tt_traps import engine
from test_tt_inventory import REWARD
from test_tt_discs import MODE,CAMERA,PLAYER
from test_native import CONTROL,OBJECT
from test_effects_hud import setup,HUD,PANEL,TEXT
from test_tt_native import memory


def prepared():
    e=engine();e.machine=e.uc;e.fput(REWARD+t.DISCO_END,57);e.fput(REWARD+t.MOON_END,115)
    return e,setup(e,e.a,reward=REWARD,table_top=100)


def test_tt_native_hud_uses_tt_font_draws_timers_and_releases_renderers():
    e,(publish,allocs,draws,deleted,lines,positions)=prepared()
    publish();e.execute(HUD+h.CODE_OFFSET)
    assert allocs==[0x5b4,0x610] and draws==deleted==[PANEL,TEXT]
    assert lines==[('AP EFFECTS           REMAINING',0xfff3d884),
        ('Disco Havoc               0:57',0xfffa8072),('Moon Boots                1:55',0xfffa8072)]
    assert positions==[(380,106),(380,120),(380,134)]
    e.execute(HUD+h.CODE_OFFSET)
    assert positions[-3:]==positions[:3]  # Repaint retains TT's lower table placement.


@pytest.mark.parametrize('reason',['pause','menu','dead','cinematic','stale','load','owner','victory'])
def test_tt_effect_table_only_describes_its_current_player(reason):
    e,(publish,allocs,draws,deleted,lines,positions)=prepared()
    field,value={'pause':(CONTROL+s.PLAYABLE,0),'menu':(e.a.game_mode,0),
        'dead':(CONTROL+s.ALIVE,0),'cinematic':(CAMERA+s.CAMERA_HOST,OBJECT),
        'stale':(CONTROL+s.HEARTBEAT,0),'load':(CONTROL+s.GENERATION,999),
        'owner':(REWARD+t.OWNER,PLAYER+16),'victory':(e.a.pending_continue,1)}[reason]
    e.put(field,value);publish();e.execute(HUD+h.CODE_OFFSET)
    if reason=='pause':assert lines[0][0].startswith('AP EFFECTS (PAUSED)')
    else:assert not allocs


def test_tt_effect_renderer_restores_the_persistent_load_guard_on_detach(memory):
    session=tt.TTSession(memory)
    render=session.inventory.traps.entries[-1]
    assert render[1]==session.hud_guard.control+h.CODE_OFFSET
    session.inventory.traps.update()
    assert memory.u32(render[0])==render[2]
    session.close()
    assert memory.u32(render[0])==render[1]
    # A fresh attachment can validate/reuse the retained compatibility guard.
    again=tt.TTSession(memory);again.close()

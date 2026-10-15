"""Execute each adapter's movement hook: jump lock, stacking and restoration."""
import struct
from types import SimpleNamespace

import pytest

from renegade.native import traps as t, services as s, tt_traps as tt


@pytest.fixture(params=('steam','tt'))
def boots(request):
    if request.param == 'steam':
        from test_traps import TrapEngine, REWARD, CONTROL, OBJECT, MOVE
        e = TrapEngine()
        uc, player = e.machine, OBJECT+768
        mode = OBJECT
        def activate(): e.start(8)
        def movement(): e.invoke(MOVE)
        advance = e.advance
    else:
        from test_tt_traps import engine, invoke, REWARD, CONTROL, PLAYER, WRAPPER, CODE_OFFSET, CLOCK, MODE
        e = engine()
        uc, player = e.uc, PLAYER
        mode = MODE
        uc.mem_write(e.a.band_base+tt.APPLY_CONTROL, b'\xc3')
        uc.mem_write(WRAPPER+CODE_OFFSET, t.build_movement(WRAPPER+CODE_OFFSET, REWARD, CONTROL,
            guard_builder=tt.guarded(e.a), apply_control=e.a.band_base+tt.APPLY_CONTROL, hologram=False))
        def activate():
            e.request(8); e.frame()
        def movement(): invoke(e, WRAPPER+CODE_OFFSET, player)
        def advance(seconds):
            for _ in range(int(seconds*4)):
                e.execute(CLOCK+CODE_OFFSET)
    def put_float(address, value): uc.mem_write(address, struct.pack('<f', value))
    def get_float(address): return struct.unpack('<f', uc.mem_read(address,4))[0]
    def jump(value=2):
        put_float(player+t.UP, value)
        movement()
        return get_float(player+t.UP)
    return SimpleNamespace(engine=e, player=player, reward=REWARD, service=CONTROL, mode=mode,
        activate=activate, advance=advance, jump=jump, put_float=put_float, get_float=get_float)


@pytest.mark.parametrize('up', [-2,0,2])
@pytest.mark.parametrize('moon', [False, True])
def test_concrete_blocks_only_upward_input_even_with_moon_boots(boots, up, moon):
    boots.activate()
    boots.put_float(boots.reward+t.MOON_END, 60 if moon else 0)
    assert boots.jump(up) == (0 if up > 0 else up)


def test_jump_returns_after_stacked_playable_duration_and_pause_does_not_expire_it(boots):
    boots.activate(); boots.activate()
    assert boots.get_float(boots.reward+t.CONCRETE_END) == 60
    boots.engine.put(boots.mode+4, 3)
    boots.advance(10)
    assert boots.get_float(boots.reward+t.CONCRETE_END) == 60
    # Paused gameplay must not have its controls modified by the hook.
    assert boots.jump() == 2
    boots.engine.put(boots.mode+4, 0)
    boots.advance(59.75)
    assert boots.jump() == 0
    boots.advance(.25)
    assert boots.jump() == 2

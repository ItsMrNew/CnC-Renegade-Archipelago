import faulthandler
import struct
import pytest
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_ESP, UC_X86_REG_EFLAGS
from renegade.native.savefiles import inspect_save, relabel, saved_difficulty, INFO, DATA
from renegade.native.savebuilder import *
from renegade.native.thunk import u32


def sample_save():
    info = b'\x01\x08M13.lsd\0' + b'\x03\x08O\0l\0d\0\0\0' + b'\x04\x02\0\0'
    world = b'original world bytes'
    return struct.pack('<II', INFO, len(info)) + info + struct.pack('<II', DATA, 0x80000000 | len(world)) + world


def test_relabel_preserves_every_world_byte_and_unknown_metadata():
    raw = sample_save()
    result = relabel(raw, 'AP Start 01 - The Scorpion Hunters')
    assert inspect_save(result)['map_name'] == 'M13'
    assert inspect_save(result)['description'] == 'AP Start 01 - The Scorpion Hunters'
    assert inspect_save(result)['world_sha256'] == inspect_save(raw)['world_sha256']
    assert result.endswith(b'original world bytes')
    assert b'\x04\x02\0\0' in result


@pytest.mark.parametrize('raw', [b'', b'bad', sample_save()[:-1], sample_save() + b'x'])
def test_rejects_incomplete_saves(raw):
    with pytest.raises(ValueError):
        inspect_save(raw)


def dispatch(command, *, combat=False, star=True, busy=False, stale=False, frames=0, freeze=0,
             star_object=True, completion_test=False, callback_matches=True, mode=1, mission=MISSIONS[0], difficulty=1):
    control, tick, stack, returned = 0x10000000, 0x20000000, 0x30008000, 0x20000100
    code_address = control + 4096
    machine = Uc(UC_ARCH_X86, UC_MODE_32)
    enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        for address, size in [(BASE, 0x500000), (control, 16384), (tick, 4096), (0x30000000, 0x10000)]:
            machine.mem_map(address, size)
    finally:
        if enabled:
            faulthandler.enable()
    original = STEAM.combat_think if combat else MENU_THINK
    machine.mem_write(code_address, build_dispatcher(control, code_address, original, combat, completion_test))
    if completion_test:
        from renegade.native.thunk import build_thunk, CODE_OFFSET, COMPLETED
        observer = control + 8192
        machine.mem_write(observer + CODE_OFFSET, build_thunk(observer))
        machine.mem_write(observer, u32(0xFFF) + u32(10000) + u32(0))
        machine.mem_write(BASE + STEAM.commands + STEAM.mission_command_offset, u32(observer + CODE_OFFSET))
        machine.mem_write(control + EXPECTED_CALLBACK, u32(observer + CODE_OFFSET if callback_matches else 0))
        machine.mem_write(BASE + STEAM.game_mode, u32(mode))
        machine.mem_write(BASE + STEAM.game_data, u32(control + 768))
        machine.mem_write(BASE + STEAM.loaded_map, u32(control + 960))
        machine.mem_write(control + 960, (mission.map_name + '.lsd\0').encode())
    for off, val in [(REQUEST, command), (BUSY, int(busy)), (HEARTBEAT, 1 if stale else 10000),
                     (FRAMES, frames), (FREEZE, freeze), (DIFFICULTY, difficulty), (LEVEL_STATE, 7), (BACKDROP, 2)]:
        machine.mem_write(control + off, u32(val))
    machine.mem_write(BASE + STEAM.tick_iat, u32(tick))
    machine.mem_write(tick, b'\xb8' + u32(10001) + b'\xc3')
    machine.mem_write(BASE + STAR, u32(control + 512 if star else 0))
    machine.mem_write(control + 516, u32(0x87654321 if star_object else 0))
    machine.mem_write(control + 768 + 4, u32(0))  # Active combat mode instance.
    machine.mem_write(BASE + 0x35EE, bytes.fromhex("5f83c41cc3"))  # Think epilogue.
    functions = {0x35EE: 'startup', INIT_SP: 'init', REPLAY: 'replay', START_GAME: 'reload', SAVE_GAME: 'save',
                 STEAM.end_game: 'exit', STEAM.end_game_menu: 'menu', original: 'original'}
    if completion_test:
        functions[STEAM.mission_complete] = 'complete'
    for offset in functions:
        if offset == 0x35EE:
            continue
        machine.mem_write(BASE + offset, b'\xc3')
    machine.mem_write(stack, u32(returned))
    machine.reg_write(UC_X86_REG_ESP, stack)
    machine.reg_write(UC_X86_REG_ECX, control + 768)
    machine.reg_write(UC_X86_REG_EFLAGS, 0x646)
    calls = []
    def hook(uc, address, size, data):
        offset = address - BASE
        if offset in functions:
            calls.append(functions[offset])
            esp = uc.reg_read(UC_X86_REG_ESP)
            if offset == REPLAY:
                assert bytes(uc.mem_read(esp + 4, 8)) == u32(control + MAP) + u32(difficulty)
            if offset == SAVE_GAME:
                assert bytes(uc.mem_read(esp + 4, 12)) == u32(control + FILENAME) + u32(BASE + COMMANDO_SAVE) + u32(0)
            if offset == START_GAME:
                assert bytes(uc.mem_read(esp + 4, 12)) == u32(control + FILENAME) + u32(-1) + u32(0)
            if offset == STEAM.mission_complete:
                assert bytes(uc.mem_read(esp + 4, 4)) == u32(1)
            if offset not in (original, 0x35EE):
                assert not uc.reg_read(UC_X86_REG_EFLAGS) & 0x400
    machine.hook_add(UC_HOOK_CODE, hook)
    machine.emu_start(code_address, returned, count=2000)
    assert machine.reg_read(UC_X86_REG_ESP) == stack + 4
    assert machine.reg_read(UC_X86_REG_ECX) == control + 768
    if "startup" not in calls:
        assert machine.reg_read(UC_X86_REG_EFLAGS) == 0x646
    else:
        assert machine.reg_read(UC_X86_REG_EFLAGS) & 0x400  # Frame setup changes arithmetic flags.
    read = lambda address: int.from_bytes(machine.mem_read(address, 4), 'little')
    if completion_test:
        assert read(observer + COMPLETED) == (1 << (mission.number - 1) if 'complete' in calls else 0)
    return calls, read(control + RESULT), read(control + REQUEST), read(control + FRAMES), read(BASE + CAMPAIGN_STATE)


@pytest.mark.parametrize("difficulty", [0, 1, 2])
def test_load_uses_requested_difficulty_and_real_campaign_position(difficulty):
    assert dispatch(LOAD, difficulty=difficulty) == (['init', 'replay'], 1, 0, 0, 7)


def test_save_and_reload_call_engine_with_correct_abi():
    assert dispatch(SAVE)[:3] == (['save'], 1, 0)
    assert dispatch(RELOAD)[:3] == (['init', 'reload'], 1, 0)
    assert dispatch(EXIT)[:3] == (['exit', 'menu'], 1, 0)


def test_save_refuses_missing_player():
    assert dispatch(SAVE, star=False)[:3] == ([], 2, 0)


@pytest.mark.parametrize('mission', MISSIONS)
def test_test_only_completion_runs_actual_observer_thunk_and_forwards_original(mission):
    assert dispatch(COMPLETE, completion_test=True, freeze=1, mission=mission)[:3] == (['complete'], 1, 0)


def test_save_builder_does_not_enable_forced_completion_by_default():
    assert dispatch(COMPLETE)[0] == ['original']


@pytest.mark.parametrize('overrides', [dict(star=False), dict(star_object=False),
    dict(callback_matches=False), dict(mode=0), dict(freeze=0)])
def test_completion_rejects_unready_game_or_changed_observer(overrides):
    options = dict(completion_test=True, freeze=1)
    options.update(overrides)
    assert dispatch(COMPLETE, **options)[:3] == ([], 2, 0)


def test_busy_reentry_and_stale_lease_do_not_execute_jobs():
    assert dispatch(LOAD, busy=True)[0] == []
    assert dispatch(LOAD, stale=True)[0] == ['original']
    assert dispatch(99)[0] == ['original']


def test_three_startup_frames_then_freeze_without_synthetic_victories():
    assert dispatch(0, combat=True, freeze=1, frames=2)[:4] == (['startup'], 0, 0, 3)
    assert dispatch(0, combat=True, freeze=1, frames=3)[0] == []
    assert dispatch(0, combat=True, freeze=1, frames=3, stale=True)[0] == ['original']


@pytest.mark.parametrize('failure', [None, 'malformed', 'load', 'saved', 'reload'])
@pytest.mark.parametrize('difficulties', [(1,), (0, 1, 2)])
def test_pack_runner_preserves_original_saves_and_only_packages_verified_output(monkeypatch, tmp_path, failure, difficulties):
    from types import SimpleNamespace
    from tools import create_save_pack as runner
    from renegade.data import MISSIONS
    game = tmp_path / 'game'
    saves = game / 'Data' / 'save'
    saves.mkdir(parents=True)
    original = saves / 'autosave.sav'
    original.write_bytes(b'personal original save')
    run = tmp_path / 'savepack-test'
    run.mkdir()
    class Memory:
        modules = [SimpleNamespace(path=game / 'Game.dat')]
        def u32(self, address):
            return {BASE + MENU_SLOT: BASE + MENU_THINK, BASE + STAR: 1,
                    BASE + CAMPAIGN_STATE: 5}.get(address, 0)
        def close(self): pass
    class Builder:
        memory = Memory()
        control = 0x10000000
        campaign = {'M13.MIX': 5}
        def prepare(self, mission, filename, difficulty=1):
            self.filename = filename.split('\\')[-1]
            self.difficulty = difficulty
            self.reloading = False
        def difficulty_level(self):
            wrong = failure == 'reload' if self.reloading else failure == 'load'
            return (self.difficulty + 1) % 3 if wrong else self.difficulty
        def job(self, command):
            original.write_bytes(b'engine autosave changed')
            if command == RELOAD:
                self.reloading = True
            if command == SAVE:
                difficulty = (self.difficulty + 1) % 3 if failure == 'saved' else self.difficulty
                (saves / self.filename).write_bytes(b'bad' if failure == 'malformed' else difficulty_save(difficulty))
        def settle(self): pass
        def player_object(self): return 1
        def close(self): pass
        def wait(self, condition): assert condition()
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'attach_builder', lambda pid: Builder())
    monkeypatch.setattr(runner, 'verify_gate', lambda memory: None)
    monkeypatch.setattr(runner, 'telemetry', lambda memory: {'mode': 1, 'map_name': 'M13.mix'})
    monkeypatch.setattr(runner, 'status', lambda *args, **kwargs: None)
    if failure:
        with pytest.raises(ValueError if failure == 'malformed' else RuntimeError):
            runner.generate(run, MISSIONS[:1], difficulties=difficulties)
        assert not list((tmp_path / 'dist').glob('*.zip'))
    else:
        report = runner.generate(run, MISSIONS[:1], difficulties=difficulties)
        assert report['stage'] == 'passed'
        import zipfile
        with zipfile.ZipFile(report['archive']) as archive:
            names = [n for n in archive.namelist() if n.endswith('.sav')]
            assert len(names) == len(difficulties)
            assert {saved_difficulty(archive.read(n)) for n in names} == set(difficulties)
            for n in names:
                data = archive.read(n)
                assert inspect_save(data)['description'].startswith('AP Start ' + DIFFICULTIES[saved_difficulty(data)] + ' 01')
        assert set(report['difficulty_archives']) == {DIFFICULTIES[d] for d in difficulties}
        for name, path in report['difficulty_archives'].items():
            with zipfile.ZipFile(path) as archive:
                assert archive.testzip() is None
                saves = [n for n in archive.namelist() if n.endswith('.sav')]
                assert len(saves) == 1
                assert DIFFICULTIES[saved_difficulty(archive.read(saves[0]))] == name
    assert original.read_bytes() == b'personal original save'
    assert (run / 'original-saves' / 'autosave.sav').read_bytes() == original.read_bytes()


def test_waits_for_real_player_even_after_initial_frame_budget():
    assert dispatch(0, combat=True, freeze=1, frames=3, star=False)[0] == ['startup']
    assert dispatch(0, combat=True, freeze=1, frames=3, star_object=False)[0] == ['startup']
    assert dispatch(SAVE, star_object=False)[:3] == ([], 2, 0)


def difficulty_save(difficulty, first_load=False):
    def chunk(kind, data):
        return struct.pack('<II', kind, len(data) | 0x80000000) + data
    variables = b'\x01\x01' + bytes([first_load]) + b'\x02\x04' + struct.pack('<I', difficulty)
    world = chunk(0x40000, chunk(916991655, chunk(916991715, variables)))
    raw = sample_save()
    size = struct.unpack_from('<I', raw, 4)[0]
    return raw[:8 + size] + chunk(DATA, world)


@pytest.mark.parametrize('difficulty', [0, 1, 2])
def test_saved_difficulty_is_read_from_combat_state(difficulty):
    raw = difficulty_save(difficulty)
    assert saved_difficulty(raw) == difficulty
    assert saved_difficulty(relabel(raw, 'AP Start')) == difficulty


@pytest.mark.parametrize('raw', [sample_save(), difficulty_save(3), difficulty_save(1, True)])
def test_missing_uninitialized_or_unknown_saved_difficulty_rejected(raw):
    with pytest.raises(ValueError):
        saved_difficulty(raw)


@pytest.mark.parametrize('difficulty', [-1, 3, 255])
def test_native_load_rejects_invalid_difficulty(difficulty):
    assert dispatch(LOAD, difficulty=difficulty)[:3] == ([], 2, 0)

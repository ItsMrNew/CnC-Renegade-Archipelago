import asyncio
import json
import threading
import queue
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from renegade.desktop import DesktopSession, load_settings, save_settings, snapshot
from renegade.client import RenegadeClient
from renegade.data import MISSIONS, slot_data, secondary_id
from test_core import Socket, authenticate, received
from renegade.desktop import preview_missions
from renegade.check_hints import filter_missions, check_kind, disc_description, DISC_HINTS


@pytest.mark.parametrize('action', [
    lambda s: s.use_supply('Ammo Supply'),
    lambda s: s.launch_mission('M01', 1),
    lambda s: s.confirm_mission_restart('request', True),
    lambda s: s.resolve_items(True),
    lambda s: s.set_transfers('all'),
])
def test_ui_actions_tolerate_worker_closing_and_never_cross_sessions(tmp_path, action):
    session = DesktopSession(tmp_path)
    old_client = Mock()
    session.client = old_client
    session.loop = Mock()
    session.loop.call_soon_threadsafe.side_effect = RuntimeError('Event loop is closed')
    action(session)  # A click racing loop shutdown must not escape into Tk.
    assert not old_client.mock_calls
    session.loop.call_soon_threadsafe.side_effect = None
    action(session)
    callback = session.loop.call_soon_threadsafe.call_args.args[0]
    session.client = Mock()  # A later connection must not inherit a queued click.
    callback()
    assert not old_client.mock_calls and not session.client.mock_calls
    session.client = old_client
    session.stopping.set()
    callback()
    assert not old_client.mock_calls


def test_concurrent_log_and_snapshot_overflow_preserves_latest_event(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import time
    class SlowEvictionQueue(queue.Queue):
        def get_nowait(self):
            value = super().get_nowait()
            # Permit another producer to run inside the old eviction/put gap.
            time.sleep(0.001)
            return value
    session = DesktopSession(tmp_path)
    session.events = SlowEvictionQueue(maxsize=2)
    def publish(kind):
        for index in range(50):
            session.publish(kind, index)
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(publish, ('log', 'snapshot', 'log')))
    session.publish('stopped', None)
    assert session.events.qsize() == 2
    session.events.get_nowait()
    assert session.events.get_nowait() == ('stopped', None)


@pytest.fixture(scope='module')
def ui_root():
    # A single Tcl interpreter avoids Windows Tk's intermittent library lookup
    # failures when destroying/recreating several interpreters in one process.
    tk=pytest.importorskip('tkinter')
    root=tk.Tk();root.withdraw()
    yield root
    root.destroy()


def reset_ui(root):
    for callback in root.tk.call('after','info'):
        root.after_cancel(callback)
    for child in root.winfo_children():
        child.destroy()


def test_filters_keep_unfinished_children_of_completed_missions_and_room_totals():
    rows = preview_missions()
    rows[1].update(state='Complete', checks='7/20')
    rows[1]['objectives'][0]['state'] = 'Complete'
    result = filter_missions(rows, query='Retribution', hide_completed=True)
    assert len(result) == 1 and result[0]['state'] == 'Complete' and result[0]['checks'] == '7/20'
    assert 'victory' not in [c['id'] for c in result[0]['objectives']]
    assert rows[1]['objectives'][0]['id'] == 'victory'
    for kind in ('primary', 'secondary', 'disc', 'victory'):
        filtered = filter_missions(rows, kinds={kind})
        assert filtered and all(check_kind(row['map'], c)==kind for row in filtered for c in row['objectives'])
    assert filter_missions(rows, kinds=set()) == []
    result = filter_missions(rows, query='reward conversation')
    assert [(r['map'],[c['id'] for c in r['objectives']]) for r in result] == [('M01',['disc_0'])]


def test_every_disc_has_a_numbered_landmark_hint():
    from renegade.data import DATA_DISCS
    assert sum(len(v) for v in DISC_HINTS.values()) == 23
    for name, ids in DATA_DISCS.items():
        for i, oid in enumerate(ids,1):
            assert disc_description(name, oid).startswith(f'PopTracker D{i}: ')
    assert 'Tunnels floor' in disc_description('M09',1201187)


def test_launcher_filters_search_hints_and_detached_rows_reset_on_disconnect(tmp_path, ui_root):
    from RenegadeDesktop import DesktopWindow
    app = DesktopWindow(ui_root,tmp_path)
    try:
        rows = preview_missions()
        rows[1]['state'] = 'Complete'
        rows[1]['objectives'][0]['state'] = 'Complete'
        rows[1]['objectives'][1]['state'] = 'Missed this attempt'
        app.show_missions(rows)
        app.hide_completed.set(True); app.change_filters()
        assert 'M01' in app.table.get_children()
        assert 'M01:victory' not in app.table.get_children('M01')
        app.search.set('priest')
        assert app.table.get_children() == ('M01',)
        assert app.table.get_children('M01') == ('M01:disc_0',)
        app.table.selection_set('M01:disc_0'); app.selection_changed()
        assert 'reward conversation' in app.check_detail.get()
        app.session.events.put(('stopped',None)); app.poll()
        app.reset_filters()
        assert app.table.set('M01:victory','status') == 'Unknown'
        assert app.table.set('M01:114','status') == 'Unknown'
        assert len(app.table.get_children()) == 12
        # Hidden children from an earlier room must not leak into a room with discs off.
        app.search.set('priest')
        app.show_missions([dict(r,objectives=[c for c in r['objectives'] if not str(c['id']).startswith('disc_')]) for r in rows])
        app.reset_filters()
        assert not app.table.exists('M01:disc_0')
        assert app.table.exists('M01:114')
    finally: reset_ui(ui_root)


def test_checkpoint_dropdown_keeps_selected_older_save_and_sends_its_fingerprint(tmp_path, ui_root, monkeypatch):
    from RenegadeDesktop import DesktopWindow
    app = DesktopWindow(ui_root,tmp_path)
    try:
        choices = [dict(name=f'checkpoint{i}.sav', fingerprint=str(i), modified=1700000000-i*60, difficulty=1) for i in range(3)]
        data = snapshot(RenegadeClient('localhost:1','Havoc',state_dir=tmp_path),{})
        data.update(connection='Connected',mission_launch=dict(menu=True,busy=False,unlocked=['M01'],checkpoints=['M01'],history={'M01':choices}))
        app.display(data);app.table.selection_set('M01');app.selection_changed()
        assert app.checkpoint_choice.current() == 0
        app.checkpoint_choice.current(1)
        app.display(data)
        assert app.checkpoint_choice.current() == 1
        calls=[];monkeypatch.setattr(app.session,'launch_mission',lambda *args:calls.append(args))
        app.launch_mission(True)
        assert calls == [('M01',1,True,dict(name='checkpoint1.sav',fingerprint='1'))]
        app.table.selection_set('M02');app.selection_changed()
        assert str(app.checkpoint_choice['state']) == 'disabled'
    finally: reset_ui(ui_root)


def test_new_controls_fit_minimum_window_and_long_disc_hint(tmp_path, ui_root):
    from RenegadeDesktop import DesktopWindow
    app = DesktopWindow(ui_root,tmp_path)
    try:
        # Constrain the real Tk content frame; a withdrawn Windows top-level does
        # not apply subsequent geometry requests until it is shown.
        frame = app.table.master.master
        frame.pack_forget()
        for width,height in ((920,960),(880,800)):
            frame.place(x=0,y=0,width=width,height=height)
            app.resize_layout(SimpleNamespace(widget=ui_root,height=height))
            app.native.set('Observer: attached\nServices: attached; checkpoints active')
            app.table.selection_set('M01:disc_0');app.selection_changed()
            for theme in ('Light','Dark'):
                app.theme.set(theme);app.apply_theme();ui_root.update()
                assert app.table.winfo_height() >= 125  # Header and four visible checks.
                assert app.search_entry.winfo_width() >= 100
                assert app.log.master.winfo_y()+app.log.winfo_height() <= height-18
                form=app.connect_button.master
                assert app.connect_button.winfo_x()+app.connect_button.winfo_width() <= form.winfo_width()
    finally: reset_ui(ui_root)


def test_snapshot_waits_for_sync_and_distinguishes_pending_from_confirmed(tmp_path):
    async def run():
        client = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path)
        sock = Socket()
        assert all(m['state'] == 'Unknown' for m in snapshot(client, {})['missions'])
        await authenticate(client, sock)
        assert snapshot(client, {})['connection'] == 'Synchronizing'
        await client.handle(sock, dict(cmd='ReceivedItems', index=0, items=received(1)))
        data = snapshot(client, {})
        assert data['missions'][0]['state'] == 'Available'
        assert data['missions'][1]['state'] == 'Locked'
        assert client.mission_finished('M13', True)
        data = snapshot(client, {})
        assert data['pending'] == 3 and data['confirmed'] == 0
        assert data['missions'][0]['state'] == 'Pending sync'
        checks = [MISSIONS[0].check_id(i) for i in range(1, 4)]
        await client.handle(sock, dict(cmd='RoomUpdate', checked_locations=checks))
        assert snapshot(client, {})['missions'][0]['state'] == 'Complete'
        client.connected = False
        data = snapshot(client, {})
        assert data['unlocked'] is None
        assert data['missions'][1]['state'] == 'Unknown'
        assert data['confirmed'] == 3
    asyncio.run(run())


def test_settings_never_persist_password(tmp_path):
    path = tmp_path / 'desktop.json'
    save_settings(path, dict(server=' localhost:1234 ', slot=' Havoc ', gate=True, password='secret'))
    assert 'secret' not in path.read_text()
    assert load_settings(path) == dict(server='localhost:1234', slot='Havoc', gate=True, theme='light', transfers='all')
    path.write_text('broken')
    assert load_settings(path)['gate'] is True


def test_mission_victory_is_visible_with_unfinished_secondaries_and_disconnect(tmp_path):
    async def run():
        client=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);sock=Socket()
        cfg=slot_data(3,2,True)
        await client.handle(sock,dict(cmd='RoomInfo',seed_name='victory-display'))
        await client.handle(sock,dict(cmd='Connected',team=0,slot=1,slot_data=cfg,checked_locations=[],missing_locations=cfg['location_ids']))
        await client.handle(sock,dict(cmd='ReceivedItems',index=0,items=received(2)))
        mission=MISSIONS[1]
        assert client.mission_finished('M01',True)
        row=snapshot(client,{})['missions'][1]
        assert row['state']==row['objectives'][0]['state']=='Pending sync'
        checks=[mission.check_id(i) for i in range(1,4)]+[secondary_id('M01',oid) for oid in (114,102,104,109)]
        await client.handle(sock,dict(cmd='RoomUpdate',checked_locations=checks))
        row=snapshot(client,{})['missions'][1]
        assert row['state']=='Available' and row['checks']=='7/9'
        assert row['objectives'][0]==dict(id='victory',title='Complete mission',state='Complete',checks='3/3')
        assert [o['state'] for o in row['objectives'][1:]]==['Complete']*4+['Available']*2
        client.connected=False
        rows=snapshot(client,{})['missions']
        assert all(row['state']=='Unknown' and all(o['state']=='Unknown' for o in row['objectives']) for row in rows)
        assert rows[1]['checks']=='7/9'  # History remains cached; access/status is unknown.
    asyncio.run(run())


def test_launcher_disconnect_clears_completed_and_pending_child_statuses(tmp_path,ui_root):
    from RenegadeDesktop import DesktopWindow
    root=ui_root
    try:
        app=DesktopWindow(root,tmp_path)
        data=snapshot(RenegadeClient('localhost:1','Havoc',state_dir=tmp_path),{})
        row=data['missions'][1]
        row.update(state='Complete',checks='7/9',objectives=[
            dict(id='victory',title='Complete mission',state='Complete',checks='3/3'),
            dict(id=109,title='Destroy Nod Turrets',state='Complete',checks='1/1'),
            dict(id=100,title='Rescue Clergy',state='Pending sync',checks='0/1')])
        app.display(data)
        assert tuple(app.table['columns'])==('mission','status','checks')
        assert app.table.item('M01:victory','values') == ('Complete mission','Complete','3/3')
        app.session.events.put(('stopped',None));app.poll()
        for iid in ('M01','M01:victory','M01:109','M01:100'):
            assert app.table.set(iid,'status')=='Unknown'
            assert not app.table.item(iid,'tags')
    finally:reset_ui(root)


def test_mission_buttons_require_live_permissions_menu_and_matching_checkpoint(tmp_path,monkeypatch,ui_root):
    from RenegadeDesktop import DesktopWindow
    root=ui_root
    try:
        app=DesktopWindow(root,tmp_path)
        data=snapshot(RenegadeClient('localhost:1','Havoc',state_dir=tmp_path),{})
        data.update(connection='Connected',mission_launch=dict(menu=True,busy=False,unlocked=['M13'],checkpoints=['M13']))
        app.display(data);app.table.selection_set('M13:victory');app.update_launch_buttons()
        assert str(app.start_button['state'])==str(app.continue_button['state'])=='normal'
        calls=[];monkeypatch.setattr(app.session,'launch_mission',lambda *args:calls.append(args))
        app.difficulty.set('Commando');app.launch_mission(True)
        assert calls==[('M13',2,True)]
        app.table.selection_set('M01');app.update_launch_buttons()
        assert str(app.start_button['state'])=='disabled'
        app.table.selection_set('M13');data['mission_launch']['menu']=False;app.display(data)
        assert str(app.start_button['state'])==str(app.continue_button['state'])=='disabled'
        data['mission_launch']['menu']=True;data['connection']='Connecting / reconnecting';app.display(data)
        assert str(app.start_button['state'])=='disabled'
    finally:reset_ui(root)


@pytest.mark.parametrize('answer',[False,True])
def test_difficulty_warning_explains_restart_and_defaults_to_no(tmp_path,monkeypatch,ui_root,answer):
    from RenegadeDesktop import DesktopWindow
    app=DesktopWindow(ui_root,tmp_path);calls=[];prompts=[]
    try:
        def ask(title,text,**kwargs):
            prompts.append((title,text,kwargs));return answer
        monkeypatch.setattr('RenegadeDesktop.messagebox.askyesno',ask)
        monkeypatch.setattr(app.session,'confirm_mission_restart',lambda *args:calls.append(args))
        offer=dict(id='a',title='Rescue and Retribution',saved_difficulty=1,difficulty=2)
        app.confirm_mission_restart(offer);app.confirm_mission_restart(offer)
        assert calls==[('a',answer)] and len(prompts)==1
        assert 'Soldier' in prompts[0][1] and 'Commando' in prompts[0][1]
        assert 'restart this mission from the beginning' in prompts[0][1]
        assert prompts[0][2]['default']=='no' and prompts[0][2]['icon']=='warning'
    finally:reset_ui(ui_root)


def test_desktop_confirmation_targets_only_current_connected_request(tmp_path):
    from types import SimpleNamespace
    import threading
    session=DesktopSession(tmp_path)
    session.loop=SimpleNamespace(call_soon_threadsafe=lambda callback:callback())
    client=SimpleNamespace(ready=True,stop=threading.Event(),mission_request=None,
                           mission_confirmation=dict(id='token',map='M01',difficulty=2))
    session.client=client
    session.confirm_mission_restart('old-token',True);assert client.mission_request is None
    client.ready=False
    session.confirm_mission_restart('token',True);assert client.mission_request is None
    client.ready=True;session.confirm_mission_restart('token',True)
    assert client.mission_request==('M01',2,True,'token')
    client.mission_request=None;session.confirm_mission_restart('token',False)
    assert client.mission_request is None and client.mission_confirmation is None


@pytest.mark.parametrize('bad', [{'server': '', 'slot': 'Havoc'}, {'server': 'http://example.com', 'slot': 'Havoc'},
                               {'server': 'localhost:1', 'slot': ' '}])
def test_invalid_settings_do_not_start_worker(tmp_path, bad):
    session = DesktopSession(tmp_path)
    with pytest.raises(ValueError):
        session.start(bad)
    assert not session.running


def test_worker_stop_waits_for_cleanup_and_reports_failure(monkeypatch, tmp_path):
    import RenegadeClient as entry
    ready = threading.Event()
    cleanup = threading.Event()
    async def run(client, simulation, **kwargs):
        assert simulation is False and kwargs['gate_missions']
        ready.set()
        await client.stop.wait()
        cleanup.set()
        raise RuntimeError('cleanup failed')
    monkeypatch.setattr(entry, 'run', run)
    session = DesktopSession(tmp_path)
    session.start(dict(server='localhost:1', slot='Havoc', gate=False))
    assert ready.wait(3)
    with pytest.raises(RuntimeError):
        session.start(dict(server='localhost:2', slot='Havoc'))
    session.stop()
    session.thread.join(3)
    assert not session.running and cleanup.is_set()
    events = []
    while not session.events.empty():
        events.append(session.events.get_nowait())
    assert ('error', 'cleanup failed') in events
    assert events[-1] == ('stopped', 'cleanup failed')


def test_session_surfaces_native_cleanup_failure(monkeypatch):
    from RenegadeClient import run
    from renegade.native import bridge
    async def watch(client, pid, status=None):
        try:
            await asyncio.sleep(100)
        finally:
            raise RuntimeError('restore failed')
    async def network():
        await asyncio.sleep(0.01)
    monkeypatch.setattr(bridge, 'watch', watch)
    with pytest.raises(RuntimeError, match='restore failed'):
        asyncio.run(run(SimpleNamespace(network=network), False))


def test_early_stop_does_not_start_connection(monkeypatch, tmp_path):
    import RenegadeClient as entry
    async def unexpected(*args, **kwargs):
        pytest.fail('Early-stopped session must not connect')
    monkeypatch.setattr(entry, 'run', unexpected)
    session = DesktopSession(tmp_path)
    session.stopping.set()
    asyncio.run(session._run(dict(server='localhost:1', slot='Havoc', gate=True), None))


def test_legacy_deathlink_override_is_ignored_and_theme_saved(tmp_path):
    path = tmp_path / 'desktop.json'
    path.write_text(json.dumps(dict(server='localhost:1234', slot='Havoc', gate=True, death_link=True)))
    settings = load_settings(path)
    assert settings == dict(server='localhost:1234', slot='Havoc', gate=True, theme='light', transfers='all')
    settings['theme'] = 'dark'
    save_settings(path, settings)
    assert load_settings(path)['theme'] == 'dark'
    assert 'death_link' not in json.loads(path.read_text())


def test_old_disabled_gate_preference_is_ignored(tmp_path):
    path=tmp_path/'desktop.json'
    path.write_text(json.dumps(dict(server='localhost:1',slot='Havoc',gate=False)))
    settings=load_settings(path)
    assert settings['gate'] is True
    settings['gate']=False;save_settings(path,settings)
    assert load_settings(path)['gate'] is True


@pytest.mark.parametrize('theme',['Light','Dark'])
def test_launcher_counters_and_only_item_names_are_coloured(tmp_path,theme,ui_root):
    import logging
    from RenegadeDesktop import DesktopWindow, STATUS_COLOURS, LIGHT_STATUS_COLOURS
    from renegade.desktop import QueueLog
    root=ui_root
    try:
        app=DesktopWindow(root,tmp_path);app.theme.set(theme);app.apply_theme()
        assert len(app.inputs)==3 and not hasattr(app,'gate') and not hasattr(app,'item_labels')
        app.show_progress(dict(unlocked=3,confirmed=2,total=90,logic_confirmed=2,logic_total=33))
        assert app.progress.get()=='Total Checks: 2/90  •  Checks in Logic: 2/33  •  Maps Available: 3/12'
        colours=STATUS_COLOURS if theme=='Dark' else LIGHT_STATUS_COLOURS
        assert app.style.lookup('TotalChecks.TLabel','foreground')==colours['Locked']
        assert app.style.lookup('LogicChecks.TLabel','foreground')==colours['Available']
        def metric_colour(key):
            return app.style.lookup(app.metric_labels[key]['style'],'foreground')
        # Finish currently available checks, then receive a map unlock that
        # brings unfinished checks into logic. Other counters remain independent.
        app.show_progress(dict(unlocked=3,confirmed=33,total=90,logic_confirmed=33,logic_total=33))
        assert metric_colour('logic')==colours['Complete']
        assert metric_colour('total')==colours['Locked'] and metric_colour('maps')==colours['Available']
        app.show_progress(dict(unlocked=4,confirmed=33,total=90,logic_confirmed=33,logic_total=36))
        assert app.metrics['logic'].get()=='Checks in Logic: 33/36'
        assert metric_colour('logic')==colours['Available']
        app.show_progress(dict(unlocked=12,confirmed=33,total=90,logic_confirmed=33,logic_total=90))
        assert metric_colour('maps')==colours['Complete'] and metric_colour('logic')==colours['Available']
        app.show_progress(dict(unlocked=12,confirmed=90,total=90,logic_confirmed=90,logic_total=90))
        assert all(metric_colour(key)==colours['Complete'] for key in app.metrics)
        app.apply_theme()
        assert all(metric_colour(key)==colours['Complete'] for key in app.metrics)
        # Same item name in both aliases, plus Unicode, must leave aliases neutral.
        parts=[('Bullet Hose 👋 sent ',None),('Bullet Hose',0),(' to Bullet Hose',None)]
        record=logging.LogRecord('Renegade',logging.INFO,'',0,''.join(p for p,_ in parts),(),None)
        record.item_parts=parts
        handler=QueueLog(lambda kind,value:app.append_log(value));handler.emit(record)
        ranges=app.log.tag_ranges('item0')
        assert len(ranges)==2 and app.log.get(*ranges)=='Bullet Hose'
        assert str(ranges[0]).split('.')[1]!='0'
        app.append_log('Inventory synchronized')
        assert app.log.tag_ranges('item0')==ranges
        record=logging.LogRecord('Renegade',logging.INFO,'',0,'Received %s',('Permanent Health Upgrade',),None)
        record.item_parts=[('Received ',None),('Permanent Health Upgrade',2)]
        handler.emit(record)
        assert app.log.get(*app.log.tag_ranges('item2'))=='Permanent Health Upgrade'
        # Logging severity, not words in room/player/item names, controls alerts.
        app.append_log('Player ERROR received an item')
        assert not app.log.tag_ranges('alert')
        item_ranges=app.log.tag_ranges('item2')
        for level in (logging.WARNING,logging.ERROR,logging.CRITICAL):
            record.levelno=level;record.levelname=logging.getLevelName(level)
            handler.emit(record)
        assert app.log.tag_ranges('item2')==item_ranges
        alert_text=''.join(app.log.get(first,last) for first,last in zip(app.log.tag_ranges('alert')[::2],app.log.tag_ranges('alert')[1::2]))
        assert all(name in alert_text for name in ('WARNING','ERROR','CRITICAL','Permanent Health Upgrade'))
        assert app.log.tag_cget('alert','foreground')==colours['Locked']
        app.session.events.put(('error','cleanup failed'));app.poll()
        assert 'alert' in app.log.tag_names(app.log.search('ERROR: cleanup failed','1.0'))
        app.session.events.put(('stopped',None));app.poll()
        assert all(value.get().endswith('—') for value in app.metrics.values())
        assert metric_colour('total')==colours['Locked']
        assert metric_colour('logic')==metric_colour('maps')==colours['Available']
    finally:reset_ui(root)
def test_version_switch_restores_separate_profiles_and_refuses_active_session(ui_root, tmp_path, monkeypatch):
    import RenegadeDesktop as desktop
    from renegade.desktop import connection_settings
    steam, tt = tmp_path/'steam', tmp_path/'tt'
    save_settings(steam/'desktop.json', connection_settings('localhost:1', 'Steam slot'))
    save_settings(tt/'desktop.json', connection_settings('localhost:2', 'TT slot', theme='dark'))
    monkeypatch.setattr(desktop, 'state_directory', lambda is_tt: tt if is_tt else steam)
    remembered=[]
    monkeypatch.setattr(desktop, 'remember_build', remembered.append)
    app=desktop.DesktopWindow(ui_root, steam)
    try:
        app.password.set('not carried between profiles')
        app.build_choice.current(1);app.change_build()
        assert app.session.tt_core and app.session.state_dir == tt
        assert app.slot.get() == 'TT slot' and app.theme.get() == 'Dark'
        assert app.password.get() == '' and remembered == [True]
        app.session.thread=SimpleNamespace(is_alive=lambda:True)
        app.build_choice.current(0);app.change_build()
        assert app.session.tt_core and app.build_choice.current()==1
        app.session.thread=None
        app.build_choice.current(0);app.change_build()
        assert not app.session.tt_core and app.session.state_dir == steam
        assert app.slot.get() == 'Steam slot' and app.theme.get() == 'Light'
    finally:reset_ui(ui_root)


def test_tt_mission_buttons_accept_gameplay_and_explain_queued_load(ui_root, tmp_path):
    from RenegadeDesktop import DesktopWindow
    app = DesktopWindow(ui_root, tmp_path, tt_core=True)
    app.update_launch_buttons()
    assert ui_root.title() == 'Renegade Archipelago'
    assert app.build_choice.get() == 'Tiberian Technologies 4.8.4'
    assert str(app.start_button.cget('state')) == 'disabled'
    assert str(app.continue_button.cget('state')) == 'disabled'
    assert app.session.tt_core
    app.table.selection_set('M01')
    app.last_snapshot = dict(connection='Connected',mission_launch=dict(
        menu=True,can_queue=True,busy=False,unlocked=['M01'],checkpoints=['M01']))
    app.update_launch_buttons()
    assert str(app.start_button.cget('state')) == 'normal'
    assert str(app.continue_button.cget('state')) == 'normal'
    app.last_snapshot['mission_launch']['menu']=False
    app.update_launch_buttons()
    assert str(app.start_button.cget('state')) == 'normal'
    assert str(app.continue_button.cget('state')) == 'normal'
    app.last_snapshot['mission_launch'].update(busy=True,status='Load queued; waiting for the cutscene to finish.')
    app.update_launch_buttons()
    assert str(app.start_button.cget('state')) == 'disabled'
    assert str(app.continue_button.cget('state')) == 'disabled'
    assert 'cutscene' in app.launch_hint.get()
    app.last_snapshot['mission_launch']['busy']=False
    app.last_snapshot['connection']='Connecting / reconnecting'
    app.update_launch_buttons()
    assert str(app.start_button.cget('state')) == 'disabled'
    reset_ui(ui_root)


@pytest.mark.parametrize('continuing',[False,True])
@pytest.mark.parametrize('combat_state',[0,1,3])
def test_tt_button_reaches_session_queue_and_native_loader(tmp_path,ui_root,continuing,combat_state):
    from RenegadeDesktop import DesktopWindow
    from renegade.native import tt_campaign, mission_launch
    from test_tt_native import Memory
    from test_checkpoints import save
    from test_primary_objectives import connect
    app=DesktopWindow(ui_root,tmp_path,tt_core=True)
    async def run():
        client=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path/'state')
        await connect(client,Socket())
        mem=Memory()
        mem.write_u32(0x81E854,0x22000000);mem.write_u32(0x22000004,0x22000100)
        mem.write_u32(0x22000100,mem.a.band_base+0x2F32A8);mem.write_u32(0x22000104,combat_state)
        mem.write_u32(mem.a.game_mode,1)  # Still single-player even after returning to the menu.
        mem.write_u32(mem.a.band_base+tt_campaign.MENU_SLOT,mem.a.band_base+tt_campaign.MENU_THINK)
        start=tmp_path/'ap_start_pack_Soldier_02_M01.sav'
        checkpoint=tmp_path/'ap_checkpoint_room_M01.sav'
        for path in (start,checkpoint):path.write_bytes(save())
        runtime=tt_campaign.TTLauncher(mem,tmp_path,'room',mem.a)
        runtime.update(client)
        app.session.client=client
        app.session.loop=asyncio.get_running_loop()
        app.display(snapshot(client,{}))
        app.table.selection_set('M01')
        app.update_launch_buttons()
        button=app.continue_button if continuing else app.start_button
        assert str(button['state'])=='normal'
        button.invoke()  # Real Tk command -> real DesktopSession, not a mocked dispatcher.
        assert client.mission_request is None  # Work is queued onto the client loop.
        await asyncio.sleep(0)
        assert client.mission_request[:3]==('M01',1,continuing)
        runtime.update(client)
        assert mem.u32(runtime.control+mission_launch.REQUEST)==1
        assert mem.u32(runtime.control+mission_launch.MAP)==1
        expected=b'save\\ap_load_room.sav\0'
        assert mem.read(runtime.control+mission_launch.FILENAME,len(expected))==expected
        assert (tmp_path/'ap_load_room.sav').read_bytes()==(checkpoint if continuing else start).read_bytes()
        runtime.close()
    try:asyncio.run(run())
    finally:reset_ui(ui_root)


@pytest.mark.parametrize('discs_enabled',[False,True])
def test_mission_colour_includes_enabled_discs_even_when_hidden(tmp_path,ui_root,discs_enabled):
    from RenegadeDesktop import DesktopWindow
    from renegade.data import DATA_DISCS, OBJECTIVES, disc_id, objective_id
    from test_discs import config
    from test_primary_objectives import connect
    app=DesktopWindow(ui_root,tmp_path)
    async def run():
        client=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path/'state')
        sock=Socket()
        await connect(client,sock,config(discs_enabled))
        mission=MISSIONS[1]
        done={mission.check_id(i) for i in range(1,6)}
        done.update(objective_id('M01',oid) for oid in OBJECTIVES['M01'])
        await client.handle(sock,dict(cmd='RoomUpdate',checked_locations=sorted(done)))
        def display(expected):
            app.display(snapshot(client,{}))
            assert app.table.set('M01','status')==expected
            assert tuple(app.table.item('M01','tags'))==(() if expected=='Unknown' else (expected,))
        display('Available' if discs_enabled else 'Complete')
        assert app.table.set('M01:victory','status')=='Complete'
        app.check_types['disc'].set(False);app.change_filters()
        assert app.table.set('M01','status')==('Available' if discs_enabled else 'Complete')
        if discs_enabled:
            discs=[disc_id('M01',oid) for oid in DATA_DISCS['M01']]
            await client.handle(sock,dict(cmd='RoomUpdate',checked_locations=sorted(done|set(discs[:-1]))))
            display('Available')
            assert client.disc_collected('M01',DATA_DISCS['M01'][-1])
            display('Pending sync')
            await client.handle(sock,dict(cmd='RoomUpdate',checked_locations=sorted(done|set(discs))))
            display('Complete')
        client.connected=False
        display('Unknown')
    try:asyncio.run(run())
    finally:reset_ui(ui_root)


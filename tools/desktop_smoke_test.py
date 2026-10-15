"""Real AP connection/UI smoke test; native hooks replaced with inert test tasks."""
import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import socket
import subprocess
import sys
import time
import tkinter as tk
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from RenegadeDesktop import DesktopWindow, STATUS_COLOURS, LIGHT_STATUS_COLOURS
from renegade.native import bridge, gate, services
from tools.live_test import generate
from renegade.data import MISSIONS, OBJECTIVES, PRIMARY_OBJECTIVES, objective_kind


async def inert_native(client, pid=None, status=None, **kwargs):
    if status:
        status("disabled", "UI test: no game access")
    await client.stop.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hold-seconds", type=int, default=0)
    parser.add_argument("--theme", choices=("Light", "Dark"), default="Light")
    args = parser.parse_args()
    directory = ROOT / ".research" / ("desktop-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(directory / "client.log", encoding="utf-8")])
    ap = Path("C:/ProgramData/Archipelago")
    seed = generate(ap, directory, secondary_objectives=True, primary_objectives=True, checks_per_mission=5)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    bridge.watch = gate.watch_gate = services.watch_services = inert_native
    with (directory / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen([str(ap / "ArchipelagoServer.exe"), str(seed), "--host", "127.0.0.1",
            "--port", str(port), "--disable_save", "--password", ""], stdin=subprocess.PIPE,
            stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        root = tk.Tk()
        if not args.hold_seconds:
            root.withdraw()
        app = DesktopWindow(root, directory / "state")
        app.server.set(f"localhost:{port}")
        app.toggle()
        deadline = time.monotonic() + 40
        report = {}
        def check():
            try:
                if app.connection.get() == "Connected":
                    rows = [app.table.item(iid, 'values') for iid in app.table.get_children()]
                    assert len(rows) == 12
                    assert tuple(app.table['columns'])==('mission','status','checks')
                    assert rows[0][1:] == ('Available', '0/5')
                    for m,row in zip(MISSIONS[1:],rows[1:]):
                        assert row[1:] == ('Locked', f'0/{5+len(OBJECTIVES[m.map_name])}')
                    for m in MISSIONS:
                        for oid in OBJECTIVES[m.map_name]:
                            title,state,count = app.table.item(f'{m.map_name}:{oid}', 'values')
                            assert title.endswith(f'({objective_kind(m.map_name,oid).title()} Objective)')
                            assert state=='Locked' and count=='0/1'
                    assert app.progress.get()=='Total Checks: 0/167  •  Checks in Logic: 0/5  •  Maps Available: 1/12'
                    assert len(app.inputs)==3 and not hasattr(app,'gate') and not hasattr(app,'item_labels')
                    assert app.table.item('M13', 'tags') == ('Available',)
                    assert app.table.item('M01', 'tags') == ('Locked',)
                    assert str(app.transfer_choice['state']) == 'readonly'
                    for choice, scope in (('Your transfers', 'you'), ('Off', 'off'), ('All players', 'all')):
                        app.transfers.set(choice)
                        app.change_transfers()
                        assert json.loads(app.settings_path.read_text())['transfers'] == scope
                    assert 'no game access' in app.native.get()
                    assert not app.password.get()
                    assert str(app.inputs[0]['state']) == 'disabled'
                    assert str(app.theme_choice['state']) == 'readonly'
                    for theme in ('Dark', 'Light', args.theme):
                        app.theme.set(theme)
                        app.change_theme()
                        root.update_idletasks()
                        assert app.connection.get() == 'Connected'
                        assert str(app.log['background']) == ('#171b21' if theme == 'Dark' else '#ffffff')
                        settings = json.loads(app.settings_path.read_text())
                        assert settings['theme'] == theme.lower()
                        assert 'death_link' not in settings
                        colours = STATUS_COLOURS if theme == 'Dark' else LIGHT_STATUS_COLOURS
                        assert all(str(app.table.tag_configure(state, 'foreground')) == colour for state, colour in colours.items())
                    report.update(stage='connected', missions=rows, progress=app.progress.get(), native=app.native.get())
                    report.update(theme=args.theme, both_themes_switched_while_connected=True, status_colours=True, transfer_preferences=True)
                    report.update(primary_rows=sum(map(len,PRIMARY_OBJECTIVES.values())),
                                  objective_suffixes_verified=True, primary_and_secondary_enabled=True)
                    (directory / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                    print("Connected desktop verified; no game hooks. " + str(directory), flush=True)
                    root.after(max(1, args.hold_seconds * 1000), app.close)
                elif time.monotonic() > deadline:
                    raise TimeoutError('Desktop failed to connect')
                else:
                    root.after(100, check)
            except Exception as error:
                report.update(stage='failed', error=str(error), traceback=traceback.format_exc())
                app.close()
        root.after(100, check)
        try:
            root.mainloop()
        finally:
            app.session.stop()
            if app.session.thread:
                app.session.thread.join(15)
            try:
                server.communicate(b'/exit\n', timeout=5)
            except subprocess.TimeoutExpired:
                server.terminate()
                server.wait(timeout=5)
        assert not app.session.running, 'Desktop worker failed to stop'
        assert report.get('stage') == 'connected', report
        report.update(stage='passed', worker_stopped=True, server_stopped=True, native_hooks_used=False)
        (directory / "result.json").write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print('PASS: connected mission list, no game access, clean close/disconnect', flush=True)


if __name__ == '__main__':
    main()

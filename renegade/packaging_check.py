"""Offline executable smoke check: never attaches to a game or AP room."""
import asyncio
import importlib
import json
from pathlib import Path
import tempfile
import tkinter as tk


def check(output):
    from RenegadeDesktop import DesktopWindow
    from .app_paths import resource_root
    from .data import MISSIONS
    from .native.mission_launch import select_save
    from .native.savefiles import inspect_save, saved_difficulty
    for module in ('RenegadeClient', 'renegade.native.bridge', 'renegade.native.services',
                   'renegade.native.tt', 'renegade.tracker_bridge'):
        importlib.import_module(module)
    async def websocket_check():
        from websockets.asyncio.server import serve
        from websockets.asyncio.client import connect
        async def echo(ws):
            async for message in ws:
                await ws.send(message)
        async with serve(echo, '127.0.0.1', 0) as server:
            async with connect(f'ws://127.0.0.1:{server.sockets[0].getsockname()[1]}') as ws:
                await ws.send('launcher-check')
                assert await ws.recv() == 'launcher-check'
    asyncio.run(websocket_check())
    with tempfile.TemporaryDirectory(prefix='renegade-package-') as temp:
        root = tk.Tk()
        root.withdraw()
        try:
            app = DesktopWindow(root, Path(temp), tt_core=False)
            root.update_idletasks()
            assert root.title() == 'Renegade Archipelago'
            assert app.build_choice.cget('values') == ('Steam','Tiberian Technologies 4.8.4')
            assert (resource_root() / 'assets' / 'Renegade.ico').is_file()
            for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md',
                         'assets/licenses/Python-LICENSE.txt', 'assets/licenses/Tcl-Tk-license.terms',
                         'assets/licenses/websockets-LICENSE.txt', 'assets/licenses/PyInstaller-COPYING.txt'):
                assert (resource_root() / name).is_file(), 'Missing packaged notice: ' + name
            save_dir = Path(temp) / 'saves'
            for m in MISSIONS:
                for difficulty in range(3):
                    path = select_save(save_dir, 'package-check', m.map_name, difficulty)
                    raw = path.read_bytes()
                    assert inspect_save(raw)['map_name'] == m.map_name
                    assert saved_difficulty(raw) == difficulty
        finally:
            root.destroy()
    Path(output).write_text(json.dumps(dict(passed=True, window=True, adapters=['Steam','TT'],
        websocket=True, starting_saves=36, game_access=False), indent=2), encoding='utf-8')

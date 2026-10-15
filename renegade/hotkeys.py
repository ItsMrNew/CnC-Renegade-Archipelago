"""Supply shortcuts restricted to foreground, active campaign gameplay."""
import ctypes as ct
from ctypes import wintypes as wt
import logging
import re

from .data import SUPPLIES

KEYS = (0x71, 0x72, 0x73)  # F2, F3, F4
CHAT_BINDINGS = {'BeginPublicMessage', 'BeginTeamMessage', 'BeginPrivateMessage'}


def binding_conflicts(config):
    """Stock multiplayer chat is ignored in missions; respect other custom binds."""
    result = set()
    for action, key in re.findall(r'^\s*(\w+)_(?:Primary|Secondary)\s*=\s*(F[234])_Key\s*$', config, re.M):
        if action not in CHAT_BINDINGS:
            result.add(int(key[1:])+0x6f)
    return result


class SupplyHotkeys:
    def __init__(self, pid, config_path, sample=None):
        self.pid = pid
        self.previous = None
        try:
            self.disabled = binding_conflicts(config_path.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError):
            self.disabled = set(KEYS)
            logging.getLogger('Renegade').warning('Supply hotkeys disabled: cannot verify game key bindings')
        if self.disabled:
            labels = ', '.join(f'F{key-0x6f}' for key in sorted(self.disabled))
            logging.getLogger('Renegade').warning('Unavailable supply shortcuts: %s; launcher buttons remain available', labels)
        self.sample = sample or self.windows_sample()

    def windows_sample(self):
        api = ct.WinDLL('user32', use_last_error=True)
        api.GetAsyncKeyState.argtypes, api.GetAsyncKeyState.restype = [ct.c_int], ct.c_short
        api.GetForegroundWindow.argtypes, api.GetForegroundWindow.restype = [], wt.HWND
        api.GetWindowThreadProcessId.argtypes = [wt.HWND, ct.POINTER(wt.DWORD)]
        api.GetWindowThreadProcessId.restype = wt.DWORD

        def sample():
            pid = wt.DWORD()
            api.GetWindowThreadProcessId(api.GetForegroundWindow(), ct.byref(pid))
            down = {key for key in KEYS if api.GetAsyncKeyState(key) & 0x8000}
            modified = any(api.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5b, 0x5c))
            return pid.value == self.pid, down, modified
        return sample

    def update(self, client, active):
        focused, down, modified = self.sample()
        pressed = down - self.previous if self.previous is not None else set()
        self.previous = down  # Latch even while paused/alt-tabbed; no delayed key press.
        if not (focused and active and client.ready) or modified:
            return
        for key, name in zip(KEYS, SUPPLIES):
            if key in pressed and key not in self.disabled:
                if not client.supply_indexes(name):
                    client.notifications.notice(f'No {name} available')
                elif client.request_supply(name):
                    break  # At most one outstanding supply request.

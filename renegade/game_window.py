"""Best-effort focus after an explicit mission-load request from the launcher."""
import ctypes as ct
from ctypes import wintypes as wt
import os


def focus_game(pid):
    if os.name != 'nt' or not pid:
        return False
    api = ct.WinDLL('user32', use_last_error=True)
    callback_type = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    specs = {
        'EnumWindows': ([callback_type, wt.LPARAM], wt.BOOL),
        'GetWindowThreadProcessId': ([wt.HWND, ct.POINTER(wt.DWORD)], wt.DWORD),
        'IsWindowVisible': ([wt.HWND], wt.BOOL),
        'GetWindow': ([wt.HWND, wt.UINT], wt.HWND),
        'IsIconic': ([wt.HWND], wt.BOOL),
        'ShowWindow': ([wt.HWND, ct.c_int], wt.BOOL),
        'SetForegroundWindow': ([wt.HWND], wt.BOOL),
    }
    for name, (args, result) in specs.items():
        getattr(api,name).argtypes = args
        getattr(api,name).restype = result
    windows = []

    @callback_type
    def visit(hwnd, _):
        owner = wt.DWORD()
        api.GetWindowThreadProcessId(hwnd,ct.byref(owner))
        if owner.value == pid and api.IsWindowVisible(hwnd) and not api.GetWindow(hwnd,4):
            windows.append(hwnd)
        return True

    api.EnumWindows(visit,0)
    if not windows:
        return False
    hwnd = windows[0]
    if api.IsIconic(hwnd):
        api.ShowWindow(hwnd,9)  # SW_RESTORE; never simulate keys or force input attachment.
    return bool(api.SetForegroundWindow(hwnd))

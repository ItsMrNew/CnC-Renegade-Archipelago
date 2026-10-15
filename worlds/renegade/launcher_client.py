"""APWorld launcher adapter; the bundled client stays independent."""
from contextlib import contextmanager
import hashlib
from importlib.resources import files
import io
import json
import logging
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import uuid
import zipfile

LABEL = "Renegade Client"
CACHE_NAME = "RenegadeArchipelagoLauncher"
LOG = logging.getLogger(__name__)


def resources():
    return files(__package__).joinpath("launcher_bundle")


def cache_root():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("Windows LOCALAPPDATA is unavailable; cannot locate the launcher cache.")
    return Path(local) / CACHE_NAME


def valid_cache(directory, manifest):
    for name, digest in manifest["files"].items():
        path = directory.joinpath(*PurePosixPath(name).parts)
        if not path.is_file() or path.is_symlink():
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return False
    return True


def validate_names(names):
    seen = set()
    for name in names:
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or ".." in path.parts or
                "\\" in name or ":" in name or str(path) != name or
                any(part.endswith((".", " ")) for part in path.parts) or
                name.casefold() in seen):
            raise RuntimeError("The bundled client contains an invalid file path.")
        seen.add(name.casefold())


def prepare_client(root=None):
    """Extract only on demand. Cache contents must match the packaged manifest."""
    bundle = resources()
    manifest = json.loads(bundle.joinpath("manifest.json").read_text(encoding="utf-8"))
    digest = manifest["payload_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise RuntimeError("The bundled client manifest is invalid.")
    validate_names(manifest["files"])
    if "Renegade Archipelago.exe" not in manifest["files"]:
        raise RuntimeError("The bundled client executable is missing from its manifest.")
    root = Path(root) if root is not None else cache_root()
    clients = root / "clients"
    clients.mkdir(parents=True, exist_ok=True)
    target = clients / digest
    if valid_cache(target, manifest):
        return target / "Renegade Archipelago.exe"
    payload = bundle.joinpath("client.zip").read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise RuntimeError("The bundled client is damaged. Reinstall the launcher APWorld.")
    with tempfile.TemporaryDirectory(prefix="extract-", dir=clients) as temporary:
        stage = Path(temporary) / "client"
        stage.mkdir()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            validate_names([entry.filename for entry in entries])
            if {entry.filename for entry in entries} != set(manifest["files"]):
                raise RuntimeError("The bundled client file list does not match its manifest.")
            for entry in entries:
                if entry.is_dir() or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise RuntimeError("The bundled client contains an unexpected directory or link.")
                destination = stage.joinpath(*PurePosixPath(entry.filename).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(entry))
        if not valid_cache(stage, manifest):
            raise RuntimeError("The extracted client failed its integrity check.")
        # Never replace a running EXE. Preserve a damaged cache for diagnosis.
        if target.exists() and not valid_cache(target, manifest):
            target.rename(clients / (digest + ".damaged-" + uuid.uuid4().hex))
        try:
            stage.rename(target)
        except FileExistsError as exc:
            # Another launcher may have finished extracting the same payload.
            if not valid_cache(target, manifest):
                raise RuntimeError("Another client extraction is incomplete. Try launching again.") from exc
    return target / "Renegade Archipelago.exe"


def client_environment():
    env = os.environ.copy()
    # Preserve LOCALAPPDATA so standalone and AP-launched clients share profiles.
    # The child is a separate PyInstaller application, not an AP worker.
    for key in list(env):
        if key.startswith("_PYI_"):
            del env[key]
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if getattr(sys, "frozen", False):
        runtime = Path(sys.executable).resolve().parent
        env["PATH"] = os.pathsep.join(
            part for part in env.get("PATH", "").split(os.pathsep)
            if part and not Path(part.strip('"')).resolve().is_relative_to(runtime)
        )
    return env


@contextmanager
def independent_dll_directory():
    """Do not pass the frozen AP runtime's DLL search directory to the client."""
    if sys.platform != "win32":
        yield
        return
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    size = kernel.GetDllDirectoryW(0, None)
    buffer = ctypes.create_unicode_buffer(size + 1)
    kernel.GetDllDirectoryW(len(buffer), buffer)
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.SetDllDirectoryW(buffer.value or None)


def launch_client(*args):
    try:
        if sys.platform != "win32":
            raise RuntimeError("This APWorld bundles the Windows Renegade client. Launch it on Windows.")
        if args:
            raise RuntimeError("Open this client from its Launcher entry, then enter your server and slot. URI links are not supported by this client.")
        root = cache_root()
        executable = prepare_client(root)
        with independent_dll_directory():
            process = subprocess.Popen([str(executable)], cwd=str(executable.parent),
                                       env=client_environment())
        LOG.info("Launched Renegade client (PID %s).", process.pid)
        return process
    except Exception as exc:
        LOG.exception("Could not launch the bundled Renegade client")
        from Utils import messagebox
        messagebox(LABEL, str(exc), error=True)
        return None


def register():
    from worlds.LauncherComponents import Component, Type, components, icon_paths
    if any(component.display_name == LABEL for component in components):
        return
    icon = "renegade_launcher"
    icon_paths[icon] = f"ap:{__package__}/launcher_bundle/icon.png"
    components.append(Component(
        LABEL, func=launch_client, component_type=Type.CLIENT, icon=icon,
        game_name="Command & Conquer: Renegade", supports_uri=False,
        description="Renegade Archipelago: Steam / TT Build",
    ))

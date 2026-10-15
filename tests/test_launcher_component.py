import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location("launcher_adapter", Path(__file__).resolve().parents[1] / "worlds/renegade/launcher_client.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    resource = tmp_path / "resources"
    resource.mkdir()
    contents = {"Renegade Archipelago.exe": b"test executable", "licenses/test.txt": b"license"}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    payload = stream.getvalue()
    manifest = {"payload_sha256": hashlib.sha256(payload).hexdigest(),
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}}
    (resource / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (resource / "client.zip").write_bytes(payload)
    monkeypatch.setattr(adapter, "resources", lambda: resource)
    return resource, tmp_path / "cache with spaces & punctuation", manifest


def test_extract_and_reuse_without_rewriting(bundle):
    _, root, _ = bundle
    first = adapter.prepare_client(root)
    before = first.stat().st_mtime_ns
    assert first.read_bytes() == b"test executable"
    assert adapter.prepare_client(root) == first
    assert first.stat().st_mtime_ns == before
    assert (first.parent / "licenses/test.txt").read_bytes() == b"license"


def test_damaged_cache_is_preserved_and_repaired(bundle):
    _, root, _ = bundle
    first = adapter.prepare_client(root)
    first.write_bytes(b"damaged")
    assert adapter.prepare_client(root).read_bytes() == b"test executable"
    backups = list((root / "clients").glob("*.damaged-*/Renegade Archipelago.exe"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"damaged"


def test_bad_payload_is_not_extracted(bundle):
    resource, root, _ = bundle
    (resource / "client.zip").write_bytes(b"broken zip")
    with pytest.raises(RuntimeError, match="damaged"):
        adapter.prepare_client(root)
    assert not list(root.rglob("*.exe"))


def test_wrong_file_digest_is_not_published(bundle):
    resource, root, manifest = bundle
    manifest["files"]["Renegade Archipelago.exe"] = "0" * 64
    (resource / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="integrity"):
        adapter.prepare_client(root)
    assert not list(root.rglob("*.exe"))


@pytest.mark.parametrize("name", ["../escape.exe", "/escape.exe", "C:/escape.exe", "a\\b",
                                  "a/../b", "a//b", "a/./b", "a:stream", "a./b", "a /b", ""])
def test_reject_unsafe_archive_names(name):
    with pytest.raises(RuntimeError):
        adapter.validate_names([name])


def test_reject_case_collisions():
    with pytest.raises(RuntimeError):
        adapter.validate_names(["Client.exe", "client.exe"])


def test_missing_and_extra_files_rejected(bundle):
    resource, root, manifest = bundle
    manifest["files"]["extra.txt"] = "0" * 64
    (resource / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="file list"):
        adapter.prepare_client(root)


def test_child_shares_settings_and_isolates_bootloader_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "normal"))
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "parent-runtime")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "ap/ArchipelagoLauncher.exe"))
    monkeypatch.setenv("PATH", str(tmp_path / "ap/lib") + adapter.os.pathsep + str(tmp_path / "unrelated"))
    root = adapter.cache_root()
    env = adapter.client_environment()
    assert env["LOCALAPPDATA"] == str(tmp_path / "normal")
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "_PYI_APPLICATION_HOME_DIR" not in env
    assert env["PATH"] == str(tmp_path / "unrelated")
    assert adapter.os.environ["LOCALAPPDATA"] == str(tmp_path / "normal")
    assert adapter.os.environ["_PYI_APPLICATION_HOME_DIR"] == "parent-runtime"


def test_registration_is_idempotent_and_does_not_extract(monkeypatch):
    module = ModuleType("worlds.LauncherComponents")
    module.components = []
    module.icon_paths = {}
    module.Type = SimpleNamespace(CLIENT="client")
    module.Component = lambda name, **kwargs: SimpleNamespace(display_name=name, **kwargs)
    monkeypatch.setitem(sys.modules, "worlds.LauncherComponents", module)
    monkeypatch.setattr(adapter, "resources", lambda: pytest.fail("registration must not extract"))
    adapter.register()
    adapter.register()
    assert len(module.components) == 1
    component = module.components[0]
    assert component.display_name == "Renegade Client"
    assert component.description == "Renegade Archipelago: Steam / TT Build"
    assert component.game_name == "Command & Conquer: Renegade"
    assert "preview" not in component.description.lower()
    assert "experimental" not in component.description.lower()
    assert component.func is adapter.launch_client
    assert component.component_type == "client"
    assert not component.supports_uri


def test_launch_uses_exe_and_never_shell(bundle, monkeypatch):
    _, root, _ = bundle
    monkeypatch.setattr(adapter, "cache_root", lambda: root)
    calls = []
    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(adapter.subprocess, "Popen", popen)
    assert adapter.launch_client().pid == 123
    args, kwargs = calls[0]
    assert len(args) == 1 and args[0].endswith("Renegade Archipelago.exe")
    assert kwargs["cwd"] == str(Path(args[0]).parent)
    assert not kwargs.get("shell")
    assert kwargs["env"]["LOCALAPPDATA"] == adapter.os.environ["LOCALAPPDATA"]


def test_unsupported_platform_shows_actionable_error(monkeypatch):
    messages = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "Utils", SimpleNamespace(messagebox=lambda *a, **kw: messages.append(a)))
    assert adapter.launch_client() is None
    assert "Windows" in messages[0][1]


def test_launch_failure_shows_error(bundle, monkeypatch):
    _, root, _ = bundle
    messages = []
    monkeypatch.setattr(adapter, "cache_root", lambda: root)
    def denied(*a, **kw):
        raise PermissionError("Access denied")
    monkeypatch.setattr(adapter.subprocess, "Popen", denied)
    monkeypatch.setitem(sys.modules, "Utils", SimpleNamespace(messagebox=lambda *a, **kw: messages.append(a)))
    assert adapter.launch_client() is None
    assert "Access denied" in messages[0][1]


def test_release_apworld_embeds_exact_client_and_extracts_from_zip(tmp_path, monkeypatch):
    import shutil
    from importlib.resources import files
    from tools import build as builder
    source = builder.ROOT
    root = tmp_path / 'source'
    for folder in ('worlds', 'recommended-yamls', 'assets/licenses'):
        shutil.copytree(source / folder, root / folder, ignore=shutil.ignore_patterns('__pycache__'))
    (root / 'renegade').mkdir()
    shutil.copy2(source / 'renegade/data.py', root / 'renegade/data.py')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'assets/Renegade.png'):
        shutil.copy2(source / name, root / name)
    monkeypatch.setattr(builder, 'ROOT', root)
    executable = tmp_path / 'Renegade Archipelago.exe'
    executable.write_bytes(b'MZ-exact-release-client')
    world = builder.build(executable)
    with zipfile.ZipFile(world) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read('archipelago.json'))
        assert manifest['version'] == manifest['compatible_version'] == 7
        assert b'_register_launcher_client()' in archive.read('renegade/__init__.py')
        assert archive.read('renegade/launcher_bundle/icon.png').startswith(b'\x89PNG')
        assert not any('tracker/' in name.lower() or '.state/' in name for name in archive.namelist())
        # Import a harmless package shim from the ZIP to exercise importlib.resources
        # through zipimport without needing Archipelago's World dependencies.
        shim = tmp_path / 'resources.zip'
        with zipfile.ZipFile(shim, 'w') as target:
            target.writestr('launcher_resource_test/__init__.py', '')
            for name in archive.namelist():
                if name.startswith('renegade/launcher_bundle/'):
                    target.writestr(name.replace('renegade/', 'launcher_resource_test/', 1), archive.read(name))
    monkeypatch.syspath_prepend(str(shim))
    try:
        monkeypatch.setattr(adapter, 'resources', lambda: files('launcher_resource_test').joinpath('launcher_bundle'))
        extracted = adapter.prepare_client(tmp_path / 'cache')
        assert extracted.read_bytes() == executable.read_bytes()
        assert (extracted.parent / 'LICENSE').read_bytes() == (source / 'LICENSE').read_bytes()
    finally:
        sys.modules.pop('launcher_resource_test', None)
    world_only = builder.build(world_only=True, output=tmp_path / 'world-only.apworld')
    with zipfile.ZipFile(world_only) as archive:
        assert b'_register_launcher_client()' not in archive.read('renegade/__init__.py')
        assert not any('/launcher_bundle/' in name for name in archive.namelist())


def test_missing_exe_never_silently_builds_without_client(tmp_path):
    from tools.build import build
    with pytest.raises(FileNotFoundError, match='Build the launcher first'):
        build(tmp_path / 'missing.exe', output=tmp_path / 'output.apworld')
    assert not (tmp_path / 'output.apworld').exists()


def test_uri_rejected_without_extracting(monkeypatch):
    messages = []
    monkeypatch.setattr(adapter, 'prepare_client', lambda *a: pytest.fail('must not extract for URI'))
    monkeypatch.setitem(sys.modules, 'Utils', SimpleNamespace(messagebox=lambda *a, **kw: messages.append(a)))
    assert adapter.launch_client('archipelago://example') is None
    assert 'URI links are not supported' in messages[0][1]

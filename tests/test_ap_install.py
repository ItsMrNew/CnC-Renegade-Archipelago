from io import BytesIO
from zipfile import ZipFile
import pytest
from tools.ap_install import installed_world


def package(code=b"world code", doc=b"old docs"):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("renegade/__init__.py", code)
        archive.writestr("renegade/docs/setup_en.md", doc)
    return buffer.getvalue()


def test_reuses_matching_world_and_preserves_original(tmp_path):
    target = tmp_path / "custom_worlds/renegade.apworld"
    target.parent.mkdir()
    original = package()
    target.write_bytes(original)
    with installed_world(tmp_path, package(doc=b"new docs")):
        assert target.read_bytes() == original
    assert target.read_bytes() == original


def test_mismatched_world_untouched(tmp_path):
    target = tmp_path / "custom_worlds/renegade.apworld"
    target.parent.mkdir()
    original = package(code=b"different world")
    target.write_bytes(original)
    with pytest.raises(RuntimeError):
        with installed_world(tmp_path, package()):
            pytest.fail("Mismatched package was accepted")
    assert target.read_bytes() == original


def test_removes_only_its_temporary_world(tmp_path):
    with installed_world(tmp_path, package()) as target:
        assert target.exists()
    assert not target.exists()
    with installed_world(tmp_path, package()) as target:
        target.write_bytes(package(code=b"external edit"))
    assert target.exists()

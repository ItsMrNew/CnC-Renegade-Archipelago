"""Temporarily supply a test APWorld without disturbing an existing installation."""
from contextlib import contextmanager
from io import BytesIO
import logging
from zipfile import ZipFile


def world_contents(payload):
    with ZipFile(BytesIO(payload)) as archive:
        # Documentation changes do not alter generation or the client protocol.
        return {name: archive.read(name) for name in archive.namelist()
                if not name.endswith("/") and not name.startswith("renegade/docs/")}


@contextmanager
def installed_world(ap, payload):
    target = ap / "custom_worlds" / "renegade.apworld"
    if (target.parent / "renegade").exists():
        raise RuntimeError("A loose Renegade world is already installed; use a separate AP installation for this test")
    if target.exists():
        if world_contents(target.read_bytes()) != world_contents(payload):
            raise RuntimeError("Installed Renegade APWorld differs from this build. Install dist/renegade.apworld before testing")
        logging.getLogger("LiveTest").info("Reusing the matching installed Renegade APWorld; it will remain installed")
        yield target
        return
    target.parent.mkdir(exist_ok=True)
    with target.open("xb") as handle:
        handle.write(payload)
    try:
        yield target
    finally:
        if target.exists() and target.read_bytes() == payload:
            target.unlink()
        else:
            logging.getLogger("LiveTest").warning("Temporary APWorld changed externally; leaving it untouched")

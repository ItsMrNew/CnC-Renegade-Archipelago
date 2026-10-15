"""Install a verified bundled mission-start save only when one is missing."""
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import zipfile

from .app_paths import resource_root


def install_start_save(save_dir, map_name, difficulty):
    from .native.savefiles import inspect_save, saved_difficulty
    package = resource_root() / 'assets' / 'mission-starts.zip'
    if not package.is_file():
        return None
    with zipfile.ZipFile(package) as archive:
        entries = json.loads(archive.read('manifest.json'))['missions']
        matches = [e for e in entries if e['map_name'] == map_name and e['difficulty'] == difficulty]
        if len(matches) != 1:
            raise ValueError('Bundled mission-start save is missing or ambiguous')
        entry = matches[0]
        filename = entry['filename']
        if Path(filename).name != filename or not filename.startswith('ap_start_') or not filename.endswith('.sav'):
            raise ValueError('Invalid bundled save filename')
        raw = archive.read('saves/' + filename)
    if (hashlib.sha256(raw).hexdigest() != entry['sha256']
            or inspect_save(raw)['map_name'] != map_name or saved_difficulty(raw) != difficulty):
        raise ValueError('Bundled mission-start save failed validation')
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    destination = save_dir / filename
    if destination.exists():
        return destination  # Caller validates it; never overwrite a user's file.
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=save_dir, prefix='ap_install_', suffix='.tmp', delete=False) as stream:
            temp = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, destination)  # Atomic publication, fails if a file already exists.
        except FileExistsError:
            pass
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    logging.getLogger('Renegade').info('Installed starting save for %s (%s)', map_name,
                                      ('Recruit','Soldier','Commando')[difficulty])
    return destination

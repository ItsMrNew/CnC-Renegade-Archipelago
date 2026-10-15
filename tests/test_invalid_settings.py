"""Malformed local JSON must produce a recoverable error, not an attribute crash."""
import json

import pytest

from renegade.desktop import load_settings
from renegade.state import Progress
from renegade.data import location_ids, slot_data


@pytest.mark.parametrize('value', [None, [], 42, 'text', {'server': 3, 'slot': 'Havoc'},
                                 {'server': 'localhost:1', 'slot': []}])
def test_invalid_settings_use_defaults_without_overwriting_file(tmp_path, value):
    path = tmp_path / 'settings.json'
    original = json.dumps(value)
    path.write_text(original, encoding='utf-8')
    assert load_settings(path)['server'] == 'localhost:38281'
    assert path.read_text(encoding='utf-8') == original


@pytest.mark.parametrize('value', [None, [], 42, 'text'])
def test_invalid_progress_is_rejected_without_discarding_it(tmp_path, value):
    progress = Progress(tmp_path, 'seed', 0, 1, location_ids(3), slot_data(3))
    original = json.dumps(value)
    progress.path.write_text(original, encoding='utf-8')
    with pytest.raises(ValueError, match='Invalid saved progress'):
        Progress(tmp_path, 'seed', 0, 1, location_ids(3), slot_data(3))
    assert progress.path.read_text(encoding='utf-8') == original

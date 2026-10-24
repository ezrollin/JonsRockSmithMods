"""Per-user tuner preferences; no audio or measured performances are stored."""
from __future__ import annotations
import json
import math
import os
import tempfile
from pathlib import Path
from intonation_analysis import DEFAULT_TUNING, TUNING_PRESETS
SETTINGS_PATH = Path.home() / 'Documents' / 'JonsRockSmithMods' / 'tuner_settings.json'

def default_settings():
    return {'version': 1, 'device_name': '', 'device_hostapi': '', 'tuning': DEFAULT_TUNING, 'reference_offsets': {}}

def validated_settings(document):
    if not isinstance(document, dict) or type(document.get('version')) is not int or document['version'] != 1:
        raise ValueError('Unsupported tuner preferences')
    result = default_settings()
    for field in ('device_name', 'device_hostapi'):
        value = document.get(field, '')
        if not isinstance(value, str) or len(value) > 512:
            raise ValueError('Invalid saved input identity')
        result[field] = value
    tuning = document.get('tuning', DEFAULT_TUNING)
    if tuning not in TUNING_PRESETS:
        raise ValueError('Unknown saved tuning')
    result['tuning'] = tuning
    offsets = document.get('reference_offsets', {})
    if not isinstance(offsets, dict) or len(offsets) > len(TUNING_PRESETS):
        raise ValueError('Invalid reference offsets')
    for preset, strings in offsets.items():
        if preset not in TUNING_PRESETS or not isinstance(strings, dict) or len(strings) > 6:
            raise ValueError('Invalid tuning reference')
        result['reference_offsets'][preset] = {}
        for number, value in strings.items():
            if number not in {str(n) for n in range(1, 7)} or type(value) not in (int, float) or (not math.isfinite(value)) or (not -100 <= value <= 100):
                raise ValueError('Reference must be -100 to +100 cents for string 1 to 6')
            result['reference_offsets'][preset][number] = float(value)
    return result

def load_tuner_settings(path=SETTINGS_PATH):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Tuner preferences cannot be a symbolic link')
    if not path.exists():
        return default_settings()
    if path.stat().st_size > 65536:
        raise ValueError('Tuner preferences are oversized')
    return validated_settings(json.loads(path.read_text(encoding='utf-8')))

def save_tuner_settings(settings, path=SETTINGS_PATH):
    data = validated_settings(settings)
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Tuner preferences cannot be a symbolic link')
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.tuner-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ValueError('Tuner preferences changed before saving')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

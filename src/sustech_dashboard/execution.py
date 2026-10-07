"""Execution location and capabilities, with legacy CLOUD compatibility."""
import os
from datetime import date


def mode():
    value = os.environ.get('SUSTECH_EXECUTION_MODE')
    if value is None:
        return 'personal_server' if os.environ.get('SUSTECH_CLOUD') == '1' else 'local'
    if value not in {'local', 'personal_server', 'hosted'}:
        raise ValueError('Invalid execution mode')
    return value


def hosted():
    return mode() == 'hosted'


def course_cutoff():
    # Keep the owner's established cutoff. New hosted spaces choose their own.
    from .paths import DATA_ROOT
    from .core import load_json
    value = load_json(DATA_ROOT / 'instance.json', {}).get('course_cutoff')
    return date.fromisoformat(value) if value else (date.min if hosted() else date(2026, 9, 1))


def direct_downloads():
    return mode() != 'local'

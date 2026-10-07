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
    from .academic_calendar import semester_scope
    return semester_scope()['start']


def direct_downloads():
    return mode() != 'local'

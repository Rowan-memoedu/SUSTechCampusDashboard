"""Recognize retired operator endpoints without deleting personal configuration."""
import re
from urllib.parse import urlsplit


def operator_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.hostname == '124.221.144.155' and (
            parsed.path.rstrip('/') in {'/app', '/campus'} or
            re.fullmatch(r'/spaces/[a-f0-9]{24}/?', parsed.path) is not None)
    except ValueError:
        return False


RETIRED_MESSAGE = '运营者托管已停用。请打开本机面板；现有校园凭据、文件和历史记录仍保留。'

"""Recovery policy for local, idempotent production; never approves a result."""
from datetime import datetime, timezone


class StageFailure(ValueError):
    def __init__(self, message, *, kind='regenerate', phase=None):
        super().__init__(message)
        self.kind = kind
        self.phase = phase


# These boundaries cannot be resolved by repeating or relaxing an audit.
PROTECTED_FAILURES = ('受保护', '哈希变化', '题面或来源发生变化', '题面或原件',
                      '审核后的 PDF 已变化', '同名成品已被修改', '双册副本已被修改',
                      '文件必须位于', '未找到明确且可读取的教材', '本机补充教材已变化',
                      '本机教材暂存收据', '复制期间本机教材', '已暂存的本机教材')


def classify(job, exc):
    message = str(exc)
    phase = job['data'].get('active_phase', job['stage'])
    if any(text in message for text in PROTECTED_FAILURES):
        return 'blocked', phase
    if isinstance(exc, StageFailure):
        return exc.kind, exc.phase or phase
    # Migrate already failed jobs conservatively, including pre-recovery versions.
    if '独立审核未通过' in message or '解答不完整' in message or '翻译未完整' in message:
        return 'regenerate', phase
    if isinstance(exc, ValueError) and job['stage'] == 'extract':
        return 'regenerate', 'extract'
    # An interrupted review is not a rejected answer. Keep accepted checkpoints.
    return 'retry', phase


def retry_delay(streak):
    return (30, 120, 600, 1800, 21600)[min(max(streak - 1, 0), 4)]


def elapsed_since(value):
    try:
        return max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds())
    except (ValueError, TypeError):
        return 0

import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which('node') is None, reason='JavaScript runtime required')
def test_evidence_display_never_invents_precision_or_zero_pending():
    source = Path(__file__).parents[1] / 'src/sustech_dashboard/static/coursework.js'
    script = """
    const fs=require('fs'),vm=require('vm');
    const scope={window:{},timestamp:v=>v,countdown:v=>'PRECISE COUNTDOWN',Object};
    vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),scope);
    const api=scope.window;
    console.log(JSON.stringify({
      day:api.courseworkSummary({due_precision:'date',due_date_only:'2026-10-20'}),
      draft:api.courseworkSummary({status:'submitted',latest_attempt_state:'draft',stale:true}),
      failed:api.courseworkCounts([],[{state:'failed'}]),
      partial:api.courseworkCounts([{status:'draft'}],[{state:'partial'}]),
      unknown:api.courseworkCounts([{status:'unknown'}],[{state:'complete'}]),
      complete:api.courseworkCounts([],[{state:'complete'}])
    }));
    """
    result = subprocess.run(['node', '-e', script, str(source)], capture_output=True,
                            text=True, encoding='utf-8', check=True, timeout=20)
    data = json.loads(result.stdout)
    assert data['day']['countdown'] == '具体时刻未提供'
    assert '仅日期' in data['day']['dateText']
    assert '草稿' in data['draft']['note'] and '缓存' in data['draft']['note']
    assert data['failed']['value'] == data['unknown']['value'] == '—'
    assert data['partial']['value'] == '≥ 1'
    assert data['complete']['value'] == 0 and not data['complete']['incomplete']

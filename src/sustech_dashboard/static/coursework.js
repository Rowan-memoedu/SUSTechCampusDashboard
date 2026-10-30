/* Read-only evidence presentation. School submissions keep their existing flow. */
(()=>{
 const sources={gradebook:'评分列',calendar:'课程日历',unknown:'未确认'};
 const latest={draft:'最新一次为草稿',needs_grading:'最新一次待评分',completed:'最新一次已完成',not_attempted:'最新一次未提交',unknown:'最新一次状态未确认'};
 const evidence={receipt:'已读取提交回执',attempt_status:'依据 Blackboard 尝试状态',complete_attempt_list:'已读取完整尝试列表',unavailable:'尝试记录未读取成功',unknown:'提交证据未确认'};
 const errors={source_unavailable:'Blackboard 本次未读取成功',access_unavailable:'当前无访问权限',columns_unavailable:'作业列表读取失败',content_unavailable:'部分作业详情待自动重试',exemptions_unavailable:'豁免状态未核对',attempts_unavailable:'部分尝试记录读取失败',submission_unconfirmed:'部分提交状态未确认',restricted_content:'部分内容不可读取'};
 window.courseworkSummary=a=>{
  const hasMetadata=Object.hasOwn(a,'due_precision');
  const dateText=a.due?timestamp(a.due):a.due_date_only?`${a.due_date_only}（仅日期）`:'未确认截止时间';
  return {dateText:hasMetadata?dateText:a.due?timestamp(a.due):'未标截止时间',
   countdown:a.due?countdown(a.due):a.due_date_only?'具体时刻未提供':'',
   note:[a.stale?'缓存 · 本次未核对':null,a.due_conflict?'截止时间有冲突':null,
    a.status==='submitted'&&a.latest_attempt_state==='draft'?'已提交过 · 最新一次是草稿':null].filter(Boolean).join(' · ')};
 };
 window.renderCourseworkEvidence=(root,a)=>{
  if(!Object.hasOwn(a,'due_precision'))return;
  root.append(node('div',`截止时间来源：${sources[a.due_source]||'未确认'} · ${a.due_precision==='time'?'具体时刻':a.due_precision==='date'?'仅日期':'未确认'}`,'muted'));
  if(a.due_conflict){
   root.append(node('div',a.due?'不同接口的截止时间不一致；倒计时沿用评分列的具体时刻。请核对下方来源与 Blackboard 原页面。':'不同接口的截止时间不一致，尚无可确认的具体时刻。请核对下方来源与 Blackboard 原页面。','notice'));
   (a.due_candidates||[]).forEach(c=>root.append(node('div',`${sources[c.source]||'未确认'}：${c.precision==='date'?c.value:timestamp(c.value)}`,'muted')));
  }
  root.append(node('div',`${evidence[a.submission_evidence]||'提交证据未确认'} · ${latest[a.latest_attempt_state]||latest.unknown}`,'muted'));
  if(a.submitted_at)root.append(node('div',`回执时间：${timestamp(a.submitted_at)}`,'muted'));
  if(a.fetched_at)root.append(node('div',`该作业核验：${timestamp(a.fetched_at)}${a.stale?' · 本次显示缓存':''}`,'muted'));
 };
 window.courseworkCounts=(tasks,coverage=[])=>{
  const pending=tasks.filter(a=>['not_submitted','draft'].includes(a.status)).length;
  const incomplete=!coverage.length||coverage.some(c=>c.state!=='complete')||tasks.some(a=>a.stale||a.status==='unknown');
  return {pending,incomplete,value:incomplete?(pending?`≥ ${pending}`:'—'):pending,
   label:incomplete?'待提交作业 · 读取不完整':'未提交或草稿作业'};
 };
 window.renderAssignmentCoverage=coverage=>{
  const box=$('assignment-coverage');if(!box)return;
  box.hidden=!coverage?.length;if(box.hidden)return;
  const target=clear('assignment-coverage-list');
  coverage.forEach(c=>{
   const label={complete:'已核对',partial:'部分读取',failed:'读取失败'}[c.state]||'未核对';
   const text=[`${c.received??0} 项最新数据`,c.retained?`${c.retained} 项缓存`:null,
    timestamp(c.checked_at),...(c.errors||[]).map(e=>errors[e]||'部分数据未核对')].filter(Boolean).join(' · ');
   row(target,`${c.course} · ${label}`,text);
  });
 };
})();

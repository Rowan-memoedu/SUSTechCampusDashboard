// Read-only adapter. The production host owns authentication; AI never sees it.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const [configFile, weekText, output] = process.argv.slice(2);
const cfg = JSON.parse(await fs.readFile(configFile, 'utf8'));
const {HydroClient, validateConfig} = await import(pathToFileURL(path.join(path.dirname(cfg.helper), 'hydro_check.mjs')));
validateConfig(cfg);
const {LiveCookieSnapshot} = await import(pathToFileURL(cfg.cookie_module));
const auth = new LiveCookieSnapshot({workflowRoot:cfg.cookie_workflow_root,dataRoot:cfg.cookie_data_root});
try {
  const client = new HydroClient(cfg, auth), all = [];
  for (let page = 1; page <= 20; page++) {
    const response = await client.json('/training?page=' + page);
    all.push(...response.tdocs);
    if (all.length >= response.tpcount || !response.tdocs.length) break;
    if (page === 20) throw Error('OJ training inventory incomplete');
  }
  const week = Number(weekText);
  const matches = all.filter(t => Number(t.title.match(/^Exercise\s*(\d+)\b/i)?.[1]) === week);
  if (matches.length !== 1) throw Error('OJ Exercise training is missing or ambiguous');
  const training = await client.json('/training/' + encodeURIComponent(matches[0].docId));
  const ids = [...new Set(training.tdoc.dag.flatMap(n => n.pids))];
  if (!ids.length) throw Error('OJ training has no readable problems');
  const problems = [];
  for (const id of ids) {
    const data = await client.json('/p/' + encodeURIComponent(id)), p = data.pdoc;
    if (p.domainId !== cfg.domain || !p.config.langs.includes('java')) throw Error('OJ problem domain or language mismatch');
    await client.identity(p.pid || String(id));
    let body = p.content;
    try { const translated = JSON.parse(body); body = translated.zh || translated.en || body; } catch {}
    if (body.includes('file://')) throw Error('OJ problem images need source review before automatic conversion');
    problems.push({pid:p.pid || String(id),title:p.title,content:body});
  }
  await fs.writeFile(output + '.pending', JSON.stringify({week,title:training.tdoc.title,problems}));
  await fs.rename(output + '.pending', output);
  console.log(JSON.stringify({week,problems:problems.length}));
} catch (error) {
  console.error('OJ read failed: ' + error.message); process.exitCode = 3;
} finally { auth.clear(); }

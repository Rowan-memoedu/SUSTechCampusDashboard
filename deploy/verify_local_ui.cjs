const {chromium}=require(process.env.CAMPUS_PLAYWRIGHT_MODULE);
const fs=require('fs'),path=require('path'),assert=require('assert');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',d=>input+=d);
process.stdin.on('end',async()=>{
 let browser;
 try{
  const c=JSON.parse(input),report={screenshots:[],errors:[]};
  browser=await chromium.launch({headless:true,executablePath:process.env.CAMPUS_CHROME});
  const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();
  page.on('pageerror',e=>report.errors.push(e.message));
  const unlock=await context.request.post(c.origin+'/auth/unlock',{headers:{Origin:c.origin},data:{token:c.token}});
  assert.equal(unlock.status(),200);
  const base=await (await context.request.get(c.origin+'/api/instance')).json();
  await page.route('**/api/**',route=>route.fulfill({json:{updated_at:'2026-10-07T12:00:00+08:00',source_updated_at:{},errors:{},warnings:[],tis:{},assignments:[],courses:[],items:[],jobs:[],records:[],library:[],ehall:[],agent:{online:false},baseline_ready:false,auto_enabled:false}}));
  let state={...base,managed:true},offline=false;
  state.update={state:'current',available:false,message:'已是当前版本',latest:'0.4.0'};
  await page.route('**/api/instance',route=>offline?route.abort():route.fulfill({json:state}));
  await page.route('**/api/instance/updates/install',async route=>{
   state.update={...state.update,state:'downloading',message:'正在下载并校验更新'};
   await route.fulfill({status:202,json:{accepted:true}});
  });
  const waitText=text=>page.waitForFunction(t=>document.querySelector('#update-state')?.textContent.includes(t),text);
  await page.goto(c.origin+'/');
  await page.waitForFunction(()=>document.querySelector('#update-panel').hidden);
  state.update={state:'available',available:true,message:'有新版本可安装',latest:'0.5.0',notes:'用于验收的更新摘要'};
  await waitText('有新版本');
  assert.equal(await page.locator('#update-notes').textContent(),'用于验收的更新摘要');
  for(const width of [320,375,414,768,1280,1920]){
   await page.setViewportSize({width,height:900});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Local overflow at ${width}`);
   await page.screenshot({path:path.join(c.output,`local-${width}.png`),fullPage:true});
  }
  await page.locator('#update-install').click();
  await waitText('下载');assert(await page.locator('#update-install').isDisabled());
  offline=true;await waitText('重新连接');
  offline=false;state={...state,version:'0.5.0',last_update:{state:'installed',version:'0.5.0'},update:{state:'current',message:'已是当前版本',available:false,latest:'0.5.0'}};
  await waitText('0.5.0');
  assert.equal(await page.locator('#update-panel').isVisible(),true);
  report.reconnected_and_confirmed=true;
  // A fresh old backend initially has no latest-version result after rollback.
  await page.evaluate(()=>sessionStorage.setItem('campus-update:',JSON.stringify({target:'0.6.0',started:Date.now()})));
  state.last_update={state:'rolled_back',failed_version:'0.6.0'};
  state.update={state:'idle',available:false,message:'尚未检查更新'};
  await page.reload();await waitText('已恢复 0.5.0');
  report.rollback_visible_before_next_check=true;
  state.update={state:'error',available:false,message:'更新未安装，原版本继续运行'};state.last_update={};
  await waitText('更新未安装');report.failed_install_visible=true;
  await page.goto(c.origin+'/settings');await page.locator('#preferences input[name=download_root]').waitFor();
  await page.setViewportSize({width:375,height:900});await page.screenshot({path:path.join(c.output,'settings-375.png'),fullPage:true});
  for(const width of [320,375,414,768,1280,1920]){
   await page.setViewportSize({width,height:900});await page.goto(c.origin+'/release-preview/');
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Site overflow at ${width}`);
   const violations=await page.locator('a.button,header a,footer a').evaluateAll(nodes=>nodes.filter(e=>e.getBoundingClientRect().width>innerWidth||getComputedStyle(e).whiteSpace!=='nowrap').length);
   assert.equal(violations,0);
   await page.screenshot({path:path.join(c.output,`release-${width}.png`),fullPage:true});
  }
  // Unsupported devices receive a truthful local-launch explanation.
  await page.locator('#open-local').click();await page.locator('#open-status:not([hidden])').waitFor();
  assert.equal((await page.locator('input[type=password]').count()),0);
  assert.deepEqual(report.errors,[]);
  report.viewport_widths=[320,375,414,768,1280,1920];report.static_site_has_no_login=true;
  report.screenshots=fs.readdirSync(c.output).filter(name=>name.endsWith('.png'));
  fs.writeFileSync(path.join(c.output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }catch(e){console.error(e.stack);process.exitCode=1;}finally{if(browser)await browser.close();}
});

const fs=require('fs'),{spawn}=require('child_process'),{chromium}=require('playwright');
(async()=>{
 const c=JSON.parse(fs.readFileSync(0,'utf8')),errors=[],launches=[];
 const browser=await chromium.launch({executablePath:c.chrome,headless:true});
 try {
  const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1280,height:900}});
  await context.exposeBinding('activateCampus',(_source,uri)=>new Promise((resolve,reject)=>{
   const process=spawn(c.python,[c.script,'activate',c.root],{windowsHide:true,stdio:['pipe','ignore','pipe']});
   let error='';process.stderr.on('data',data=>error+=data);process.stdin.end(uri);
   process.on('exit',code=>{launches.push(code);code===0?resolve():reject(Error('Native handoff failed: '+error.slice(-600)));});
  }));
  await context.addInitScript(({direct})=>{const click=HTMLAnchorElement.prototype.click;HTMLAnchorElement.prototype.click=function(){if(this.href.startsWith('sustech-campus:')){if(!(direct&&this.href.startsWith('sustech-campus://login')))window.activateCampus(this.href);return;}return click.call(this);};},{direct:c.direct});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto(c.origin+'/app/?login=1');
  const login=page.locator('form').first();
  await login.locator('[name=username]').fill('fixture-user');await login.locator('[name=password]').fill('fixture-panel-password');
  await login.locator('button[type=submit]').click();await page.waitForURL(/\/spaces\/[a-f0-9]{24}\/setup$/);
  if(fs.existsSync(c.root+'/local/credentials.dpapi.json'))throw Error('Native campus credentials were preseeded');
  if(c.direct)await page.evaluate(()=>sessionStorage.removeItem('campus-desktop-handoff'));
  const campus=page.locator('#owner-login');await campus.locator('[name=sid]').fill('fixture-student');await campus.locator('[name=password]').fill('fixture-cas-password');await campus.locator('[name=consent]').check();
  await campus.locator('button[type=submit]').click();await page.waitForURL(/\/spaces\/[a-f0-9]{24}\/$/);
  const base=c.origin+'/spaces/'+'f'.repeat(24);
  if((await context.cookies()).some(x=>x.name.startsWith('campus_')))throw Error('Local browser unexpectedly pre-unlocked');
  await page.waitForFunction(()=>document.querySelector('#computer-auto-message')?.textContent.includes('当前电脑已自动连接'),null,{timeout:60000});
  await page.waitForFunction(()=>document.querySelector('main').textContent.includes('Fixture assignment'),null,{timeout:30000});
  await page.waitForFunction(()=>!document.querySelector('#material-download-all').disabled,null,{timeout:30000});
  if(await page.locator('#material-auto').isChecked())throw Error('Automatic saving must default off');
  await page.locator('#material-download-all').click();
  await page.waitForFunction(()=>Array.from(document.querySelectorAll('.material-file-actions button')).some(b=>b.textContent==='打开文件'),null,{timeout:45000});
  if(await page.getByRole('button',{name:'核验并下载',exact:true}).count())throw Error('Saved files still offer verification downloads');
  if(await page.locator('.material-file-actions').getByRole('button',{name:'下载',exact:true}).count())throw Error('Saved file still offers download');
  for(const label of ['打开下载文件夹根目录','打开文件','打开所在文件夹']) {
   await page.getByRole('button',{name:label,exact:true}).click();
   await page.waitForFunction(()=>document.querySelector('#material-connect-message').textContent==='已在当前电脑打开。',null,{timeout:30000});
  }
  await page.goto(base+'/printing');
  await page.waitForFunction(()=>document.querySelector('#service-message').textContent.includes('已连接官方打印系统'),null,{timeout:45000});
  if(!await page.locator('#print-agent-select').inputValue())throw Error('Print agent not selected');
  if(await page.locator('#print-agent-pair').count())throw Error('Misleading print button remains');
  await page.locator('#print-connect-computer').click();
  await page.waitForFunction(()=>document.querySelector('#print-connect-message').textContent.includes('当前电脑已连接'),null,{timeout:30000});
  await page.screenshot({path:c.root+'/printing.png',fullPage:true});
  await page.goto(base+'/');await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.querySelector('#material-summary').textContent.includes('1 个已保存'));
  await page.screenshot({path:c.root+'/downloads-mobile.png',fullPage:true});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');
  if(errors.length||launches.length!==5||launches.some(x=>x!==0))throw Error(JSON.stringify({errors,launches}));
  console.log(JSON.stringify({public_login_once:true,personal_data_after_first_campus_login:true,automatic_pairing_after_login:true,remembered_panel_without_wait:c.direct,no_local_browser_cookie:true,native_handoffs:launches.length,cloud_download_completed:true,reconnect_without_password:true,print_query_via_agent:true,mobile_overflow:false,script_errors:0}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exit(1)});

// Private JSON enters/leaves through stdin/stdout; the Python runner prints only report.
const {chromium}=require(process.env.CAMPUS_PLAYWRIGHT_MODULE);
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',data=>input+=data);
process.stdin.on('end',async()=>{
 let browser,privateInput;
 try{
  const c=JSON.parse(input),report={},scriptErrors=[],originChecks=[],serverErrors=[];privateInput=c;
  browser=await chromium.launch({headless:true,executablePath:process.env.CAMPUS_CHROME});
  let context=await browser.newContext({ignoreHTTPSErrors:c.fixture===true,viewport:{width:390,height:844}});
  let page=await context.newPage();
  const observe=p=>{
   p.on('pageerror',e=>scriptErrors.push(e.name));
   p.on('response',r=>{if([500,502,504].includes(r.status()))serverErrors.push(r.status());});
   p.on('request',async r=>{if(r.isNavigationRequest()&&r.method()==='POST')originChecks.push((await r.allHeaders()).origin===c.origin);});
  };observe(page);
  const state=async()=>{const r=await context.request.get(c.origin+c.prefix+'/api/instance');if(r.status()!==200)throw Error('Private instance not authenticated');return r.json();};
  const login=async(password)=>{
   await page.goto(c.origin+'/app/?login=1',{waitUntil:'domcontentloaded'});
   await page.locator('#username').fill(c.username);await page.locator('#password').fill(password);
   await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}),page.getByRole('button',{name:'进入面板',exact:true}).click()]);
   if(!new URL(page.url()).pathname.startsWith(c.prefix+'/'))throw Error('Login destination incorrect');
  };
  await page.goto(c.origin+'/app/?login=1#invite='+c.invitation,{waitUntil:'networkidle'});
  if(!await page.locator('#activation').evaluate(e=>e.open))throw Error('Invitation not opened');
  if(new URL(page.url()).hash)throw Error('Invitation fragment not cleared');
  await page.locator('#new-user').fill(c.username);await page.locator('#new-password').fill(c.password);
  await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}),page.getByRole('button',{name:'开通并进入',exact:true}).click()]);
  await page.locator('#owner-login').waitFor();
  if((await state()).configured)throw Error('Fresh space inherited a school connection');
  if(await page.locator('[name=remember]').isChecked())throw Error('CAS persistence not opt-in');
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile layout overflow');
  report.invitation_activation=true;report.fresh_unbound=true;report.mobile_layout=true;
  if(c.fixture){
   await page.locator('[name=sid]').fill(c.sid);await page.locator('[name=password]').fill('wrong-fixture-password');
   await page.locator('[name=consent]').check();
   const denied=page.waitForResponse(r=>r.url().endsWith('/api/instance/login')&&r.request().method()==='POST');
   await page.getByRole('button',{name:'验证账号并开始同步'}).click();
   if((await denied).status()!==400)throw Error('Invalid school credential accepted');
   await page.waitForFunction(()=>document.querySelector('#owner-login button').disabled===false);
   if((await state()).configured)throw Error('Failed binding configured instance');
   report.invalid_cas_denied=true;
  }
  await page.locator('[name=sid]').fill(c.sid);await page.locator('[name=password]').fill(c.casPassword);
  await page.locator('[name=course_cutoff]').fill('2026-09-01');
  await page.locator('[name=consent]').check();await page.locator('[name=remember]').check();
  const binding=page.waitForResponse(r=>r.url().endsWith('/api/instance/login')&&r.request().method()==='POST',{timeout:120000});
  await page.getByRole('button',{name:'验证账号并开始同步'}).click();
  if((await binding).status()!==200)throw Error('School binding failed');
  await page.waitForURL(c.origin+c.prefix+'/',{waitUntil:'domcontentloaded'});
  if(!(await state()).configured)throw Error('Binding state not established');
  report.school_binding=true;
  await page.goto(c.origin+c.prefix+'/settings',{waitUntil:'domcontentloaded'});
  await page.locator('#panel-password [name=previous]').fill(c.password);
  await page.locator('#panel-password [name=password]').fill(c.newPassword);
  await Promise.all([page.waitForURL(c.origin+'/app/?login=1',{waitUntil:'domcontentloaded'}),page.locator('#panel-password button').click()]);
  const revoked=await context.request.get(c.origin+c.prefix+'/api/instance');
  if(revoked.status()!==401)throw Error('Changing password did not revoke old session');
  await login(c.newPassword);report.password_change_and_relogin=true;
  const storage=await context.storageState();await context.close();
  context=await browser.newContext({ignoreHTTPSErrors:c.fixture===true,storageState:storage});
  page=await context.newPage();observe(page);
  await page.goto(c.origin+'/app/',{waitUntil:'domcontentloaded'});
  if(!(await state()).configured)throw Error('Reopened browser lost saved session');
  report.reopened_browser=true;
  if(c.disconnect){
   await page.goto(c.origin+c.prefix+'/setup',{waitUntil:'domcontentloaded'});
   const disconnected=page.waitForResponse(r=>r.url().endsWith('/api/instance/disconnect')&&r.request().method()==='POST');
   await page.locator('#disconnect-school').click();
   if((await disconnected).status()!==200)throw Error('Disconnect failed');
   if((await state()).configured)throw Error('Disconnect left school configured');
   report.school_disconnected=true;
  }
  await Promise.all([page.waitForURL(c.origin+'/app/?login=1',{waitUntil:'domcontentloaded'}),page.getByRole('button',{name:'退出面板登录',exact:true}).click()]);
  if((await context.request.get(c.origin+c.prefix+'/api/instance')).status()!==401)throw Error('Logout left API authenticated');
  report.logout_revoked=true;
  await login(c.newPassword);report.final_relogin=true;
  if(scriptErrors.length||serverErrors.length||originChecks.some(valid=>!valid))throw Error('Browser script/server/origin regression');
  report.script_errors=0;report.server_errors=0;report.form_origins_valid=true;
  process.stdout.write(JSON.stringify({report,cookies:await context.cookies(),password:c.newPassword}));
 }catch(error){
  let message=error.name+': '+error.message;
  for(const key of ['invitation','sid','username','password','newPassword','casPassword']){
   const value=privateInput?.[key];if(typeof value==='string'&&value)message=message.split(value).join('[redacted]');
  }
  process.stderr.write(message);process.exitCode=1;
 }
 finally{if(browser)await browser.close();}
});

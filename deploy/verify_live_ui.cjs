const {chromium}=require(process.env.CAMPUS_PLAYWRIGHT_MODULE);
const fs=require('fs'),path=require('path'),assert=require('assert');
(async()=>{
 const output=process.argv[2],publicOutput=process.argv[3];
 fs.mkdirSync(output,{recursive:true});fs.mkdirSync(publicOutput,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:process.env.CAMPUS_CHROME});
 try{
  const context=await browser.newContext(),page=await context.newPage(),errors=[],localOrigins=new Set();
  page.on('pageerror',e=>errors.push(e.name));
  const origin='http://127.0.0.1:18765';
  const token=fs.readFileSync('D:/AppData/SUSTechCampusDashboard/browser-token','utf8');
  assert.equal((await context.request.post(origin+'/auth/unlock',{headers:{Origin:origin},data:{token}})).status(),200);
  await page.route('**/*',route=>{
   const request=route.request();
   localOrigins.add(new URL(request.url()).origin);
   if(!['GET','HEAD'].includes(request.method()))return route.abort();
   return route.continue();
  });
  for(const width of [375,1280]){
   await page.setViewportSize({width,height:900});await page.goto(origin+'/',{waitUntil:'networkidle'});
   assert(!page.url().endsWith('/setup'));
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:path.join(output,`local-${width}.png`),fullPage:true});
  }
  await page.goto(origin+'/settings',{waitUntil:'networkidle'});
  await page.screenshot({path:path.join(output,'settings.png'),fullPage:true});
  assert.deepEqual([...localOrigins],[origin]);
  const publicPage=await browser.newPage();publicPage.on('pageerror',e=>errors.push(e.name));
  for(const width of [320,1280]){
   await publicPage.setViewportSize({width,height:900});
   await publicPage.goto('https://124.221.144.155/app/',{waitUntil:'networkidle'});
   assert(await publicPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert.equal(await publicPage.locator('input[type=password]').count(),0);
   await publicPage.screenshot({path:path.join(publicOutput,`release-${width}.png`),fullPage:true});
  }
  assert.deepEqual(errors,[]);
  const result={verified:true,local_viewports:[375,1280],public_viewports:[320,1280],script_errors:0,local_resources_only:true,school_writes_performed:false};
  fs.writeFileSync(path.join(output,'live-ui.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.name+': '+e.message);process.exitCode=1;});

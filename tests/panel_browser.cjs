// Mocked panel regression checks: no production writes or real parser runs.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const repo=path.resolve(__dirname,'..');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_BIN || undefined,headless:true});
 try {
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  let running=false,started=true,deny=false,submitted=null,statusRequests=0;
  await context.route('**/*',route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(repo,'webapp_static/index.html'),'utf8')});
   if(url.pathname.endsWith('.css'))return route.fulfill({contentType:'text/css',body:fs.readFileSync(path.join(repo,'webapp_static/style.css'),'utf8')});
   if(url.pathname==='/api/status'){
    statusRequests++;
    return route.fulfill({json:{running,can_trigger:true,last_run:null,news:{enabled:false}}});
   }
   if(url.pathname==='/api/pages')return route.fulfill({json:{total:1,sections:[{root:'https://s3454.nubex.ru/22955/',path:'/22955/',pages:[{url:'https://s3454.nubex.ru/22955/',title:'Test',slug:'22955'}]}]}});
   if(url.pathname==='/api/changelog')return route.fulfill({json:[]});
   if(url.pathname==='/api/sources'){
    submitted=route.request().postDataJSON();
    return route.fulfill(deny?{status:403,json:{message:'Введите верный пароль панели.'}}:{status:201,json:{root:'https://s3454.nubex.ru/22955/'}});
   }
   if(url.pathname==='/api/update-now'){
    running=started;
    return route.fulfill({json:{started,reason:started?null:'already_running'}});
   }
   return route.abort();
  });
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://panel.test/');
  await page.clock.install();
  assert.equal(await page.locator('.tilda-link').getAttribute('href'),'https://tilda.cc/projects/');
  assert.equal(await page.locator('.tilda-link').getAttribute('rel'),'noopener noreferrer');
  await page.locator('.section summary').click();
  await page.locator('.embed-btn').click();
  const code=await page.locator('#embed-code').inputValue();
  assert.equal(await page.evaluate(code=>{
   const doc=new DOMParser().parseFromString(code,'text/html');
   const root=doc.querySelector('.lc22-dynamic-app');
   return root.dataset.file==='22955'&&root.children.length===1&&root.textContent.trim()==='Загрузка данных...'&&doc.body.children.length===1;
  },code),true,'loading placeholder must be nested inside the single app container');
  await page.evaluate(()=>{document.execCommand=()=>true});
  await page.locator('#embed-copy').click();
  assert.equal(await page.locator('#embed-copy').innerText(),'Скопировано');
  for(const width of [1280,390]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.locator('#embed-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth),true);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   if(process.env.PANEL_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.PANEL_SCREENSHOT_DIR,'tilda-dialog-'+width+'.png')});
  }
  await page.getByRole('button',{name:'Закрыть',exact:true}).click();
  assert.equal(await page.locator('#embed-dialog').isVisible(),false);
  await page.setViewportSize({width:1280,height:900});
  await page.locator('#open-source-btn').click();
  assert.equal(await page.locator('#source-base-url').inputValue(),'https://s3454.nubex.ru');
  // Exercise HTTP fallback deterministically, then manual-copy fallback.
  await page.evaluate(()=>{document.execCommand=cmd=>{window.__copyCommand=cmd;return true}});
  await page.locator('#copy-source-base').click();
  assert.equal(await page.evaluate(()=>window.__copyCommand),'copy');
  assert.equal(await page.locator('#copy-source-base').innerText(),'Скопировано');
  assert.equal(await page.locator('#source-base-url').evaluate(el=>el.selectionEnd-el.selectionStart),'https://s3454.nubex.ru'.length);
  await page.evaluate(()=>{document.execCommand=()=>false});
  await page.locator('#copy-source-base').click();
  assert.equal(await page.locator('#copy-source-base').innerText(),'Нажмите Ctrl+C');
  await page.locator('#source-url').fill('https://старый.лицей22.рф/22955/');
  await page.locator('#panel-token').fill('test-only');
  await page.locator('#management-submit').click();
  await page.waitForFunction(()=>!document.querySelector('#management-dialog').open);
  assert.equal(submitted.url,'https://старый.лицей22.рф/22955/');
  assert.match(await page.locator('#source-message').innerText(),/Проверяем/);
  await page.clock.runFor(100);
  await page.waitForFunction(()=>document.querySelector('#status-text').textContent.includes('сейчас'));
  assert.ok(statusRequests>=2);
  running=false;
  await page.clock.runFor(1700);
  await page.waitForFunction(()=>document.querySelector('#source-message').hidden);
  // Queued confirmation also expires even if no run transition is observed.
  started=false;
  await page.locator('#open-source-btn').click();
  await page.locator('#source-url').fill('https://старый.лицей22.рф/22956/');
  await page.locator('#management-submit').click();
  await page.waitForFunction(()=>!document.querySelector('#management-dialog').open);
  assert.match(await page.locator('#source-message').innerText(),/следующем/);
  await page.clock.runFor(8100);
  assert.equal(await page.locator('#source-message').isVisible(),false);
  // Invalid credentials keep the dialog and error visible, and permit retry.
  deny=true;
  await page.locator('#open-source-btn').click();
  await page.locator('#source-url').fill('https://старый.лицей22.рф/22957/');
  await page.locator('#management-submit').click();
  await page.locator('#management-error').waitFor();
  assert.equal(await page.locator('#token-field').isVisible(),true);
  assert.equal(await page.locator('#management-submit').isEnabled(),true);
  for(const width of [1280,390]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   assert.equal(await page.locator('#management-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth),true);
  }
  assert.deepEqual(errors,[]);
  console.log('Panel checks passed: copy and manual fallback, old-site URL submission, completion cleanup, queued timeout, auth retry, desktop/mobile layout');
 } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});

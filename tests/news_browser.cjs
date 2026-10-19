// Browser regression suite. Run with Playwright installed, optionally setting
// PLAYWRIGHT_MODULE and CHROME_BIN when using an existing local installation.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const repo = path.resolve(__dirname,'..');
const archive = fs.readFileSync(path.join(repo,'tilda_news_block.html'),'utf8');
const widget = fs.readFileSync(path.join(repo,'tilda_news_widget.html'),'utf8');
const index = Array.from({length:65},(_,i)=>({id:i+1,
  title:i===0?'Needle article':'Очень длинный заголовок школьной новости, который должен корректно переноситься и ограничиваться тремя строками '+(i+1),
  announce:'Короткий анонс школьного события. Дополнительный текст для проверки ограничения длины карточек.',
  date:i===0?'2026-10-20':i<40?'2026-10-19':'2024-01-03',
  cover:i===1?'https://photos.test/broken.jpg':'https://photos.test/'+i+'.jpg',
}));
const bodies=Object.fromEntries(index.map(item=>[String(item.id),{
  body:'<p onclick="window.__xss=1">Article body '+item.id+'</p><script>window.__xss=1</script><img src="javascript:alert(1)" onerror="window.__xss=1"><iframe src="https://evil.test/" srcdoc="<script>window.__xss=1</script>"></iframe><a href="https://s3454.nubex.ru/news/2.html">Related news</a>',
  gallery:[{full:'https://photos.test/a.jpg',thumb:'https://photos.test/a.jpg'}, {full:'https://photos.test/b.jpg',thumb:'https://photos.test/b.jpg'}],
}]));
const html=content=>'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Лицей</title></head><body style="margin:0;font-family:Times New Roman"><header style="position:fixed;top:0;left:0;right:0;height:70px;z-index:100;background:white">Шапка</header>'+content+'</body></html>';
async function assertHeaderClear(page,selector) {
 const top=await page.locator(selector).evaluate(el=>el.getBoundingClientRect().top);
 assert.ok(top>=104&&top<=106,'content starts at 105px below fixed header: '+top);
}

(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_BIN || undefined,headless:true});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  let fullDelay=0,failIndex=false,malformedIndex=false;
  const calls=[];
  await context.route('**/*',async route=>{
   const url=route.request().url();calls.push(url);
   if(url.includes('/news_index.json'))return route.fulfill(failIndex?{status:503,body:'offline'}:{json:malformedIndex?{}:index});
   if(url.includes('/news_full.json')){
    if(fullDelay)await new Promise(resolve=>setTimeout(resolve,fullDelay));
    return route.fulfill({json:bodies});
   }
   if(url.startsWith('https://photos.test/'))return url.endsWith('broken.jpg')?route.fulfill({status:404}):route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#d7e7f4"/></svg>'});
   if(url.startsWith('http://licey22.test/'))return route.fulfill({contentType:'text/html; charset=utf-8',body:html(new URL(url).pathname==='/news'?archive:widget)});
   return route.abort();
  });
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://licey22.test/news');
  await page.locator('.lyc22-news__card').first().waitFor();
  await page.evaluate(()=>document.addEventListener('click',event=>{
    if(event.target.closest('a[data-internal]'))window.__outerLinkHandler=true;
  }));
  assert.equal(await page.locator('.lyc22-news__card').count(),24);
  assert.equal(await page.locator('.lyc22-news__card').first().getAttribute('href'),'/news?n=1');
  assert.match(await page.locator('.lyc22-news__title').evaluate(el=>getComputedStyle(el).fontFamily),/Geologica/);
  for(const [width,columns] of [[1440,3],[1024,3],[768,2],[390,1]]) {
   await page.setViewportSize({width,height:1000});
   assert.equal(await page.locator('.lyc22-news__list').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),columns,'archive columns at '+width);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'archive overflow at '+width);
   await page.evaluate(()=>window.scrollTo(0,0));
   await assertHeaderClear(page,'.lyc22-news__title');
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('link',{name:'2',exact:true}).click();
  await page.waitForURL(/p=2/);
  assert.equal(await page.evaluate(()=>window.__outerLinkHandler),undefined,'internal navigation must not bubble into site handlers');
  assert.equal(await page.locator('.lyc22-news__card').count(),24);
  await assertHeaderClear(page,'.lyc22-news__title');
  const samePageHistory=await page.evaluate(()=>history.length);
  await page.getByRole('link',{name:'2',exact:true}).click();
  assert.equal(await page.evaluate(()=>history.length),samePageHistory,'current page must not duplicate history');
  await page.locator('.lyc22-news__card').nth(8).scrollIntoViewIfNeeded();
  const scroll=await page.evaluate(()=>scrollY);
  await page.locator('.lyc22-news__card').nth(8).click();
  await page.locator('.lyc22-news__body').waitFor();
  await page.waitForFunction(()=>Math.abs(document.querySelector('.lyc22-news__article').getBoundingClientRect().top-105)<1);
  await assertHeaderClear(page,'.lyc22-news__article');
  assert.equal(await page.locator('.lyc22-news__body script,.lyc22-news__body iframe,.lyc22-news__body [onclick],.lyc22-news__body [onerror]').count(),0);
  assert.equal(await page.evaluate(()=>window.__xss),undefined);
  await page.getByRole('link',{name:'← К списку новостей',exact:true}).click();
  await page.waitForURL(/p=2$/);
  await page.waitForFunction(y=>Math.abs(scrollY-y)<8,scroll);
  await page.reload();await page.locator('.lyc22-news__card').first().waitFor();
  assert.match(page.url(),/p=2$/);
  const historyLength=await page.evaluate(()=>history.length);
  await page.getByRole('searchbox').fill('needle');
  assert.equal(await page.locator('.lyc22-news__card').count(),1);
  assert.match(page.url(),/q=needle/);assert.doesNotMatch(page.url(),/p=2/);
  assert.equal(await page.evaluate(()=>history.length),historyLength);
  await page.getByRole('searchbox').fill('');
  await page.getByRole('combobox').selectOption('2024');
  await page.waitForURL(/year=2024/);
  await page.getByRole('link',{name:'2',exact:true}).click();
  assert.equal(await page.locator('.lyc22-news__card').count(),1);
  await page.getByRole('searchbox').fill('no such news');
  assert.equal(await page.locator('.lyc22-news__card').count(),0);
  assert.match(await page.locator('.lyc22-news__empty').innerText(),/Ничего не найдено/);
  await page.goto('http://licey22.test/news?p=9999');await page.locator('.lyc22-news__card').first().waitFor();
  assert.match(page.url(),/p=3/);assert.equal(await page.locator('.lyc22-news__card').count(),17);
  await page.goto('http://licey22.test/news?n=1');await page.locator('.lyc22-news__body').waitFor();
  await assertHeaderClear(page,'.lyc22-news__article');
  await page.getByRole('button',{name:/Открыть фото 1/}).click();
  const dialog=page.getByRole('dialog');await dialog.waitFor();
  await page.keyboard.press('ArrowRight');assert.match(await dialog.innerText(),/2 \/ 2/);
  await page.keyboard.press('Tab');assert.equal(await dialog.locator(':focus').count(),1);
  await page.keyboard.press('Escape');assert.equal(await page.getByRole('dialog').count(),0);
  await page.getByText('Related news').click();await page.waitForURL(/n=2/);
  await page.goBack();await page.waitForURL(/n=1/);
  fullDelay=400;
  const race=await context.newPage();
  await race.goto('http://licey22.test/news');await race.locator('.lyc22-news__card').first().waitFor();
  await race.locator('.lyc22-news__card').first().click();await race.goBack();
  await race.getByRole('searchbox').waitFor();await race.waitForTimeout(650);
  assert.equal(await race.locator('.lyc22-news__body').count(),0);fullDelay=0;
  await page.goto('http://licey22.test/news?n=999999');await page.locator('.lyc22-news__empty').waitFor();
  assert.match(await page.locator('.lyc22-news__empty').innerText(),/Такой новости/);
  const before=calls.filter(u=>u.includes('/news_full.json')).length;
  await page.goto('http://licey22.test/');await page.locator('.lc22-news-home__card').first().waitFor();
  assert.equal(await page.locator('.lc22-news-home__card').count(),4);
  assert.equal(calls.filter(u=>u.includes('/news_full.json')).length,before,'widget must not fetch article bodies');
  assert.equal(await page.getByRole('link',{name:'Все новости',exact:true}).getAttribute('href'),'/news');
  for(const [width,columns] of [[1440,4],[1024,2],[768,2],[390,1]]) {
   await page.setViewportSize({width,height:1000});
   assert.equal(await page.locator('.lc22-news-home__grid').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),columns,'widget columns at '+width);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  }
  await page.locator('.lc22-news-home__card').first().click();await page.waitForURL(/\/news\?n=1/);
  await page.locator('.lyc22-news__body').waitFor();
  failIndex=true;
  await page.goto('http://licey22.test/news');await page.locator('.lyc22-news__error').waitFor();
  failIndex=false;
  await page.getByRole('button',{name:'Повторить',exact:true}).click();await page.locator('.lyc22-news__card').first().waitFor();
  malformedIndex=true;
  await page.goto('http://licey22.test/');await page.locator('.lc22-news-home__status').waitFor();
  await page.waitForFunction(()=>document.querySelector('.lc22-news-home__status').textContent.includes('Не удалось'));
  assert.equal(await page.getByRole('link',{name:'Все новости',exact:true}).count(),1);
  assert.deepEqual(errors,[]);
  console.log('Browser checks passed: both components, 4 viewport widths, pagination, search/year URLs, history/scroll, deep links, gallery/keyboard, safe HTML, races, errors/retry, light widget');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});

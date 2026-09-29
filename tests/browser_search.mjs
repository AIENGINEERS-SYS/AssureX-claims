import assert from 'node:assert/strict';

export async function runSearch({baseURL, call, click, fill, wait, has, evaluate, screenshot, pause}) {
  await call('Page.navigate', {url: baseURL + '/search'});
  await fill('[name="email"]', 'customer@example.com');
  await fill('[name="password"]', process.env.ASSUREX_TEST_PASSWORD);
  await click('.sx-login button');
  await wait(has('h1', 'Search your workspace'));
  await fill('[aria-label="Search in"]', 'claims');
  await wait(has('.sx-result-summary', '1 matching records'));
  await fill('#assurex-search', 'Laptop');
  await wait(has('.sx-suggestions', 'Products'));
  await click('button[aria-controls="advanced-search-filters"]');
  await click('[name="claim_status"][value="submitted"]');
  await wait(has('.sx-result-summary', '1 matching records'));
  await fill('[name="saved_search_name"]', 'Submitted laptops');
  await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Save search').click()");
  await wait(has('.sx-saved', 'Submitted laptops'));
  await fill('#assurex-search', 'no results');
  await wait(has('.sx-empty', 'No matching claims'));
  await click('.sx-saved button');
  await wait(has('.sx-result-summary', '1 matching records'));
  assert.equal(await evaluate("document.querySelector('#assurex-search').value"), 'Laptop');
  await screenshot('search-desktop.png');
  await call('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(200);
  assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true, 'Search page overflows on mobile');
  await screenshot('search-mobile.png');
  await click('[aria-label="Delete Submitted laptops"]');
  await wait('document.querySelectorAll(".sx-saved").length === 0');
  // The integrated claims list uses the same reusable, paginated controls.
  await click('.sx-nav a[href="/claims"]');
  await wait(has('h1', 'My claims'));
  await wait(has('.sx-result-summary', '1 matching records'));
  await click('.sx-result a');
  await wait('!!document.querySelector(".confirmation")');
}

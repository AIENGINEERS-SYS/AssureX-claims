import assert from 'node:assert/strict';

export async function runReports({baseURL, call, click, fill, wait, has, evaluate, screenshot, pause}) {
  await call('Page.navigate', {url: baseURL + '/reports'});
  await fill('[name="email"]', 'customer@example.com');
  await fill('[name="password"]', process.env.ASSUREX_TEST_PASSWORD);
  await click('.report-login button');
  await wait(has('h1', 'Report center'));
  await click('.report-actions button');
  await wait(has('.report-count', '1 claims'));
  assert.equal(await evaluate('document.querySelectorAll(".report-table tbody tr").length'), 1);
  await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Export CSV').click()");
  await wait(has('.report-job', 'Download CSV'));
  assert.ok(await evaluate("document.querySelector('.report-job').textContent.includes('Completed')"));
  await screenshot('reports-desktop.png');
  // Cross-section navigation preserves the in-memory session.
  await click('.report-nav a[href="/products"]');
  await wait(has('h1', 'Products & warranties'));
  await click('a[href="/reports"]');
  await wait(has('h1', 'Report center'));
  await wait(has('.report-job', 'Download CSV'));
  await call('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
  await pause(200);
  assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true, 'Mobile report page overflows');
  await screenshot('reports-mobile.png');
  await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Sign out').click()");
  await wait(has('h1', 'Sign in to your report center'));
}

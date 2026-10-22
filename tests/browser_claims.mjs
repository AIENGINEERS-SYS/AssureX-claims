import assert from 'node:assert/strict';

export async function runClaims({baseURL, call, click, fill, wait, has, evaluate, screenshot, pause}) {
  async function login() {
    await fill('#email', 'customer@example.com');
    await fill('#password', process.env.ASSUREX_TEST_PASSWORD);
    await click('.login button');
  }
  const action = text => `Array.from(document.querySelectorAll('.actions button')).find(b=>b.textContent===${JSON.stringify(text)})`;
  async function button(text) {
    await wait(`${action(text)} && !${action(text)}.disabled`);
    await evaluate(`${action(text)}.click()`);
  }
  await call('Page.navigate', {url: baseURL + '/claims'});
  await login();
  await wait(has('h1', 'My claims'));
  await click('.title-row button');
  await wait(has('h1', 'Submit a claim'));
  await button('Next');
  await wait(has('#product_id-error', 'Select a product'));
  const product = await evaluate("Array.from(document.querySelector('#product_id').options).find(o=>o.value).value");
  await fill('#product_id', product);
  await button('Next');
  await wait(has('h2', 'Tell us what happened'));
  await fill('#fault_date', '2026-09-20');
  await fill('#fault_type', 'Electrical Failure');
  await fill('#damage_category', 'Moderate');
  await fill('#description', 'Too short');
  await button('Next');
  await wait(has('#description-error', '20 characters'));
  const description = '<img src=x onerror="window.attacked=true"> The product no longer powers on.';
  await fill('#description', description);
  await fill('#repair_history', 'No previous repairs.');
  await wait(has('.save-state', 'All changes saved'));
  await button('Save and exit');
  await wait(has('h1', 'My claims'));
  await click('a[href^="/claims/draft/"]');
  await wait(has('h2', 'Tell us what happened'));
  assert.equal(await evaluate("document.querySelector('#description').value"), description);
  await button('Next');
  await wait(has('h2', 'Add supporting documents'));
  await button('Next');
  await wait(has('.error', 'Upload the required supporting evidence'));
  for (const type of ['product_image', 'damage_evidence']) {
    await wait(`!document.querySelector('#upload-${type}').disabled`);
    await evaluate(`(async () => {
      const canvas=document.createElement('canvas'); canvas.width=8; canvas.height=8;
      const context=canvas.getContext('2d');
      context.fillStyle = ${JSON.stringify({receipt:'#112233', product_image:'#223344', serial_number_image:'#334455', damage_evidence:'#445566'}[type])};
      context.fillRect(0,0,8,8);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
      const transfer=new DataTransfer(); transfer.items.add(new File([blob],'${type}.png',{type:'image/png'}));
      const input=document.querySelector('#upload-${type}'); input.files=transfer.files;
      input.dispatchEvent(new Event('change',{bubbles:true}));
    })()`);
    await wait(`document.body.textContent.includes('${type}.png') && !document.querySelector('#upload-${type}').disabled`);
  }
  // Product/damage sections never show receipt extraction inputs.
  await evaluate("Array.from(document.querySelectorAll('.ocr-panel')).forEach(panel=>panel.open=true)");
  await pause(300);
  assert.equal(await evaluate(`document.querySelectorAll('input[id$="-invoice_number"],input[id$="-retailer"],input[id$="-purchase_date"]').length`), 0);
  await button('Save and exit');
  await wait(has('h1', 'My claims'));
  await click('a[href^="/claims/draft/"]');
  await wait(has('h2', 'Add supporting documents'));
  assert.equal(await evaluate("document.querySelectorAll('.document-result').length"), 2);
  await button('Next');
  await wait(has('h2', 'Review your claim'));
  assert.ok(await evaluate(`document.body.textContent.includes(${JSON.stringify(description)})`));
  assert.equal(await evaluate('window.attacked'), undefined);
  assert.equal(await evaluate("document.querySelectorAll('main img').length"), 0);
  await call('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
  assert.ok(await evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'));
  await screenshot('phase5-review-mobile.png');
  await button('Submit claim');
  await wait("/^CLM-\\d{4}-\\d{6,}$/.test(document.querySelector('h1')?.textContent || '')");
  await wait("!!document.querySelector('.assessment-panel')");
  await screenshot('phase5-confirmation.png');
  await call('Page.reload');
  await login();
  await wait("!!document.querySelector('.confirmation')");
  await wait("Array.from(document.querySelectorAll('.assessment-panel button')).some(button => button.textContent === 'View Assessment Report')");
  await evaluate("Array.from(document.querySelectorAll('.assessment-panel button')).find(button => button.textContent === 'View Assessment Report').click()");
  await wait("!!document.querySelector('article[aria-label=\"Assessment report\"]')");
  assert.ok(await evaluate("document.querySelector('.assessment-panel').textContent.includes('recommendation')"));
  assert.equal(await evaluate('window.attacked'), undefined);
  assert.ok(await evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'));
  await screenshot('assessment-report-mobile.png');
  await evaluate("(async () => {const {api}=await import('/src/claims/api.js'); await api.post('/auth/logout'); try {await api.get('/claims/my');} catch {}})()");
  await wait("!!document.querySelector('[aria-label=\"Sign in again\"]')");
  await login();
  await wait("!document.querySelector('[aria-label=\"Sign in again\"]')");
  assert.ok(await evaluate("!!document.querySelector('article[aria-label=\"Assessment report\"]')"));
}

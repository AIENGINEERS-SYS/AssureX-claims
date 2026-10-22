import assert from 'node:assert/strict';

export async function runRoles({baseURL, call, click, fill, wait, has, evaluate}) {
  for (const [role, title] of [['customer','Your protection'], ['employee','Assigned claims'], ['reviewer','review'], ['admin','every claim']]) {
    await call('Page.navigate', {url: baseURL + '/dashboard'});
    await fill('#email', `${role}@example.com`);
    await fill('#password', process.env.ASSUREX_TEST_PASSWORD);
    await click('.login-form button');
    await wait("!!document.querySelector('.sidebar')");
    if (role === 'employee') {
      await wait(has('h1', title));
      assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).some(button => /Approve|Reject|Assign employee/.test(button.textContent))"), false);
    }
    const denied = role === 'admin' ? '/claims' : '/dashboard/operations';
    await evaluate(`window.history.pushState(null, '', ${JSON.stringify(denied)}); window.dispatchEvent(new PopStateEvent('popstate'))`);
    await wait(has('h1', 'Access denied'));
    assert.equal(await evaluate("!!document.querySelector('input[type=password]')"), false);
  }
}

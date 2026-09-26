// Real Chromium tests using only Node built-ins and a private headless browser profile.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {readFile, mkdir, writeFile} from "node:fs/promises";
import path from "node:path";
const [scenario, baseURL, executable, profile] = process.argv.slice(2);
await mkdir(profile, {recursive:true});
const browser = spawn(executable, ["--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
  "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1", `--user-data-dir=${profile}`, "about:blank"], {windowsHide:true, stdio:"ignore"});
let startupError;
browser.on("error", (error) => {startupError = error;});
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let socket, session, counter = 0;
const pending = new Map(), errors = [];
async function call(method, params = {}, target = session) {
  const id = ++counter;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {pending.delete(id); reject(new Error(`CDP timeout: ${method}`));}, 12000);
    pending.set(id, {resolve, reject, timer});
    socket.send(JSON.stringify({id, method, params, ...(target ? {sessionId:target} : {})}));
  });
}
async function evaluate(expression) {
  const result = await call("Runtime.evaluate", {expression, returnByValue:true, awaitPromise:true, userGesture:true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}
async function wait(expression) {
  const deadline = Date.now()+12000;
  while(Date.now()<deadline) {if(await evaluate(expression)) return; await pause(100);}
  throw new Error(`UI condition timed out: ${expression}\n${await evaluate("document.querySelector('#view').innerText")}`);
}
const q = JSON.stringify;
const has = (selector,text) => `document.querySelector(${q(selector)})?.textContent.includes(${q(text)})`;
async function click(selector) {
  await wait(`!!document.querySelector(${q(selector)})`);
  await evaluate(`document.querySelector(${q(selector)}).click()`);
}
async function fill(selector,value) {
  await wait(`!!document.querySelector(${q(selector)})`);
  await evaluate(`(() => {const el=document.querySelector(${q(selector)}); el.focus(); el.value=${q(value)}; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
}
async function screenshot(name) {
  await mkdir(".pytest_cache",{recursive:true});
  const result = await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:true});
  await writeFile(path.join(".pytest_cache", name),Buffer.from(result.data,"base64"));
}
async function fillProduct() {
  for(const [name,value] of Object.entries({name:"Living room television",brand:"Samsung",category:"Electronics",model_number:"QLED-55",
      serial_number:"BROWSER-001",purchase_date:"2025-01-01",purchase_price:"450000.50",retailer:"Example store",warranty_duration:"2",warranty_duration_unit:"years",coverage:"Parts and labour"})) {
    await fill(`#product-form [name="${name}"]`,value);
  }
}
try {
  let endpoint;
  const deadline = Date.now()+12000;
  while(Date.now()<deadline) {
    if(startupError) throw startupError;
    try {const [port, route] = (await readFile(path.join(profile,"DevToolsActivePort"),"utf8")).trim().split(/\r?\n/); endpoint=`ws://127.0.0.1:${port}${route}`; break;}
    catch {await pause(100);}
  }
  assert.ok(endpoint,"Headless browser did not start");
  socket = new WebSocket(endpoint);
  await new Promise((resolve,reject) => {socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
  socket.addEventListener("message",({data}) => {
    const message = JSON.parse(data);
    if(message.id) {
      const entry = pending.get(message.id); if(!entry) return;
      pending.delete(message.id); clearTimeout(entry.timer);
      if(message.error) entry.reject(new Error(message.error.message)); else entry.resolve(message.result);
    } else if(message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
    else if(message.method === "Log.entryAdded" && message.params.entry.text.includes("Content Security Policy")) errors.push(message.params.entry.text);
  });
  const target = await call("Target.createTarget",{url:"about:blank"},null);
  session = (await call("Target.attachToTarget",{targetId:target.targetId,flatten:true},null)).sessionId;
  await call("Page.enable"); await call("Runtime.enable"); await call("Log.enable");
  await call("Emulation.setDeviceMetricsOverride",{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await call("Page.navigate",{url:baseURL+"/products"});
  await click('[data-action="login"]');
  await fill('#auth-form [name="email"]',"customer@example.com");
  await fill('#auth-form [name="password"]',process.env.ASSUREX_TEST_PASSWORD);
  await click("#auth-submit");
  await wait(has("h1","Products & warranties"));
  if(scenario === "xss") {
    const name = '<img src=x onerror="window.attacked=true">';
    await wait(has("tbody",name));
    assert.equal(await evaluate("document.querySelectorAll('tbody img').length"),0);
    assert.equal(await evaluate("window.attacked === undefined"),true);
    await click(".product-cell");
    await wait(has("h1",name));
    assert.equal(await evaluate("window.attacked === undefined"),true);
  } else {
    await click('.page-heading a[href="/products/new"]'); await fillProduct();
    await wait(has("#expiry-preview","2027"));
    await click('#product-form button[type="submit"]');
    await wait(has("h1","Living room television"));
    assert.ok(await evaluate(has("#notice","Product registered")));
    await click('[data-action="add-warranty"]');
    assert.equal(await evaluate('document.querySelector("#warranty-form [name=start_date]").value'),"2027-01-02");
    await fill('#warranty-form [name="provider"]',"Extended Care");
    await click('#warranty-form button[type="submit"]');
    await wait("document.querySelectorAll('.warranty-card').length === 2");
    assert.equal(await evaluate('document.querySelector(".warranty-card:last-child").textContent.includes(\'{"description"\')'),false,"Empty coverage must not render internal JSON");
    assert.ok(await evaluate(has(".coverage-hero .status","Active")));
    await click('.detail-actions a[href$="/edit"]');
    await fill('#product-form [name="name"]',"Family room television");
    await click('#product-form button[type="submit"]');
    await wait(has("h1","Family room television"));
    await screenshot("phase4-details.png");
    await click('.back-link[href="/products"]');
    await wait("document.querySelectorAll('tbody tr').length === 1");
    await fill('#filters [name="q"]',"no matching product");
    await wait(has(".empty-state h2","No matching products"));
    await click('[data-action="clear-filters"]');
    await wait("document.querySelectorAll('tbody tr').length === 1");
    await fill('#filters [name="status"]',"Expired");
    await wait(has(".empty-state h2","No matching products"));
    await click('[data-action="clear-filters"]');
    await wait("document.querySelectorAll('tbody tr').length === 1");
    await screenshot("phase4-desktop.png");
    await call("Emulation.setDeviceMetricsOverride",{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await pause(150);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"),true,"Mobile page overflows viewport");
    await screenshot("phase4-mobile.png");
    await click('.page-heading a[href="/products/new"]'); await fillProduct();
    await click('#product-form button[type="submit"]');
    await wait(has("#product-form .form-error","already registered"));
    assert.equal(await evaluate('document.querySelector("#product-form [name=serial_number]").value'),"BROWSER-001");
    assert.equal(await evaluate('document.querySelector("#product-form [name=purchase_price]").value'),"450000.50");
    await click("#sign-out");
    await wait(has("h1","Your products, protected."));
    assert.equal(await evaluate('document.querySelector("#product-form") === null'),true);
  }
  assert.deepEqual(errors,[],"No unhandled JavaScript or CSP errors");
  console.log(`Browser scenario passed: ${scenario}`);
} catch(error) {
  if(session) await screenshot("phase4-failure.png").catch(() => {});
  console.error(error);
  process.exitCode = 1;
} finally {
  if(socket?.readyState === WebSocket.OPEN) {await call("Browser.close",{},null).catch(() => {});socket.close();}
  browser.kill();
}

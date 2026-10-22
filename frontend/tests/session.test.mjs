import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {mkdtemp, rm} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import axios from 'axios';

const root = path.resolve(import.meta.dirname, '..');
const temp = await mkdtemp(path.join(root, 'node_modules', '.session-test-'));
const originalAdapter = axios.defaults.adapter;
try {
  const file = path.join(temp, 'api.mjs');
  await build({entryPoints:[path.join(root,'src/claims/api.js')], bundle:true, platform:'node', format:'esm',
    packages:'external', define:{'import.meta.env':'{}'}, outfile:file});
  const {api, setSession, getSession, onExpired} = await import(pathToFileURL(file));
  const userA = {user:{id:1,role:'customer'},access_token:'old-a',refresh_token:'refresh-a'};
  const userB = {user:{id:2,role:'customer'},access_token:'access-b',refresh_token:'refresh-b'};
  let refreshCalls, requests, release, started, expired = 0;
  onExpired(() => expired++);
  function configure() {
    refreshCalls = requests = 0;
    let signal;
    started = new Promise(resolve => {signal = resolve;});
    axios.defaults.adapter = async config => {
      refreshCalls++; signal();
      return new Promise(resolve => {release = value => resolve({data:value,status:200,statusText:'OK',headers:{},config});});
    };
    api.defaults.adapter = async config => {
      requests++;
      if (!config._retried) throw new axios.AxiosError('Expired', 'ERR_BAD_REQUEST', config, null,
        {status:401,data:{error:{message:'expired'}},headers:{},config});
      return {data:config.headers.Authorization,status:200,statusText:'OK',headers:{},config};
    };
  }
  configure(); setSession(userA);
  let pending = api.post('/claims/1/assessment/retry', {}).catch(error => error);
  await started; setSession(null); release({...userA,access_token:'renewed-a'}); await pending;
  assert.equal(getSession(), null, 'A refresh must not restore a signed-out session');
  assert.equal(requests, 1, 'A signed-out mutation must not be replayed');
  assert.equal(expired, 0);

  configure(); setSession(userA);
  pending = api.post('/auth/me', {}).catch(error => error);
  // Auth operations deliberately do not refresh; use a protected claim mutation instead.
  await pending;
  pending = api.post('/claims/1/assessment/retry', {}).catch(error => error);
  await started; setSession(userB); release({...userA,access_token:'renewed-a'}); await pending;
  assert.equal(getSession().user.id, 2, 'An old refresh must not replace the new account');
  assert.equal(requests, 2, 'An old mutation must not be replayed under the new account');

  configure(); setSession(userA);
  const first = api.get('/claims/1/assessment'), second = api.get('/claims/2/assessment');
  await started; await new Promise(resolve => setImmediate(resolve));
  assert.equal(refreshCalls, 1, 'Concurrent expired requests share one refresh');
  release({...userA,access_token:'renewed-a'});
  const responses = await Promise.all([first,second]);
  assert.ok(responses.every(response => response.data === 'Bearer renewed-a'));
  assert.equal(requests, 4);
  setSession(null);
  console.log('3 session refresh race checks passed');
} finally {
  axios.defaults.adapter = originalAdapter;
  assert.ok(temp.startsWith(path.join(root,'node_modules') + path.sep));
  await rm(temp,{recursive:true,force:true});
}

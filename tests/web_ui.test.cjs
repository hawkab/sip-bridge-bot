const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = name => fs.readFileSync(path.join(__dirname, '../web/', name), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function notifications(permission, replies = []) {
  const listeners = new Map();
  let requests = 0, subscriptions = 0;
  const window = {
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: name => listeners.delete(name),
  };
  const Notification = {permission, requestPermission: async () => {
    requests++;
    const reply = replies.shift();
    if (reply instanceof Error) throw reply;
    Notification.permission = reply;
    return reply;
  }};
  window.Notification = Notification;
  vm.runInNewContext(source('notifications.js'), {window, Notification, console: {error(){}}});
  window.SipNotifications.start(async () => {subscriptions++;});
  return {listeners, get requests(){return requests;}, get subscriptions(){return subscriptions;}};
}
test('automatic prompt and granted permission subscription', async () => {
  const n = notifications('default', ['granted']); await flush();
  assert.equal(n.requests,1); assert.equal(n.subscriptions,1); assert.equal(n.listeners.size,0);
  const granted = notifications('granted'); await flush();
  assert.equal(granted.requests,0); assert.equal(granted.subscriptions,1);
});
test('denied permission does not loop', async () => {
  const n = notifications('denied'); await flush();
  assert.equal(n.requests,0); assert.equal(n.listeners.size,0);
});
test('gesture fallback supports dismissal and rejection without repeated prompts', async () => {
  for (const reply of ['default', new Error('gesture required')]) {
    const n = notifications('default', [reply,'granted']); await flush();
    assert.equal(n.requests,1);
    n.listeners.get('pointerup')(); await flush();
    assert.equal(n.requests,2); assert.equal(n.subscriptions,1); assert.equal(n.listeners.size,0);
  }
  const n = notifications('default', ['default','default']); await flush();
  n.listeners.get('keydown')(); await flush();
  assert.equal(n.requests,2); assert.equal(n.listeners.size,0);
});
function serviceWorker(fetcher) {
  const handlers = {}, cached = [], deleted = [];
  const fallback = new Response('offline spinner');
  const cache = {add: async request => {
    const url = typeof request === 'string' ? request : request.url;
    if (url.includes('192-any')) throw new Error('Missing optional icon');
    cached.push(url);
  }, match: async url => String(url).includes('offline.html') ? fallback : undefined};
  const self = {location: new URL('https://example.com/sip/sw.js'),
    addEventListener: (name, fn) => handlers[name] = fn,
    skipWaiting: async()=>{}, clients: {claim:async()=>{}}};
  vm.runInNewContext(source('sw.js'), {self, caches: {open:async()=>cache, keys:async()=>['sip-pwa-v4','other-app'], delete:async key=>deleted.push(key)}, fetch:fetcher, URL, Request, AbortController, setTimeout, clearTimeout});
  return {handlers,cached,deleted, async navigate(url, method='GET',mode='navigate') {
    let response;
    handlers.fetch({request:{url,method,mode},respondWith: promise=>response=promise});
    return response;
  }};
}
test('offline shell installed even when optional icon fails; unrelated caches retained', async () => {
  const sw = serviceWorker(); let pending;
  sw.handlers.install({waitUntil: p=>pending=p}); await pending;
  assert(sw.cached.includes('https://example.com/sip/offline.html'));
  sw.handlers.activate({waitUntil:p=>pending=p}); await pending;
  assert.deepEqual(sw.deleted,['sip-pwa-v4']);
});
test('network failures and server outages render offline shell; live responses remain intact', async () => {
  for (const fetcher of [async()=>{throw new Error('offline')}, async()=>new Response('',{status:503})]) {
    const sw=serviceWorker(fetcher);
    assert.equal(await (await sw.navigate('https://example.com/sip/index.php?view=call&id=123')).text(),'offline spinner');
  }
  const sw=serviceWorker(async()=>new Response('login',{status:403}));
  assert.equal((await sw.navigate('https://example.com/sip/')).status,403);
});
test('API, POST, unrelated pages and recordings never receive cached HTML', async () => {
  const sw=serviceWorker(async()=>{throw new Error('must not intercept')});
  for (const url of ['https://example.com/sip/index.php?action=list_calls','https://example.com/sip/transcription_settings.php','https://example.com/sip/recordings/test.wav','https://example.com/other/']) {
    assert.equal(await sw.navigate(url),undefined);
  }
  assert.equal(await sw.navigate('https://example.com/sip/index.php','POST'),undefined);
});

test('batch delete sends exactly selected IDs and CSRF; cancellation sends no request', async () => {
  const index = source('index.php');
  const start = index.indexOf('    async function deleteSelected(kind)');
  const end = index.indexOf('    function renderCallsTable()', start);
  for (const confirmed of [false,true]) {
    const sent=[];
    const state={deleting:false,listEpoch:0,authenticated:true,deletionCsrf:'token',calls:{selected:new Set(['one','two'])}};
    const context={state,stopPolling(){},startPolling(){},refreshMainArea(){},
      confirmDeletion:async(kind,count)=>{assert.equal(kind,'calls');assert.equal(count,2);return confirmed;},
      apiPost:async(action,payload)=>{sent.push({action,payload});return {deleted_ids:['one','two']};},
      loadTarget:async()=>{},bootstrapKnownIds:async()=>{},showToast(){}};
    vm.createContext(context);
    vm.runInContext(index.slice(start,end), context);
    await context.deleteSelected('calls');
    assert.equal(state.deleting,false);
    assert.equal(sent.length,confirmed?1:0);
    if (confirmed) {
      assert.equal(sent[0].action,'delete_events');
      assert.deepEqual(JSON.parse(JSON.stringify(sent[0].payload)),{kind:'calls',ids:['one','two'],csrf_token:'token'});
      assert.equal(state.calls.selected.size,0);
    } else assert.equal(state.calls.selected.size,2);
  }
});
test('selection survives pagination and is pruned only when IDs disappear from the whole store',()=>{
  const index=source('index.php'), start=index.indexOf('    function applyListState('), end=index.indexOf('    async function loadCalls()',start);
  const state={deletionCsrf:'',calls:{items:[{id:'one'}],meta:{page:1},selected:new Set(['one','two']),allIds:['one','two']}};
  const context={state,isSamePayload:(a,b)=>JSON.stringify(a)===JSON.stringify(b)};vm.createContext(context);vm.runInContext(index.slice(start,end),context);
  context.applyListState('calls',{items:[{id:'two'}],all_ids:['one','two','three'],meta:{page:2},csrf_token:'csrf'});
  assert.deepEqual([...state.calls.selected],['one','two']);
  context.applyListState('calls',{items:[{id:'two'}],all_ids:['two','three'],meta:{page:2}});
  assert.deepEqual([...state.calls.selected],['two']);
});

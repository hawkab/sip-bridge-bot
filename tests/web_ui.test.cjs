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

test('call durations omit empty units and use Russian plurals', () => {
  const index = source('index.php');
  const context = {}; vm.createContext(context);
  vm.runInContext(index.slice(index.indexOf('    function formatDuration('), index.indexOf('    function getSortIndicator(')), context);
  for (const [seconds, full, compact] of [
    [3602,'1 час 2 секунды','1 ч. 2 сек.'], [135,'2 минуты 15 секунд','2 мин. 15 сек.'],
    [60,'1 минута','1 мин.'], [59,'59 секунд','59 сек.'], [663,'11 минут 3 секунды','11 мин. 3 сек.'],
    [0,'0 секунд','0 сек.'], [3600,'1 час','1 ч.'], [7200,'2 часа','2 ч.'],
    [18000,'5 часов','5 ч.'], [75600,'21 час','21 ч.'], [1321,'22 минуты 1 секунда','22 мин. 1 сек.'],
    [11,'11 секунд','11 сек.'], [14,'14 секунд','14 сек.'], [22,'22 секунды','22 сек.'],
  ]) {
    assert.equal(context.formatDuration(seconds), full);
    assert.equal(context.formatDuration(String(seconds), true), compact);
  }
  for (const invalid of [null, undefined, '', 'invalid', -1, Infinity]) assert.equal(context.formatDuration(invalid), '—');
});

test('settings Save follows actual changes and returns to disabled after saving or reverting', () => {
  const index=source('index.php');
  const s={backend:'gigaam',savedBackend:null,loading:true,saving:false,csrfToken:''};
  const context={state:{settings:s},escapeHtml:String}; vm.createContext(context);
  vm.runInContext(index.slice(index.indexOf('    function canSaveSettings('),index.indexOf('    async function loadSettings(')),context);
  const disabled=()=>/id="saveTranscriptionSettings"[^>]*\bdisabled/.test(context.renderSettings());
  assert(disabled());
  Object.assign(s,{loading:false,csrfToken:'token',savedBackend:'gigaam'}); assert(disabled());
  s.backend='whisper'; assert(!disabled());
  s.backend='gigaam'; assert(disabled());
  s.backend='whisper'; s.saving=true; assert(disabled());
  s.savedBackend='whisper'; s.saving=false; assert(disabled());
  s.backend='gigaam'; s.error='Network error'; assert(!disabled());
});

test('manual SMS sender and draft survive polling; stale SIM response cannot overwrite a newer choice', async () => {
  const index=source('index.php');
  const s={contextId:'legacy',chatEpoch:0,senderSelectable:true,draft:{sender:'1',number:'+79991111111',text:'Unsaved message',request_key:'key'}};
  const pending=[];
  const context={state:{outbox:s,detailView:'sms',detailItem:{id:'legacy'}},
    apiGet:(action,query)=>new Promise(resolve=>pending.push({query,resolve})),saveSmsDraft(){}};
  vm.createContext(context);
  vm.runInContext(index.slice(index.indexOf('    async function loadSmsChat('),index.indexOf('    async function submitSms(')),context);
  const old=context.loadSmsChat('legacy');
  s.draft.sender='2'; const latest=context.loadSmsChat('legacy');
  assert.equal(pending[0].query.sender,'1'); assert.equal(pending[1].query.sender,'2');
  const response=port=>({sim_port:port,number:'+79991111111',messages:[],ports:[],connected:true,csrf_token:'csrf',can_reply:true,sender_selectable:true});
  pending[1].resolve(response(2)); await latest;
  pending[0].resolve(response(1)); await old;
  assert.equal(s.draft.sender,'2'); assert.equal(s.draft.text,'Unsaved message'); assert.equal(s.canReply,true);
  const poll=context.loadSmsChat('legacy'); assert.equal(pending[2].query.sender,'2');
  pending[2].resolve(response(2)); await poll;
  assert.equal(s.draft.text,'Unsaved message');
});

const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const crypto = require('node:crypto').webcrypto;
const source = fs.readFileSync(require('node:path').join(__dirname,'../web/voice_calls.js'),'utf8');
function setup({getUserMedia,upload}={}) {
  const events={}, storage=new Map(), form={elements:{number:{value:'+79991111111'},mode:{value:'now'},when:{value:''}}};
  let stops=0, media, uploads=[];
  class Recorder {
    static isTypeSupported(type){ return type.includes('webm'); }
    constructor(){media=this;this.state='inactive';this.mimeType='audio/webm';}
    start(){this.state='recording';}
    stop(){this.state='inactive';this.ondataavailable({data:new Blob(['x'.repeat(100)])});this.onstop();}
  }
  const window={MediaRecorder:Recorder,addEventListener(){}};
  const root={addEventListener:(name,fn)=>events[name]=fn};
  const context={window,document:{getElementById:id=>id==='voiceCallForm'?form:null}, navigator:{mediaDevices:{getUserMedia:getUserMedia||async function(){return {getTracks:()=>[{stop(){stops++}}]}}}},
    MediaRecorder:Recorder,URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},Blob,FormData,Uint8Array,crypto,Date,Intl,console,
    setInterval:()=>1,clearInterval(){},sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}};
  vm.runInNewContext(source,context);
  const voice=window.SipVoiceCalls;
  voice.bind(root,{escape:s=>s,get:async()=>({jobs:[],connected:true,csrf_token:'csrf'}),post:async()=>{},active:()=>true,refresh(){},upload:async data=>{uploads.push(data);if(upload)return upload(data);return {job:{number:data.get('number'),scheduled_at:'2026-09-20T00:00:00Z'}};}});
  return {voice,events,form,uploads, get stops(){return stops},get media(){return media},
    clickRecord:()=>events.click({target:{closest:selector=>selector==='#voiceRecord'?{}:null}}),
    uploadFile:()=>events.change({target:{id:'voiceFile',files:[new Blob(['test'.repeat(30)])]}}),
    submit:()=>events.submit({target:{id:'voiceCallForm'},preventDefault(){}})};
}
test('microphone starts only on click, preview appears and tracks close after stop',async()=>{
  const app=setup();assert.equal(app.media,undefined);await app.voice.load();
  await app.clickRecord();assert.equal(app.media.state,'recording');
  await app.clickRecord();assert.equal(app.stops,1);assert.match(app.voice.render(),/Прослушать перед отправкой/);
});
test('leaving while permission prompt is pending releases late microphone',async()=>{
  let resolve,stops=0;const app=setup({getUserMedia:()=>new Promise(r=>resolve=r)});
  const pending=app.clickRecord();app.voice.leave();resolve({getTracks:()=>[{stop(){stops++}}]});await pending;
  assert.equal(stops,1);assert.equal(app.media,undefined);
});
test('failed HTTP reply retries the same recording without duplicate request key',async()=>{
  const app=setup({upload:async()=>{throw new Error('lost reply')}});await app.voice.load();app.uploadFile();
  await app.submit();await app.submit();assert.equal(app.uploads.length,2);
  assert.equal(app.uploads[0].get('request_key'),app.uploads[1].get('request_key'));
  assert.equal(app.uploads[0].get('csrf_token'),'csrf');assert.equal(app.uploads[0].get('scheduled_at'),'');
});
test('scheduled call uses explicit Moscow offset, and success clears the audio',async()=>{
  const app=setup();await app.voice.load();app.form.elements.mode.value='scheduled';app.form.elements.when.value='2099-01-01T12:30';app.uploadFile();
  await app.submit();assert.equal(app.uploads[0].get('scheduled_at'),'2099-01-01T12:30:00+03:00');
  assert.doesNotMatch(app.voice.render(),/Прослушать перед отправкой/);
});

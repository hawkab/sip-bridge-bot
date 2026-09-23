const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const crypto = require('node:crypto').webcrypto;
const source = fs.readFileSync(require('node:path').join(__dirname,'../web/transcription_test.js'),'utf8');
const flush=()=>new Promise(r=>setImmediate(r));
function setup({getUserMedia,fetcher,saved=''}={}) {
  const elements=new Map(), windows={}, timers=new Map(), storage=new Map(saved?[['sipTranscriptionTest',saved]]:[]), requests=[];
  let timerId=0, stops=0, media, copied;
  const element=id=>{if(!elements.has(id))elements.set(id,{id,value:'',textContent:'',hidden:true,disabled:false,events:{},classList:{toggle(){},add(){},remove(){}},addEventListener(type,fn){this.events[type]=fn;},pause(){}});return elements.get(id);};
  element('testConfig').textContent=JSON.stringify({backend:'gigaam',csrf:'csrf',maxBytes:1024});
  class Recorder {
    static isTypeSupported(type){return type.includes('webm');}
    constructor(){media=this;this.mimeType='audio/webm';this.state='inactive';}
    start(){this.state='recording';}
    stop(){this.state='inactive';this.ondataavailable({data:new Blob(['voice'.repeat(20)])});this.onstop();}
  }
  const window={MediaRecorder:Recorder,addEventListener:(n,f)=>windows[n]=f};
  const url=URL;url.createObjectURL=()=> 'blob:test';url.revokeObjectURL=()=>{};
  vm.runInNewContext(source,{window,document:{getElementById:element},location:{href:'http://localhost/transcription_test.php'},URL:url,MediaRecorder:Recorder,
    navigator:{mediaDevices:{getUserMedia:getUserMedia||async function(){return {getTracks:()=>[{stop(){stops++;}}]};}},clipboard:{writeText:async text=>{copied=text;}}},
    Blob,FormData,Uint8Array,crypto,Date,console,AbortSignal,
    fetch:async(url,options)=>{requests.push({url,options});return fetcher?fetcher(url,options):{ok:true,json:async()=>({ok:true,job:{id:'a'.repeat(32),backend:'gigaam',status:'completed',text:'Тест.',duration:1,elapsed:2}})};},
    setInterval:()=>1,clearInterval(){},setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),
    sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}});
  return {element,requests,windows,timers,get stops(){return stops;},get media(){return media;},get copied(){return copied;},
    click:id=>element(id).events.click(), file:(file=new Blob(['sample'.repeat(20)]))=>element('audioFile').events.change({target:{files:[file],value:'file'}}),
    drop:files=>element('dropzone').events.drop({preventDefault(){},dataTransfer:{files}})};
}
test('recording starts on demand and stopping releases microphone, enables preview and upload',async()=>{
 const app=setup();assert.equal(app.media,undefined);assert.equal(app.element('transcribe').disabled,true);
 await app.click('record');assert.equal(app.media.state,'recording');assert.equal(app.element('audioFile').disabled,true);
 await app.click('record');assert.equal(app.stops,1);assert.equal(app.element('preview').hidden,false);assert.equal(app.element('transcribe').disabled,false);
 await app.click('transcribe');assert.equal(app.element('resultText').textContent,'Тест.');assert.equal(app.requests[0].options.body.get('backend'),'gigaam');
});
test('late microphone permission after leaving is released',async()=>{
 let resolve,stops=0;const app=setup({getUserMedia:()=>new Promise(r=>resolve=r)});const pending=app.click('record');
 app.windows.pagehide();resolve({getTracks:()=>[{stop(){stops++;}}]});await pending;assert.equal(stops,1);assert.equal(app.media,undefined);
});
test('drag/drop selects one file and rejects oversize or multiple files',()=>{
 const app=setup();app.drop([new Blob(['x'.repeat(200)])]);assert.equal(app.element('transcribe').disabled,false);
 app.drop([new Blob(['x'.repeat(2000)])]);assert.match(app.element('error').textContent,/размер/);
 app.drop([new Blob(['1']),new Blob(['2'])]);assert.match(app.element('error').textContent,/один/);
});
test('uncertain upload retries the same id, changing engine gets a new id',async()=>{
 const app=setup({fetcher:async()=>{throw new Error('network');}});app.file();
 await app.click('transcribe');await app.click('transcribe');
 assert.equal(app.requests[0].options.body.get('id'),app.requests[1].options.body.get('id'));
 app.element('engine').value='whisper';app.element('engine').events.change();await app.click('transcribe');
 assert.notEqual(app.requests[1].options.body.get('id'),app.requests[2].options.body.get('id'));
 assert.equal(app.requests[2].options.body.get('backend'),'whisper');
});
test('refresh restores result without uploading again and treats transcript as plain text',async()=>{
 const app=setup({saved:'a'.repeat(32),fetcher:async()=>({ok:true,json:async()=>({ok:true,job:{status:'completed',text:'<img onerror=alert(1)>',backend:'whisper',duration:2,elapsed:1}})})});
 await flush();assert.equal(app.requests.length,1);assert.equal(app.requests[0].options.method,undefined);assert.equal(app.element('resultText').textContent,'<img onerror=alert(1)>');
});
test('timestamped phrases retain line breaks in displayed, restored and copied results',async()=>{
 const text='[00:00:01.250] Первая реплика.\n[01:02:03.450] Вторая реплика.';
 for(const saved of ['', 'b'.repeat(32)]){
  const app=setup({saved,fetcher:async()=>({ok:true,json:async()=>({ok:true,job:{status:'completed',text,backend:'gigaam'}})})});
  if(saved) await flush();else {app.file();await app.click('transcribe');}
  assert.equal(app.element('resultText').textContent,text);
  await app.click('copy');assert.equal(app.copied,text);
 }
});
test('silence is distinct from speech that could not be decoded',async()=>{
 for(const detected of [false,true]){
  const app=setup({fetcher:async()=>({ok:true,json:async()=>({ok:true,job:{status:'completed',text:'',speech_detected:detected}})})});
  app.file();await app.click('transcribe');assert.match(app.element('resultText').textContent,detected?/текст не распознан/:/Речь не обнаружена/);assert.equal(app.element('copy').disabled,true);
 }
});

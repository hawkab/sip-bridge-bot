<?php
declare(strict_types=1);
define('DATA_STORAGE_DIR', sys_get_temp_dir().'/asr-test-'.bin2hex(random_bytes(8)));
function ensure_storage_layout(): void { if (!is_dir(DATA_STORAGE_DIR)) mkdir(DATA_STORAGE_DIR,0700); }
require __DIR__.'/../web/transcription_test_store.php';
function check(bool $condition, string $message): void { if (!$condition) throw new RuntimeException($message); }
function invalid(callable $fn): void { try {$fn();} catch (InvalidArgumentException $e) {return;} throw new RuntimeException('Expected validation error'); }
try {
    $audio = str_repeat('test',100); $id = str_repeat('a',32);
    $data = ['id'=>$id,'backend'=>'gigaam'];
    $job = asr_test_enqueue($data,$audio)['job'];
    check($job['status']==='queued','Enqueued');
    check(asr_test_enqueue($data,$audio)['job']===$job,'Idempotent upload');
    invalid(fn()=>asr_test_enqueue($data,$audio.'changed'));
    invalid(fn()=>asr_test_enqueue(array_replace($data,['backend'=>'whisper']),$audio));
    invalid(fn()=>asr_test_enqueue(array_replace($data,['id'=>'../bad']),$audio));
    invalid(fn()=>asr_test_enqueue(array_replace($data,['backend'=>'bad']),$audio));
    invalid(fn()=>asr_test_enqueue($data,str_repeat('x',ASR_TEST_MAX_BYTES+1)));
    $claim = asr_test_worker(['action'=>'claim'])['job'];
    check($claim['id']===$id,'Claim');
    check(asr_test_worker(['action'=>'claim'])['job']===null,'Only one in flight');
    check(!isset(asr_test_get($id)['job']['claim_token']),'Private claim token');
    $result = ['action'=>'complete','id'=>$id,'claim_token'=>$claim['claim_token'],'status'=>'completed','text'=>'Проверка речи.','speech_detected'=>true,'duration'=>3.1,'elapsed'=>2.4];
    invalid(fn()=>asr_test_worker(array_replace($result,['claim_token'=>'bad'])));
    check(is_file(DATA_STORAGE_DIR.'/transcription-tests/'.$id.'.audio'),'Audio kept until completion');
    check(asr_test_worker($result)['job']['text']==='Проверка речи.','Text saved');
    check(asr_test_worker($result)['job']['text']==='Проверка речи.','Idempotent completion');
    check(!is_file(DATA_STORAGE_DIR.'/transcription-tests/'.$id.'.audio'),'Audio released');
    check(asr_test_enqueue($data,$audio)['job']['status']==='completed','Uncertain upload retry does not repeat ASR');
    $path = DATA_STORAGE_DIR.'/transcription-tests/jobs.json';
    $store = json_decode(file_get_contents($path),true); $store['jobs'][$id]['created_at']=time()-86401; file_put_contents($path,json_encode($store));
    asr_test_worker(['action'=>'claim']);
    invalid(fn()=>asr_test_get($id));
    for($i=0;$i<5;$i++) asr_test_enqueue(['id'=>str_repeat((string)$i,32),'backend'=>'whisper'],$audio);
    invalid(fn()=>asr_test_enqueue(['id'=>str_repeat('f',32),'backend'=>'whisper'],$audio));
    $store = json_decode(file_get_contents($path),true);
    foreach($store['jobs'] as &$j) $j['created_at']=time()-3601;
    unset($j); file_put_contents($path,json_encode($store));
    check(asr_test_worker(['action'=>'claim'])['job']===null,'Expired work never runs');
    check(count(glob(DATA_STORAGE_DIR.'/transcription-tests/*.audio'))===0,'Expired audio removed');
    echo "PASS: upload retry, claims, limits, results, expiry and audio cleanup\n";
} finally {
    foreach(glob(DATA_STORAGE_DIR.'/transcription-tests/*') ?: [] as $file) unlink($file);
    if(is_dir(DATA_STORAGE_DIR.'/transcription-tests')) rmdir(DATA_STORAGE_DIR.'/transcription-tests');
    if(is_dir(DATA_STORAGE_DIR)) rmdir(DATA_STORAGE_DIR);
}

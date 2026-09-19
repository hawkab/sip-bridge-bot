<?php
declare(strict_types=1);
$root = $argv[2] ?? sys_get_temp_dir().'/sip-voice-test-'.bin2hex(random_bytes(6));
define('DATA_STORAGE_DIR', $root);
function ensure_storage_layout(): void {}
require __DIR__.'/../web/voice_outbox_store.php';
if (($argv[1] ?? '') === 'claim') { echo json_encode(voice_worker_action(['action'=>'claim'])); exit; }
function check(bool $ok, string $message): void { if (!$ok) throw new RuntimeException($message); }
function invalid(callable $fn): void { try { $fn(); } catch (InvalidArgumentException $e) { return; } throw new RuntimeException('Expected validation error'); }
mkdir($root,0700);
try {
    $audio = str_repeat('sample', 100);
    $request = ['request_key'=>'voice-request-0001','number'=>'+79991111111','scheduled_at'=>''];
    $job = voice_enqueue($request, 'web', $audio);
    check(voice_enqueue($request, 'web', $audio)['id'] === $job['id'], 'Idempotent audio upload');
    invalid(fn()=>voice_enqueue($request, 'web', $audio.'different'));
    invalid(fn()=>voice_enqueue(array_replace($request,['number'=>"+79991111111\nApplication: System"]), 'web', $audio));
    invalid(fn()=>voice_enqueue(array_replace($request,['scheduled_at'=>'2026-02-30T12:00:00Z']), 'web', $audio));
    invalid(fn()=>voice_enqueue(array_replace($request,['scheduled_at'=>'2020-01-01T12:00:00Z']), 'web', $audio));
    invalid(fn()=>voice_enqueue($request, 'web', str_repeat('x', VOICE_MAX_BYTES+1)));
    $future = voice_enqueue(array_replace($request,['request_key'=>'voice-request-0002','scheduled_at'=>gmdate('Y-m-d\TH:i:s\Z',time()+3600)]), 'telegram', $audio);
    $processes=[];
    for($i=0;$i<3;$i++) { $process=proc_open([PHP_BINARY,__FILE__,'claim',$root],[1=>['pipe','w'],2=>STDERR],$pipes); $processes[]=[$process,$pipes[1]]; }
    $claims=[];
    foreach($processes as [$process,$output]) { $reply=json_decode(stream_get_contents($output),true); fclose($output); check(proc_close($process)===0,'Worker exits'); if($reply['job'])$claims[]=$reply['job']; }
    check(count($claims)===1 && $claims[0]['id']===$job['id'], 'Exactly one worker claims due call');
    invalid(fn()=>voice_cancel($job['id']));
    check(voice_cancel($future['id'])['job']['status']==='cancelled','Cancel future call');
    check(!is_file($root.'/voice-audio/'.$future['id'].'.audio'), 'Cancelled transport audio released after commit');
    check(voice_cancel($future['id'])['job']['status']==='cancelled','Cancel idempotently');
    $done=['action'=>'complete','id'=>$job['id'],'claim_token'=>$claims[0]['claim_token'],'status'=>'completed','message'=>'Done'];
    invalid(fn()=>voice_worker_action(array_replace($done,['claim_token'=>'bad'])));
    check(voice_worker_action($done)['job']['status']==='completed','Completion');
    check(voice_worker_action($done)['job']['status']==='completed','Completion retry');
    check(voice_worker_action(['action'=>'claim'])['job']===null,'Never redial');
    $next=voice_enqueue(array_replace($request,['request_key'=>'voice-request-0003']), 'web', $audio);
    $path=$root.'/voice-outbox.json';$store=json_decode(file_get_contents($path),true);
    $store['jobs'][2]['scheduled_unix']=time()-601;file_put_contents($path,json_encode($store));
    check(voice_state()['jobs'][0]['status']==='missed','Expired schedule not dialed late');
    check(!isset(voice_state()['jobs'][2]['claim_token']),'No claim secret in public state');
    check(voice_worker_action(['action'=>'claim'])['job']===null,'Missed call not dialed');
    echo "PASS: schedule, cancellation, duplicate uploads, concurrency, claim tokens, missed time\n";
} finally {
    foreach(glob($root.'/voice-audio/*') as $file) unlink($file);
    if(is_dir($root.'/voice-audio'))rmdir($root.'/voice-audio');
    foreach(glob($root.'/*') as $file)unlink($file);
    rmdir($root);
}

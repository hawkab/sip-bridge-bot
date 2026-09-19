<?php
declare(strict_types=1);
$root = $argv[2] ?? sys_get_temp_dir().'/sip-outbox-test-'.bin2hex(random_bytes(6));
define('DATA_STORAGE_DIR',$root);
function ensure_storage_layout(): void {}
require __DIR__.'/../web/sms_outbox_store.php';
if (($argv[1] ?? '') === 'claim') { echo json_encode(sms_outbox_claim()); exit; }
function check(bool $ok, string $message): void { if (!$ok) throw new RuntimeException($message); }
function invalid(callable $fn): void { try { $fn(); } catch (InvalidArgumentException $e) {return;} throw new RuntimeException('Expected validation error'); }
mkdir($root,0700);
try {
    sms_outbox_heartbeat(['ports'=>[['port'=>1,'number'=>'+79991111111'],['port'=>2,'number'=>'+79992222222']], 'connected'=>true]);
    $request=['request_key'=>'test-request-0001','sender'=>'1','number'=>'+79992222222','text'=>'Привет + "мир"!'];
    $job=sms_outbox_enqueue($request,'web');
    check(sms_outbox_enqueue($request,'web')['id']===$job['id'],'Idempotent enqueue');
    invalid(fn()=>sms_outbox_enqueue(array_replace($request,['text'=>'changed']),'web'));
    invalid(fn()=>sms_outbox_enqueue(array_replace($request,['sender'=>'3']),'web'));
    invalid(fn()=>sms_outbox_enqueue(array_replace($request,['number'=>"+7999\r\nAction: Login"]),'web'));
    invalid(fn()=>sms_outbox_enqueue(array_replace($request,['text'=>str_repeat('я',513)]),'web'));
    $claimed=sms_outbox_claim()['job'];
    check($claimed['id']===$job['id'] && strlen($claimed['claim_token'])===48,'Claim returns token');
    check(sms_outbox_claim()['job']===null,'Claimed SMS never sent twice');
    $public=sms_outbox_state()['jobs'][0];
    check(!isset($public['claim_token'])&&!isset($public['request_key']),'No internal tokens in UI');
    $result=['id'=>$claimed['id'],'claim_token'=>$claimed['claim_token'],'status'=>'sent','message'=>'OK','gateway_id'=>'123'];
    invalid(fn()=>sms_outbox_complete(array_replace($result,['claim_token'=>'bad'])));
    check(sms_outbox_complete($result)['job']['status']==='sent','Completed');
    check(sms_outbox_complete($result)['job']['status']==='sent','Result persistence retry is safe');
    sms_outbox_enqueue(array_replace($request,['request_key'=>'test-request-0002','sender'=>'+79991111111']),'email');
    $processes=[];
    for ($i=0;$i<3;$i++) {
        $process=proc_open([PHP_BINARY,__FILE__,'claim',$root],[1=>['pipe','w'],2=>STDERR],$pipes);
        $processes[]=[$process,$pipes[1]];
    }
    $claims=0;
    foreach($processes as [$process,$output]) {
        $reply=json_decode(stream_get_contents($output),true);fclose($output);
        check(proc_close($process)===0,'Claim worker succeeds');
        if ($reply['job']) $claims++;
    }
    check($claims===1,'Concurrent workers must claim one message exactly once');
    $path=$root.'/sms-outbox.json';$store=json_decode(file_get_contents($path),true);
    $store['jobs'][1]['claimed_at']=time()-181;file_put_contents($path,json_encode($store));
    check(sms_outbox_state()['jobs'][0]['status']==='unknown','Stale claim becomes unknown');
    check(sms_outbox_claim()['job']===null,'Stale claim is not resent');
    file_put_contents($path,'{broken');
    try { sms_outbox_state(); throw new Exception('Expected corruption failure'); } catch(RuntimeException $e) {}
    check(file_get_contents($path)==='{broken','Corruption not overwritten');
    echo "PASS: SMS outbox idempotency, auth tokens, validation, stale claims and corruption\n";
} finally {foreach(glob($root.'/*') as $file)unlink($file);rmdir($root);}

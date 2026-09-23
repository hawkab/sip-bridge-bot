<?php
declare(strict_types=1);
define('DATA_STORAGE_DIR', sys_get_temp_dir().'/sip-chat-list-'.bin2hex(random_bytes(6)));
define('SMS_JSON_PATH', DATA_STORAGE_DIR.'/sms.json');
define('CALLS_JSON_PATH', DATA_STORAGE_DIR.'/calls.json');
function ensure_storage_layout(): void {}
function read_json_array(string $path): array { return json_decode(file_get_contents($path), true); }
function find_record_by_id(string $path, string $id): ?array {
    foreach (read_json_array($path) as $row) if ($row['id'] === $id) return $row;
    return null;
}
function check(bool $ok, string $why): void { if (!$ok) throw new RuntimeException($why); }
function invalid(callable $fn): void {
    try { $fn(); } catch (InvalidArgumentException $e) { return; }
    throw new RuntimeException('Expected invalid operation');
}
require __DIR__.'/../web/event_store.php';
require __DIR__.'/../web/sms_chat.php';
mkdir(DATA_STORAGE_DIR, 0700);
try {
    $ports=[['port'=>1,'number'=>'+79990000001'],['port'=>2,'number'=>'+79990000002']];
    sms_outbox_heartbeat(['connected'=>true,'ports'=>$ports]);
    $base=['number'=>'+79991111111','local_number'=>'+79990000001','sim_port'=>1,'timestamp'=>'2026-09-23 10:00:00'];
    $records=[array_replace($base,['id'=>'first','number'=>'8 (999) 111-11-11','text'=>'Старое слово Светлана']),
        array_replace($base,['id'=>'second','text'=>'Второе сообщение','timestamp'=>'2026-09-23 10:00:01']),
        array_replace($base,['id'=>'other-sim','text'=>'Другая SIM','local_number'=>'+79990000002','sim_port'=>2]),
        ['id'=>'unknown-one','number'=>'+79991111111','text'=>'Без SIM 1','timestamp'=>'2026-09-23 09:00:00'],
        ['id'=>'unknown-two','number'=>'+79991111111','text'=>'Без SIM 2','timestamp'=>'2026-09-23 09:00:01']];
    file_put_contents(SMS_JSON_PATH,json_encode($records));
    $request=['sender'=>'1','number'=>'+79991111111','text'=>'Последний ответ','request_key'=>'chat-list-test-001'];
    $sent=sms_outbox_enqueue($request,'web');
    sms_outbox_enqueue(array_replace($request,['sender'=>'2','text'=>'Ответ SIM2','request_key'=>'chat-list-test-002']),'web');
    sms_outbox_transaction(static function(array &$store): array {
        foreach($store['jobs'] as &$j) {$j['status']='sent';$j['created_at']='2026-09-23T10:00:02+03:00';}
        return [];
    });
    $pending=sms_outbox_enqueue(array_replace($request,['number'=>'+79992222222','text'=>'Только исходящее','request_key'=>'chat-list-test-003']),'web');
    $one=sms_chat_id($base);
    $two=sms_chat_id(array_replace($base,['local_number'=>'+79990000002','sim_port'=>2]));
    $unknown=sms_chat_id($records[3]);
    $outOnly=sms_chat_id(sms_chat_outgoing_item($pending));
    $chats=array_column(sms_chat_summaries(),null,'id');
    check(count($chats)===4,'One row per SIM/counterparty pair, unknown separate, outgoing-only included');
    check($chats[$one]['message_count']===3 && $chats[$two]['message_count']===2 && $chats[$unknown]['message_count']===2,'Count both directions without mixing SIMs');
    check($chats[$one]['text']==='Последний ответ' && $chats[$one]['direction']==='outgoing','Latest message preview across both directions');
    check($chats[$one]['timestamp']==='2026-09-23T10:00:02+03:00','Last message timestamp');
    check(array_column(sms_chat_summaries('светлана'),'id')===[$one],'Search old incoming text returns full chat');
    check(array_column(sms_chat_summaries('только исходящее'),'id')===[$outOnly],'Search outgoing text');
    check(sms_chat_summaries('йцйцйцйц')===[],'Missing query returns no chats');
    check(count(sms_chat(['id'=>$one])['messages'])===3,'Stable chat opens complete history');
    check(count(sms_chat(['id'=>'first'])['messages'])===3,'Old notification link opens same history');
    check(count(sms_chat(['id'=>$unknown])['messages'])===2 && sms_chat(['id'=>$unknown])['sender_selectable'],'Unidentified SIM history stays separate and reply sender selectable');
    check(sms_chat(['id'=>$outOnly])['can_reply'],'Outgoing-only chat supports reply');
    check(sms_reply_payload($request+['reply_to'=>$one])['sender']==='1','Reply stays on chat SIM');
    invalid(fn()=>sms_reply_payload(array_replace($request,['reply_to'=>$one,'sender'=>'2'])));
    $records[]=array_replace($base,['id'=>'newest','text'=>'Новое входящее','timestamp'=>'2026-09-23 10:00:03']);
    file_put_contents(SMS_JSON_PATH,json_encode($records));
    check(sms_chat_record($one)['message_count']===4 && sms_chat_record($one)['text']==='Новое входящее','Chat ID survives newest-message changes');
    sms_outbox_heartbeat(['connected'=>true,'ports'=>[['port'=>2,'number'=>'+79990000001'],['port'=>1,'number'=>'+79990000002']]]);
    check(sms_chat_record($one)['sim_port']===2 && sms_chat(['id'=>$one])['sim_port']===2,'SIM identity follows number when ports change');
    sms_outbox_heartbeat(['connected'=>true,'ports'=>[['port'=>1,'number'=>'+79990000002']]]);
    $disconnected=sms_chat(['id'=>$one,'sender'=>'1']);
    check(!$disconnected['can_reply'] && !$disconnected['sender_selectable'] && count($disconnected['messages'])===4,'Missing SIM does not merge another SIM history');
    sms_outbox_heartbeat(['connected'=>true,'ports'=>$ports]);
    $before=file_get_contents(SMS_JSON_PATH);
    invalid(fn()=>delete_sms_chats([$one,$outOnly]));
    check(file_get_contents(SMS_JSON_PATH)===$before && count(sms_chat_summaries())===4,'Pending send blocks deletion before any changes');
    check(delete_sms_chats([$one])['deleted_ids']===[$one],'Delete complete selected chat');
    check(delete_sms_chats([$one])['deleted_ids']===[],'Deletion retry is idempotent');
    check(sms_chat_record($one)===null && count(sms_chat(['id'=>$two])['messages'])===2,'Other SIM remains intact');
    check(count(read_json_array(SMS_JSON_PATH))===3,'Incoming messages removed only for selected pair');
    check(sms_outbox_enqueue($request,'web')['id']===$sent['id'],'Deleted outgoing receipt still prevents duplicate send');
    check(sms_chat_record($one)===null,'Retry of sent request does not resurrect chat');
    append_event_record(SMS_JSON_PATH,array_replace($base,['id'=>'after-delete','text'=>'После удаления']));
    check(sms_chat_record($one)['message_count']===1,'New SMS reopens chat without resurrecting old outgoing history');
    invalid(fn()=>delete_sms_chats(['../sms.json']));
    echo "PASS: grouped chats, SIM isolation, outgoing-only, old links, search, stable IDs, reply validation and deletion\n";
} finally {
    foreach(glob(DATA_STORAGE_DIR.'/*') as $file) unlink($file);
    rmdir(DATA_STORAGE_DIR);
}

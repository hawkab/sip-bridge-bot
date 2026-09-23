<?php
declare(strict_types=1);
$root = sys_get_temp_dir() . '/sip-chat-' . bin2hex(random_bytes(6));
define('DATA_STORAGE_DIR', $root);
define('SMS_JSON_PATH', $root . '/sms.json');
function ensure_storage_layout(): void {}
function read_json_array(string $path): array { return json_decode(file_get_contents($path), true); }
function find_record_by_id(string $path, string $id): ?array {
    foreach (read_json_array($path) as $row) if ($row['id'] === $id) return $row;
    return null;
}
function check(bool $ok, string $why): void { if (!$ok) throw new RuntimeException($why); }
function invalid(callable $fn): void {
    try { $fn(); } catch (InvalidArgumentException $e) { return; }
    throw new RuntimeException('Expected invalid reply');
}
require __DIR__ . '/../web/sms_chat.php';
mkdir($root, 0700);
try {
    sms_outbox_heartbeat(['connected'=>true, 'ports'=>[
        ['port'=>1,'number'=>'+79990000001'], ['port'=>2,'number'=>'+79990000002']]]);
    file_put_contents(SMS_JSON_PATH, json_encode([
        ['id'=>'one','number'=>'89991111111','local_number'=>'+79990000001','sim_port'=>1,'text'=>'First SIM','timestamp'=>'2026-09-20 09:34:46'],
        ['id'=>'two','number'=>'+79991111111','local_number'=>'+79990000002','sim_port'=>2,'text'=>'Second SIM','timestamp'=>'2026-09-20 10:00:00'],
        ['id'=>'legacy','number'=>'+79991111111','text'=>'Unknown SIM','timestamp'=>'2026-09-20 10:00:00'],
        ['id'=>'alpha','number'=>'Bank','local_number'=>'+79990000001','text'=>'Service','timestamp'=>'2026-09-20 10:00:00']
    ]));
    $request = ['sender'=>'1','number'=>'+79991111111','text'=>'Answer','request_key'=>'chat-test-request-001'];
    $job = sms_outbox_enqueue($request,'web');
    sms_outbox_transaction(static function(array &$store) use ($job): array {
        foreach ($store['jobs'] as &$entry) {
            if ($entry['id'] === $job['id']) $entry['created_at'] = '2026-09-20T09:34:47+03:00';
        }
        return [];
    });
    $chat = sms_chat(['id'=>'one']);
    check(array_column($chat['messages'], 'display_timestamp') === ['09:34:46 20.09.2026','09:34:47 20.09.2026'], 'Display naive incoming and ISO outgoing dates consistently, in order');
    check($chat['messages'][1]['timestamp'] === '2026-09-20T09:34:47+03:00', 'Preserve raw timestamp');
    check(!isset($chat['messages'][0]['_sort_timestamp']), 'Do not expose internal sort key');
    check($chat['can_reply'] && $chat['sim_port'] === 1, 'Select receiving SIM');
    check(count($chat['messages']) === 2 && in_array('First SIM', array_column($chat['messages'], 'text'), true), 'Incoming and outgoing together; isolate SIMs');
    check(count(sms_chat(['id'=>'two'])['messages']) === 1, 'Second SIM has independent history');
    check(count(sms_chat(['sender'=>'1','number'=>'+79991111111'])['messages']) === 2, 'New compose opens the same conversation');
    check(!sms_chat(['id'=>'legacy'])['can_reply'] && sms_chat(['id'=>'legacy'])['sender_selectable'] && count(sms_chat(['id'=>'legacy'])['messages']) === 1, 'Require explicit choice for legacy SIM');
    check(!sms_chat(['id'=>'one'])['sender_selectable'], 'Known receiving SIM stays locked');
    $legacy = sms_chat(['id'=>'legacy','sender'=>'2']);
    check($legacy['can_reply'] && $legacy['sim_port'] === 2 && $legacy['sender_selectable'], 'Select reply SIM manually');
    check(!isset(find_record_by_id(SMS_JSON_PATH,'legacy')['local_number']), 'Manual reply does not rewrite receiving SIM');
    check(!sms_chat(['id'=>'alpha'])['can_reply'], 'Alphanumeric sender cannot receive reply');
    check(sms_reply_payload($request + ['reply_to'=>'one'])['number'] === '+79991111111', 'Canonical reply');
    invalid(fn()=>sms_reply_payload(array_replace($request,['sender'=>'2','reply_to'=>'one'])));
    invalid(fn()=>sms_reply_payload(array_replace($request,['number'=>'+79992222222','reply_to'=>'one'])));
    check(sms_reply_payload($request + ['reply_to'=>'legacy'])['sender'] === '1', 'Reply from manually selected SIM');
    invalid(fn()=>sms_reply_payload(array_replace($request,['sender'=>'','reply_to'=>'legacy'])));
    invalid(fn()=>sms_reply_payload(array_replace($request,['sender'=>'9','reply_to'=>'legacy'])));
    invalid(fn()=>sms_reply_payload(array_replace($request,['number'=>'+79992222222','reply_to'=>'legacy'])));
    sms_outbox_heartbeat(['connected'=>true,'ports'=>[['port'=>1,'number'=>'+79990000009'],['port'=>2,'number'=>'+79990000001']]]);
    check(sms_chat(['id'=>'one'])['sim_port'] === 2, 'Follow number after SIM moves ports');
    invalid(fn()=>sms_reply_payload($request + ['reply_to'=>'one']));
    check(!sms_chat(['id'=>'two','sender'=>'1'])['can_reply'] && !sms_chat(['id'=>'two'])['sender_selectable'], 'Disconnected known SIM cannot be replaced inside another SIM chat');
    invalid(fn()=>sms_reply_payload(array_replace($request,['reply_to'=>'two'])));
    $records = read_json_array(SMS_JSON_PATH);
    foreach (['utc'=>'2026-09-19T22:01:02Z', 'bad'=>'invalid timestamp', 'empty'=>''] as $id=>$timestamp) {
        $records[] = array_replace($records[0], ['id'=>$id, 'timestamp'=>$timestamp]);
    }
    file_put_contents(SMS_JSON_PATH, json_encode($records));
    $messages = array_column(sms_chat(['id'=>'one'])['messages'], null, 'id');
    check($messages['in-utc']['display_timestamp'] === '01:01:02 20.09.2026', 'Convert explicit timezone to Moscow, including day boundary');
    check($messages['in-bad']['display_timestamp'] === '—' && $messages['in-empty']['display_timestamp'] === '—', 'Malformed historical dates do not break chat or become current time');
    echo "PASS: two-SIM chats, replies, number normalization, legacy records, changed SIM, route tampering and timestamp formatting\n";
} finally {
    foreach (glob($root . '/*') as $file) unlink($file);
    rmdir($root);
}

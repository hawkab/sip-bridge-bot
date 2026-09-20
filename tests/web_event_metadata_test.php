<?php
declare(strict_types=1);
$root = sys_get_temp_dir() . '/sip-metadata-' . bin2hex(random_bytes(6));
define('CALLS_JSON_PATH', $root . '/calls.json');
define('SMS_JSON_PATH', $root . '/sms.json');
function ensure_storage_layout(): void {}
function check(bool $ok, string $why): void { if (!$ok) throw new RuntimeException($why); }
require __DIR__ . '/../web/event_metadata_store.php';
mkdir($root,0700);
try {
    file_put_contents(CALLS_JSON_PATH,json_encode([['id'=>'a','transcription'=>[['text'=>'Keep']]],['id'=>'b']]));
    file_put_contents(SMS_JSON_PATH,'[]');
    $snapshot = event_metadata_snapshot('calls');
    $update = ['id'=>'a','version'=>$snapshot[0]['version'],'metadata'=>['local_number'=>'+79990000001','sim_port'=>1]];
    $result = update_event_metadata('calls',[$update]);
    check($result['updated_ids'] === ['a'] && is_file($root.'/'.$result['backup']), 'Back up before updating identity');
    $after = event_metadata_snapshot('calls');
    check($after[0]['record']['transcription'][0]['text'] === 'Keep' && $after[1] === $snapshot[1], 'Only specified metadata changes');
    check(update_event_metadata('calls',[$update])['conflicts']['a'] === 'record_changed', 'Reject stale overwrite');
    delete_event_records('calls',['b']);
    check(update_event_metadata('calls',[['id'=>'b','version'=>$snapshot[1]['version'],'metadata'=>['sim_port'=>1]]])['conflicts']['b'] === 'record_missing', 'Never recreate deleted record');
    try { update_event_metadata('calls',[array_replace($update,['metadata'=>['transcription'=>[]]])]); throw new RuntimeException('Expected rejection'); }
    catch (InvalidArgumentException $expected) {}
    echo "PASS: metadata backup, compare-and-swap, preservation of transcripts and deleted records\n";
} finally {
    foreach (glob($root.'/*') as $file) unlink($file);
    rmdir($root);
}

<?php
declare(strict_types=1);
$root = sys_get_temp_dir() . '/voice-store-' . bin2hex(random_bytes(6));
define('CALLS_JSON_PATH', $root . '/calls.json');
define('SMS_JSON_PATH', $root . '/sms.json');
define('RECORDINGS_DIR', $root . '/recordings');
function ensure_storage_layout(): void {}
function check(bool $ok, string $why): void { if (!$ok) throw new RuntimeException($why); }
require __DIR__ . '/../web/event_store.php';
mkdir(RECORDINGS_DIR, 0700, true);
file_put_contents(CALLS_JSON_PATH, '[]');
try {
    $base = ['id'=>'first','source_id'=>'voice:'.str_repeat('a',32),'created_at'=>'original',
             'number'=>'+79990000002','recording_file'=>'first.wav','transcription'=>[]];
    file_put_contents(RECORDINGS_DIR.'/first.wav', 'first');
    $first = save_call_event_record($base);
    check($first['created'], 'First upload creates the card');
    file_put_contents(RECORDINGS_DIR.'/retry.wav','retry');
    $retry = save_call_event_record(array_replace($base, ['id'=>'unused','created_at'=>'later',
        'recording_file'=>'retry.wav','transcription'=>[['text'=>'Сообщение получил.']]]));
    check(!$retry['created'] && $retry['record']['id']==='first', 'Retry keeps URL and does not push again');
    check($retry['record']['created_at']==='original', 'Keep original creation time');
    $items=json_decode(file_get_contents(CALLS_JSON_PATH),true);
    check(count($items)===1 && count($items[0]['transcription'])===1, 'ASR retry updates the existing card');
    check(!file_exists(RECORDINGS_DIR.'/first.wav') && file_exists(RECORDINGS_DIR.'/retry.wav'), 'Remove only replaced recording');
    $normal=['id'=>'normal1','created_at'=>'now','recording_file'=>'shared.wav'];
    file_put_contents(RECORDINGS_DIR.'/shared.wav','keep');
    save_call_event_record($normal);
    save_call_event_record(array_replace($normal,['id'=>'normal2']));
    check(count(json_decode(file_get_contents(CALLS_JSON_PATH),true))===3, 'Ordinary calls keep append behavior');
    save_call_event_record(array_replace($base,['recording_file'=>'shared.wav']));
    save_call_event_record(array_replace($base,['recording_file'=>'retry.wav']));
    check(file_exists(RECORDINGS_DIR.'/shared.wav'), 'Do not delete audio referenced by another call');
    file_put_contents(CALLS_JSON_PATH,'broken');
    try { save_call_event_record($base); throw new LogicException('Expected error'); }
    catch (RuntimeException $expected) {}
    check(file_get_contents(CALLS_JSON_PATH)==='broken','Do not replace corrupt history');
    echo "PASS: voice upload retry, stable URL, transcript update, audio cleanup, ordinary calls, corrupt storage\n";
} finally {
    foreach (glob(RECORDINGS_DIR.'/*') as $path) unlink($path);
    rmdir(RECORDINGS_DIR);
    foreach (glob($root.'/*') as $path) unlink($path);
    rmdir($root);
}

<?php
declare(strict_types=1);

// Standalone: php tests/web_event_store_test.php (no production common.php).
$worker = $argv[1] ?? '';
$root = $worker ? $argv[2] : sys_get_temp_dir() . '/sip-event-test-' . bin2hex(random_bytes(6));
define('CALLS_JSON_PATH', $root . '/calls.json');
define('SMS_JSON_PATH', $root . '/sms.json');
define('RECORDINGS_DIR', $root . '/recordings');
function ensure_storage_layout(): void {}
require __DIR__ . '/../web/event_store.php';
if ($worker === 'append') {
    for ($i = 0; $i < 40; $i++) append_event_record(CALLS_JSON_PATH, ['id' => $argv[3] . '-' . $i]);
    exit;
}
if ($worker === 'delete') {
    for ($i = 0; $i < 40; $i++) delete_event_records('calls', ['old-' . $i]);
    exit;
}
function check(bool $ok, string $message): void {
    if (!$ok) throw new RuntimeException($message);
}
function rejected(callable $operation, string $exception): void {
    try { $operation(); } catch (Throwable $error) {
        check($error instanceof $exception, 'Unexpected exception: ' . get_class($error));
        return;
    }
    throw new RuntimeException('Expected rejection');
}
mkdir(RECORDINGS_DIR, 0700, true);
try {
    file_put_contents(SMS_JSON_PATH, '[{"id":"sms1"},{"id":"sms2"}]');
    file_put_contents(CALLS_JSON_PATH, json_encode([
        ['id'=>'a', 'recording_file'=>'shared.wav'], ['id'=>'b', 'recording_file'=>'shared.wav'],
        ['id'=>'c', 'recording_file'=>'unique.wav'], ['id'=>'bad', 'recording_file'=>'../outside.wav']
    ]));
    foreach (['shared.wav', 'unique.wav'] as $file) file_put_contents(RECORDINGS_DIR . '/' . $file, 'test');
    file_put_contents($root . '/outside.wav', 'keep');
    $result = delete_event_records('calls', ['a','a','missing','c','bad']);
    check($result['deleted_ids'] === ['a','c','bad'], 'Delete selected IDs only, deduplicate');
    check(is_file(RECORDINGS_DIR . '/shared.wav'), 'Retain shared recording');
    check(!is_file(RECORDINGS_DIR . '/unique.wav'), 'Delete unreferenced recording');
    check(is_file($root . '/outside.wav'), 'Do not traverse directories');
    check(count(json_decode(file_get_contents(SMS_JSON_PATH), true)) === 2, 'Calls cannot delete SMS');
    check(delete_event_records('calls', ['a'])['deleted_ids'] === [], 'Retry is idempotent');
    delete_event_records('calls', ['b']);
    check(!is_file(RECORDINGS_DIR . '/shared.wav'), 'Last reference deletes shared recording');
    delete_event_records('sms', ['sms1']);
    check(json_decode(file_get_contents(SMS_JSON_PATH), true) === [['id'=>'sms2']], 'Retain unselected SMS');
    foreach ([[], [123], ['']] as $ids) {
        rejected(fn()=>delete_event_records('calls', $ids), InvalidArgumentException::class);
    }
    file_put_contents(SMS_JSON_PATH, json_encode(array_map(fn($i)=>['id'=>'many-'.$i],range(1,205))));
    check(count(delete_event_records('sms', array_map(fn($i)=>'many-'.$i,range(1,205)))['deleted_ids']) === 205, 'Select all can delete more than one page or 100 records');
    rejected(fn()=>delete_event_records('errors', ['a']), InvalidArgumentException::class);
    file_put_contents(CALLS_JSON_PATH, '{broken');
    rejected(fn()=>delete_event_records('calls', ['a']), RuntimeException::class);
    rejected(fn()=>append_event_record(CALLS_JSON_PATH, ['id'=>'new']), RuntimeException::class);
    check(file_get_contents(CALLS_JSON_PATH) === '{broken', 'Corrupt data must not be overwritten');

    file_put_contents(CALLS_JSON_PATH, json_encode(array_map(fn($i)=>['id'=>'old-'.$i], range(0,39))));
    $processes = [];
    foreach ([['append','writer1'], ['append','writer2'], ['delete','']] as [$mode, $prefix]) {
        $processes[] = proc_open([PHP_BINARY, __FILE__, $mode, $root, $prefix], [], $pipes);
    }
    foreach ($processes as $process) check(proc_close($process) === 0, 'Concurrent worker succeeds');
    $records = json_decode(file_get_contents(CALLS_JSON_PATH), true);
    check(count($records) === 80, 'Concurrent append/delete must not lose new events');
    $ids = array_column($records,'id');
    foreach (['writer1','writer2'] as $prefix) foreach (range(0,39) as $i) check(in_array($prefix.'-'.$i,$ids,true), 'Missing appended event');
    echo "PASS: deletion, audio references, validation, corruption and 3 concurrent writers\n";
} finally {
    foreach (glob(RECORDINGS_DIR . '/*') as $file) unlink($file);
    rmdir(RECORDINGS_DIR);
    foreach (glob($root . '/*') as $file) unlink($file);
    rmdir($root);
}

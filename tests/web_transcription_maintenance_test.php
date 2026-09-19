<?php
declare(strict_types=1);
$root = sys_get_temp_dir() . '/sip-transcription-test-' . bin2hex(random_bytes(6));
define('CALLS_JSON_PATH', $root . '/calls.json');
define('SMS_JSON_PATH', $root . '/sms.json');
define('RECORDINGS_DIR', $root . '/recordings');
function ensure_storage_layout(): void {}
function normalize_transcription_entries(array $rows): array {
    return array_map(static function (array $row): array {
        foreach (['start', 'end'] as $key) if (isset($row[$key])) $row[$key] = (float) $row[$key];
        return $row;
    }, $rows);
}
require __DIR__ . '/../web/transcription_maintenance_store.php';
function check(bool $ok, string $message): void { if (!$ok) throw new RuntimeException($message); }
mkdir(RECORDINGS_DIR, 0700, true);
try {
    file_put_contents(RECORDINGS_DIR . '/call.wav', 'audio');
    $original = ['id'=>'a', 'number'=>'123', 'timestamp'=>'2026-01-02', 'duration'=>12,
        'recording_file'=>'call.wav', 'transcription'=>[['text'=>'old hallucination']]];
    $other = ['id'=>'b', 'recording_file'=>'call.wav', 'transcription'=>[]];
    file_put_contents(CALLS_JSON_PATH, json_encode([$original, $other]));
    file_put_contents(SMS_JSON_PATH, '[{"id":"sms"}]');
    $snapshot = transcription_snapshot();
    $update = ['id'=>'a', 'version'=>$snapshot[0]['version'], 'audio_sha256'=>$snapshot[0]['audio_sha256'], 'transcription'=>[]];
    // A new call arrived after our snapshot. It must remain first and intact.
    $new = ['id'=>'new', 'transcription'=>[]];
    append_event_record(CALLS_JSON_PATH, $new);
    $result = replace_call_transcriptions([$update]);
    check($result['updated_ids'] === ['a'] && $result['conflicts'] === [], 'Update only selected call');
    check(json_decode(file_get_contents($root . '/' . $result['backup']), true) === [$new, $original, $other], 'Backup captures current full history');
    check((fileperms($root . '/' . $result['backup']) & 0777) === 0600, 'Private backup permissions');
    $expected = $original;
    $expected['transcription'] = [];
    check(json_decode(file_get_contents(CALLS_JSON_PATH), true) === [$new, $expected, $other], 'Clear silence; preserve all metadata and new calls');
    check(replace_call_transcriptions([$update])['unchanged_ids'] === ['a'], 'Retry is idempotent');
    check(file_get_contents(SMS_JSON_PATH) === '[{"id":"sms"}]', 'SMS untouched');
    $row = ['start'=>0, 'end'=>1, 'speaker'=>'Я', 'channel'=>'left', 'text'=>'Привет'];
    $update['transcription'] = [$row];
    check(replace_call_transcriptions([$update])['conflicts']['a'] === 'record_changed', 'Reject stale transcript');
    $update['version'] = transcription_record_version($expected);
    file_put_contents(RECORDINGS_DIR . '/call.wav', 'changed audio');
    check(replace_call_transcriptions([$update])['conflicts']['a'] === 'recording_changed_or_missing', 'Reject changed audio');
    file_put_contents(RECORDINGS_DIR . '/call.wav', 'audio');
    check(replace_call_transcriptions([$update])['updated_ids'] === ['a'], 'Accept valid new transcript');
    check(replace_call_transcriptions([$update])['unchanged_ids'] === ['a'], 'Retry remains idempotent after JSON turns 0.0 into 0');
    delete_event_records('calls', ['a']);
    check(replace_call_transcriptions([$update])['conflicts']['a'] === 'record_missing', 'Do not resurrect deleted call');
    foreach ([[], [$update, $update], [array_replace($update, ['transcription'=>[['text'=>'bad']]])]] as $invalid) {
        $caught = false;
        try { replace_call_transcriptions($invalid); } catch (InvalidArgumentException $error) { $caught = true; }
        check($caught, 'Reject malformed updates before writing');
    }
    check(transcription_recording_path(['recording_file'=>'../calls.json']) === null, 'Block path traversal');
    symlink(CALLS_JSON_PATH, RECORDINGS_DIR . '/symlink.wav');
    check(transcription_recording_path(['recording_file'=>'symlink.wav']) === null, 'Block symlink');
    file_put_contents(CALLS_JSON_PATH, '{broken');
    $caught = false;
    try { replace_call_transcriptions([$update]); } catch (RuntimeException $error) { $caught = true; }
    check($caught && file_get_contents(CALLS_JSON_PATH) === '{broken', 'Fail closed on corrupted history');
    echo "PASS: backup, metadata, new calls, silence, retries, conflicts, deletion, validation, audio paths, corruption\n";
} finally {
    foreach (glob(RECORDINGS_DIR . '/*') as $file) unlink($file);
    rmdir(RECORDINGS_DIR);
    foreach (glob($root . '/*') as $file) unlink($file);
    rmdir($root);
}

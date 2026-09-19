<?php
declare(strict_types=1);

require_once __DIR__ . '/event_store.php';

function transcription_record_version(array $record): string
{
    return hash('sha256', json_encode($record, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));
}

function transcription_recording_path(array $record): ?string
{
    $file = $record['recording_file'] ?? null;
    if (!is_string($file) || $file === '' || basename($file) !== $file || in_array($file, ['.', '..'], true)) {
        return null;
    }
    $path = RECORDINGS_DIR . '/' . $file;
    return is_file($path) && !is_link($path) ? $path : null;
}

function transcription_snapshot(): array
{
    return with_event_store_lock(CALLS_JSON_PATH, static function (array $items): array {
        return array_map(static function (array $record): array {
            $audio = transcription_recording_path($record);
            return ['record' => $record, 'version' => transcription_record_version($record),
                'audio_sha256' => $audio === null ? null : hash_file('sha256', $audio)];
        }, $items);
    });
}

// Only transcription changes. Compare whole records and audio hashes under the
// same lock used by ingestion/deletion, so a stale batch cannot restore a deleted
// call, erase a new call, or attach text to a replaced recording.
function replace_call_transcriptions(array $updates): array
{
    if (!$updates || count($updates) > 1000 || array_values($updates) !== $updates) {
        throw new InvalidArgumentException('Expected 1–1000 updates.');
    }
    $selected = [];
    foreach ($updates as $update) {
        if (!is_array($update) || !is_string($update['id'] ?? null) || $update['id'] === ''
            || strlen($update['id']) > 128 || isset($selected[$update['id']])
            || !is_string($update['version'] ?? null) || !preg_match('/^[a-f0-9]{64}$/D', $update['version'])
            || !is_string($update['audio_sha256'] ?? null) || !preg_match('/^[a-f0-9]{64}$/D', $update['audio_sha256'])
            || !is_array($update['transcription'] ?? null) || array_values($update['transcription']) !== $update['transcription']) {
            throw new InvalidArgumentException('Invalid or duplicate transcription update.');
        }
        foreach ($update['transcription'] as $row) {
            if (!is_array($row) || !is_string($row['text'] ?? null) || trim($row['text']) === ''
                || !is_string($row['speaker'] ?? null) || !is_string($row['channel'] ?? null)
                || !is_numeric($row['start'] ?? null) || !is_numeric($row['end'] ?? null)
                || !is_finite((float) $row['start']) || !is_finite((float) $row['end'])
                || $row['start'] < 0 || $row['end'] < $row['start']) {
                throw new InvalidArgumentException('Invalid transcription segment.');
            }
        }
        $update['transcription'] = normalize_transcription_entries($update['transcription']);
        $selected[$update['id']] = $update;
    }
    return with_event_store_lock(CALLS_JSON_PATH, static function (array $items) use ($selected): array {
        $before = $items;
        $updated = [];
        $unchanged = [];
        $conflicts = [];
        $seen = [];
        foreach ($items as &$record) {
            $id = (string) ($record['id'] ?? '');
            if (!isset($selected[$id])) continue;
            if (isset($seen[$id])) throw new RuntimeException('Duplicate stored call ID.');
            $seen[$id] = true;
            $update = $selected[$id];
            $audio = transcription_recording_path($record);
            $sameText = normalize_transcription_entries($record['transcription'] ?? []) === $update['transcription'];
            if ($audio === null || !hash_equals($update['audio_sha256'], hash_file('sha256', $audio))) {
                $conflicts[$id] = 'recording_changed_or_missing';
            } elseif (hash_equals($update['version'], transcription_record_version($record))) {
                if ($sameText) {
                    $unchanged[] = $id;
                } else {
                    $record['transcription'] = $update['transcription'];
                    $updated[] = $id;
                }
            } elseif ($sameText) {
                // An identical retry needs no write and cannot overwrite metadata.
                $unchanged[] = $id;
            } else {
                $conflicts[$id] = 'record_changed';
            }
        }
        unset($record);
        foreach (array_diff_key($selected, $seen) as $id => $_) $conflicts[$id] = 'record_missing';
        $backup = null;
        if ($updated) {
            $backup = 'calls-before-retranscription-' . gmdate('Ymd-His') . '-' . bin2hex(random_bytes(6)) . '.json';
            $path = dirname(CALLS_JSON_PATH) . '/' . $backup;
            $json = json_encode($before, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . PHP_EOL;
            $handle = fopen($path, 'x');
            if ($handle === false) throw new RuntimeException('Cannot create backup.');
            try {
                if (!chmod($path, 0600) || fwrite($handle, $json) !== strlen($json) || !fflush($handle)) {
                    throw new RuntimeException('Cannot save backup; calls unchanged.');
                }
            } finally {
                fclose($handle);
            }
            write_event_store(CALLS_JSON_PATH, $items);
        }
        return ['updated_ids' => $updated, 'unchanged_ids' => $unchanged, 'conflicts' => $conflicts, 'backup' => $backup];
    });
}

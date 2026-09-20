<?php
declare(strict_types=1);

function event_transcription_search_text($value): string
{
    if (is_string($value)) {
        $decoded = json_decode($value, true);
        return is_array($decoded) ? event_transcription_search_text($decoded) : $value;
    }
    if (!is_array($value)) return '';
    if (isset($value['conversation'])) return event_transcription_search_text($value['conversation']);
    $texts = [];
    foreach ($value as $entry) {
        if (is_array($entry) && is_string($entry['text'] ?? null)) $texts[] = $entry['text'];
    }
    return implode(' ', $texts);
}

function filter_events(array $items, string $query, string $kind): array
{
    if (!in_array($kind, ['calls', 'sms'], true)) throw new InvalidArgumentException('Unknown event kind.');
    $query = trim($query);
    if ($query === '') return $items;

    // Strip formatting only from phone queries: letters must not become an
    // empty filter or accidentally match the digits in an unrelated number.
    $digits = preg_match('/^[+0-9\s().-]+$/uD', $query) ? preg_replace('/\D/', '', $query) : '';
    $pattern = '/' . preg_quote($query, '/') . '/iu';
    return array_values(array_filter($items, static function(array $item) use ($digits, $pattern, $kind): bool {
        $number = (string) ($item['number'] ?? '');
        if (preg_match($pattern, $number) === 1
            || ($digits !== '' && str_contains(preg_replace('/\D/', '', $number), $digits))) return true;
        $text = $kind === 'sms' ? (string) ($item['text'] ?? '')
            : event_transcription_search_text($item['transcription'] ?? []);
        return preg_match($pattern, $text) === 1;
    }));
}

// A stable sidecar lock is shared by ingestion and deletion. Locking the JSON
// inode itself is insufficient because each write replaces it with rename().
function with_event_store_lock(string $path, callable $operation)
{
    ensure_storage_layout();
    if (!in_array($path, [CALLS_JSON_PATH, SMS_JSON_PATH], true)) {
        throw new InvalidArgumentException('Unknown event store.');
    }
    $lock = fopen($path . '.lock', 'c');
    if ($lock === false) {
        throw new RuntimeException('Не удалось открыть хранилище.');
    }
    try {
        if (!flock($lock, LOCK_EX)) {
            throw new RuntimeException('Не удалось заблокировать хранилище.');
        }
        $raw = file_get_contents($path);
        $items = $raw === false ? null : json_decode($raw, true);
        // Fail closed: malformed storage must never become an empty history.
        if (!is_array($items) || array_values($items) !== $items
            || count(array_filter($items, 'is_array')) !== count($items)) {
            throw new RuntimeException('Хранилище повреждено. Изменения не сохранены.');
        }
        return $operation($items);
    } finally {
        flock($lock, LOCK_UN);
        fclose($lock);
    }
}

function write_event_store(string $path, array $items): void
{
    $json = json_encode(array_values($items), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . PHP_EOL;
    $temporary = tempnam(dirname($path), '.events-');
    if ($temporary === false) {
        throw new RuntimeException('Не удалось создать временный файл.');
    }
    try {
        if (file_put_contents($temporary, $json, LOCK_EX) !== strlen($json) || !rename($temporary, $path)) {
            throw new RuntimeException('Не удалось сохранить изменения.');
        }
    } finally {
        if (is_file($temporary)) {
            unlink($temporary);
        }
    }
}

function append_event_record(string $path, array $record): void
{
    with_event_store_lock($path, static function (array $items) use ($path, $record): void {
        array_unshift($items, $record);
        write_event_store($path, $items);
    });
}

function delete_event_records(string $kind, array $ids): array
{
    if (!in_array($kind, ['calls', 'sms'], true) || !$ids) {
        throw new InvalidArgumentException('Выберите записи одного типа.');
    }
    foreach ($ids as $id) {
        if (!is_string($id) || $id === '' || strlen($id) > 128) {
            throw new InvalidArgumentException('Некорректный идентификатор записи.');
        }
    }
    $selected = array_fill_keys($ids, true);
    $path = $kind === 'calls' ? CALLS_JSON_PATH : SMS_JSON_PATH;
    return with_event_store_lock($path, static function (array $items) use ($selected, $path, $kind): array {
        $remaining = [];
        $removed = [];
        $recordings = [];
        $retainedRecordings = [];
        foreach ($items as $item) {
            $id = (string) ($item['id'] ?? '');
            $file = (string) ($item['recording_file'] ?? '');
            if (isset($selected[$id])) {
                $removed[] = $id;
                // Only files directly inside recordings may be removed.
                if ($kind === 'calls' && $file !== '' && basename($file) === $file
                    && $file !== '.' && $file !== '..') {
                    $recordings[$file] = true;
                }
            } else {
                $remaining[] = $item;
                $retainedRecordings[basename($file)] = true;
            }
        }
        $cleanupFailed = 0;
        if ($removed) {
            write_event_store($path, $remaining);
            foreach (array_diff_key($recordings, $retainedRecordings) as $file => $_) {
                $audio = RECORDINGS_DIR . '/' . $file;
                if ((is_file($audio) || is_link($audio)) && !@unlink($audio)) {
                    $cleanupFailed++;
                }
            }
        }
        return ['deleted_ids' => array_values(array_unique($removed)), 'recording_cleanup_failed' => $cleanupFailed];
    });
}

function event_deletion_csrf_token(): string
{
    start_app_session();
    if (empty($_SESSION['event_deletion_csrf'])) {
        $_SESSION['event_deletion_csrf'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['event_deletion_csrf'];
}

function normalize_transcription_channels($value): array
{
    if (is_string($value)) $value = json_decode($value, true);
    if (!is_array($value)) return [];
    $result = [];
    foreach (['left','right'] as $channel) {
        $meta = $value[$channel] ?? null;
        if (!is_array($meta) || !in_array($meta['status'] ?? null, ['recognized','unrecognized','no_speech'], true)) continue;
        $result[$channel] = ['status'=>$meta['status'], 'speaker'=>substr((string) ($meta['speaker'] ?? ''), 0, 160),
            'speech_seconds'=>max(0, min(86400, (float) ($meta['speech_seconds'] ?? 0)))];
    }
    return $result;
}

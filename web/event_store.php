<?php
declare(strict_types=1);

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
    if (!in_array($kind, ['calls', 'sms'], true) || !$ids || count($ids) > 100) {
        throw new InvalidArgumentException('Выберите от 1 до 100 записей одного типа.');
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

<?php
declare(strict_types=1);

const VOICE_MAX_BYTES = 2097152;

function voice_transaction(callable $operation): array
{
    ensure_storage_layout();
    $path = DATA_STORAGE_DIR . '/voice-outbox.json';
    $lock = fopen($path . '.lock', 'c');
    if (!$lock) throw new RuntimeException('Не удалось открыть очередь вызовов.');
    try {
        if (!flock($lock, LOCK_EX)) throw new RuntimeException('Не удалось заблокировать очередь вызовов.');
        $store = is_file($path) ? json_decode((string) file_get_contents($path), true) : ['jobs'=>[], 'worker_seen'=>0];
        if (!is_array($store) || !is_array($store['jobs'] ?? null)) throw new RuntimeException('Очередь вызовов повреждена.');
        $before = $store;
        foreach ($store['jobs'] as &$job) {
            if ($job['status'] === 'queued' && $job['scheduled_unix'] < time()-600) {
                $job['status'] = 'missed';
                $job['message'] = 'Время пропущено: сервер не начал вызов в течение 10 минут.';
            }
            if ($job['status'] === 'calling' && $job['claimed_at'] < time()-600) {
                $job['status'] = 'unknown';
                $job['message'] = 'Связь с сервером потеряна. Автоповтора не будет.';
            }
        }
        unset($job);
        $result = $operation($store);
        if ($store !== $before) {
            $json = json_encode($store, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
            $temp = tempnam(DATA_STORAGE_DIR, '.voice-');
            if ($temp === false) throw new RuntimeException('Не удалось сохранить очередь вызовов.');
            try {
                if (file_put_contents($temp, $json, LOCK_EX) !== strlen($json) || !rename($temp, $path)) throw new RuntimeException('Не удалось сохранить очередь вызовов.');
            } finally { if (is_file($temp)) unlink($temp); }
        }
        // Uploaded originals are transport files; after a durable terminal state
        // only the Asterisk-side journal/audio is needed. Do not fill shared hosting.
        foreach ($store['jobs'] as $job) {
            if (in_array($job['status'], ['queued','calling'], true)) continue;
            $audioPath = DATA_STORAGE_DIR . '/voice-audio/' . $job['id'] . '.audio';
            if (is_file($audioPath)) @unlink($audioPath);
        }
        return $result;
    } finally { flock($lock, LOCK_UN); fclose($lock); }
}

function voice_public_job(array $job): array
{
    unset($job['claim_token'], $job['request_key'], $job['audio_hash']);
    return $job;
}

function voice_state(): array
{
    return voice_transaction(static function(array &$store): array {
        $pending = array_values(array_filter($store['jobs'], static fn(array $j): bool => in_array($j['status'], ['queued','calling'], true)));
        usort($pending, static fn($a,$b) => $a['scheduled_unix'] <=> $b['scheduled_unix']);
        $history = array_values(array_filter(array_reverse($store['jobs']), static fn(array $j): bool => !in_array($j['status'], ['queued','calling'], true)));
        return ['jobs'=>array_map('voice_public_job', array_merge($pending, array_slice($history, 0, 100-count($pending)))),
            'ports'=>$store['ports'] ?? [], 'connected'=>$store['worker_seen'] > time()-90, 'timezone'=>'Europe/Moscow'];
    });
}

function voice_enqueue(array $data, string $source, string $audio): array
{
    $key = $data['request_key'] ?? null;
    $number = $data['number'] ?? null;
    $when = $data['scheduled_at'] ?? '';
    $sender = $data['sender'] ?? null;
    if ($sender !== null && !is_string($sender) && !is_int($sender)) throw new InvalidArgumentException('Выберите SIM отправителя.');
    if (!is_string($key) || !preg_match('/^[a-zA-Z0-9_.:-]{16,128}$/D', $key)
        || !is_string($number) || !preg_match('/^\+[1-9][0-9]{6,14}$/D', $number)
        || !is_string($when) || strlen($audio) < 32 || strlen($audio) > VOICE_MAX_BYTES) {
        throw new InvalidArgumentException('Укажите международный номер с + и запись до 2 МБ / 120 секунд.');
    }
    $at = $when === '' ? time() : false;
    if ($when !== '' && preg_match('/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$/D', $when)) {
        try { $date = new DateTimeImmutable($when); $errors = DateTimeImmutable::getLastErrors();
            if ($errors === false || (!$errors['warning_count'] && !$errors['error_count'])) $at = $date->getTimestamp();
        } catch (Exception $error) { /* rejected below */ }
    }
    if ($at === false) throw new InvalidArgumentException('Некорректное время вызова.');
    $hash = hash('sha256', $audio);
    return voice_transaction(static function(array &$store) use ($key, $number, $when, $at, $hash, $audio, $source, $sender): array {
        $port = null;
        foreach ($store['ports'] ?? [] as $candidate) {
            if ((string) $candidate['port'] === (string) $sender || $candidate['number'] === $sender) $port = $candidate;
        }
        // Existing Telegram clients may omit sender and use the configured first route.
        if ($sender === null && $source !== 'web') $port = ($store['ports'] ?? [])[0] ?? null;
        if (!$port) throw new InvalidArgumentException('Выберите доступный номер отправителя.');
        // A retry after a lost HTTP response returns the same job, even after its due time.
        foreach ($store['jobs'] as $job) {
            if ($job['request_key'] !== $key) continue;
            if ($job['number'] !== $number || $job['requested_at'] !== $when || $job['audio_hash'] !== $hash || $job['source'] !== $source || ($job['port'] ?? null) !== $port['port'] || ($job['sender'] ?? '') !== $port['number']) throw new InvalidArgumentException('Запрос уже использован для другой записи.');
            return voice_public_job($job);
        }
        if ($when !== '' && ($at <= time() || $at > time()+90*86400)) throw new InvalidArgumentException('Выберите будущее время в пределах 90 дней.');
        if (count(array_filter($store['jobs'], static fn(array $j): bool => in_array($j['status'], ['queued','calling'], true))) >= 50) throw new InvalidArgumentException('В очереди уже 50 вызовов.');
        $free = disk_free_space(DATA_STORAGE_DIR);
        if ($free !== false && $free < strlen($audio) + 32*1024*1024) throw new InvalidArgumentException('На хостинге недостаточно свободного места для записи.');
        $id = bin2hex(random_bytes(16));
        $dir = DATA_STORAGE_DIR . '/voice-audio';
        if (!is_dir($dir) && !mkdir($dir, 0700, true)) throw new RuntimeException('Не удалось сохранить запись.');
        if (file_put_contents($dir . '/' . $id . '.audio', $audio, LOCK_EX) !== strlen($audio)) throw new RuntimeException('Не удалось сохранить запись.');
        chmod($dir . '/' . $id . '.audio', 0600);
        $job = ['id'=>$id, 'request_key'=>$key, 'number'=>$number, 'port'=>$port['port'], 'sender'=>$port['number'], 'source'=>$source, 'requested_at'=>$when,
            'scheduled_at'=>gmdate('Y-m-d\TH:i:s\Z', $at), 'scheduled_unix'=>$at, 'audio_hash'=>$hash,
            'status'=>'queued', 'message'=>'Ожидает времени вызова', 'created_at'=>gmdate('Y-m-d\TH:i:s\Z')];
        $store['jobs'][] = $job;
        return voice_public_job($job);
    });
}

function voice_uploaded_audio(): string
{
    $upload = $_FILES['audio'] ?? null;
    if (!is_array($upload) || ($upload['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_OK
        || !is_uploaded_file($upload['tmp_name']) || $upload['size'] > VOICE_MAX_BYTES) throw new InvalidArgumentException('Загрузите аудиозапись до 2 МБ.');
    $audio = file_get_contents($upload['tmp_name']);
    if ($audio === false) throw new RuntimeException('Не удалось прочитать запись.');
    return $audio;
}

function voice_cancel(string $id): array
{
    return voice_transaction(static function(array &$store) use ($id): array {
        foreach ($store['jobs'] as &$job) {
            if ($job['id'] !== $id) continue;
            if ($job['status'] === 'cancelled') return ['job'=>voice_public_job($job)];
            if ($job['status'] !== 'queued') throw new InvalidArgumentException('Вызов уже начат или завершён; отменить его нельзя.');
            $job['status'] = 'cancelled'; $job['message'] = 'Отменён пользователем';
            return ['job'=>voice_public_job($job)];
        }
        throw new InvalidArgumentException('Вызов не найден.');
    });
}

function voice_worker_action(array $data): array
{
    return voice_transaction(static function(array &$store) use ($data): array {
        if ($data['action'] === 'heartbeat') {
            if (array_key_exists('ports', $data)) {
                if (!is_array($data['ports']) || count($data['ports']) > 32) throw new InvalidArgumentException('Invalid voice routes.');
                $ports = [];
                foreach ($data['ports'] as $port) {
                    if (!is_array($port) || !is_int($port['port'] ?? null) || $port['port'] < 1 || $port['port'] > 32
                        || !is_string($port['number'] ?? null) || !preg_match('/^\+[1-9][0-9]{6,14}$/D', $port['number'])
                        || isset($ports[$port['port']])) throw new InvalidArgumentException('Invalid voice route.');
                    $ports[$port['port']] = ['port'=>$port['port'], 'number'=>$port['number']];
                }
                $store['ports'] = array_values($ports);
            }
            if ($store['worker_seen'] <= time()-15) $store['worker_seen'] = time();
            $due = false;
            foreach ($store['jobs'] as $job) {
                if ($job['status'] === 'queued' && $job['scheduled_unix'] <= time()) { $due = true; break; }
            }
            return ['has_due'=>$due];
        }
        if ($data['action'] === 'claim') {
            // Serialize calls even if a second worker is accidentally started.
            foreach ($store['jobs'] as $job) if ($job['status'] === 'calling') return ['job'=>null];
            $indices = array_keys($store['jobs']);
            usort($indices, static fn($a,$b) => $store['jobs'][$a]['scheduled_unix'] <=> $store['jobs'][$b]['scheduled_unix']);
            foreach ($indices as $index) {
                $job = &$store['jobs'][$index];
                if ($job['status'] !== 'queued' || $job['scheduled_unix'] > time()) continue;
                $job['status'] = 'calling'; $job['message'] = 'Подготовка записи и вызов';
                $job['claimed_at'] = time(); $job['claim_token'] = bin2hex(random_bytes(24));
                return ['job'=>$job];
            }
            return ['job'=>null];
        }
        if ($data['action'] !== 'complete' || !in_array($data['status'] ?? null, ['completed','failed','interrupted','unknown'], true)
            || !is_string($data['message'] ?? null) || strlen($data['message']) > 1000) throw new InvalidArgumentException('Некорректный результат вызова.');
        foreach ($store['jobs'] as &$job) {
            if ($job['id'] !== ($data['id'] ?? null)) continue;
            if (!is_string($data['claim_token'] ?? null) || empty($job['claim_token']) || !hash_equals($job['claim_token'], $data['claim_token'])) throw new InvalidArgumentException('Invalid claim.');
            if (in_array($job['status'], ['calling','unknown'], true)) {
                $job['status'] = $data['status']; $job['message'] = $data['message'];
                $job['completed_at'] = gmdate('Y-m-d\TH:i:s\Z');
            }
            return ['job'=>voice_public_job($job)];
        }
        throw new InvalidArgumentException('Вызов не найден.');
    });
}

function voice_audio_response(string $id): void
{
    if (!preg_match('/^[a-f0-9]{32}$/D', $id)) json_response(['ok'=>false], 404);
    $path = DATA_STORAGE_DIR . '/voice-audio/' . $id . '.audio';
    if (!is_file($path)) json_response(['ok'=>false], 404);
    header('Content-Type: application/octet-stream');
    header('Cache-Control: no-store');
    header('Content-Length: ' . filesize($path));
    readfile($path); exit;
}

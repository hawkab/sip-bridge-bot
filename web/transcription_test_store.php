<?php
declare(strict_types=1);

const ASR_TEST_MAX_BYTES = 20 * 1024 * 1024;

function asr_test_transaction(callable $operation): array
{
    ensure_storage_layout();
    $dir = DATA_STORAGE_DIR . '/transcription-tests';
    if (!is_dir($dir) && !mkdir($dir, 0700, true)) throw new RuntimeException('Не удалось сохранить запись.');
    $path = $dir . '/jobs.json';
    $lock = fopen($dir . '/jobs.lock', 'c');
    if (!$lock) throw new RuntimeException('Очередь недоступна.');
    try {
        if (!flock($lock, LOCK_EX)) throw new RuntimeException('Очередь недоступна.');
        $store = is_file($path) ? json_decode((string) file_get_contents($path), true) : ['jobs'=>[], 'worker_seen'=>0];
        if (!is_array($store) || !is_array($store['jobs'] ?? null)) throw new RuntimeException('Очередь повреждена.');
        $before = $store;
        foreach ($store['jobs'] as $id => &$job) {
            if ($job['created_at'] < time()-86400) { unset($store['jobs'][$id]); continue; }
            if (in_array($job['status'], ['queued','processing'], true) && $job['created_at'] < time()-3600) {
                $job['status'] = 'failed'; $job['message'] = 'Время ожидания истекло. Повторите проверку.';
            }
        }
        unset($job);
        $result = $operation($store, $dir);
        if ($before !== $store) {
            $json = json_encode($store, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
            $tmp = tempnam($dir, '.jobs-');
            if ($tmp === false) throw new RuntimeException('Не удалось сохранить результат.');
            try {
                chmod($tmp, 0600);
                if (file_put_contents($tmp, $json) !== strlen($json) || !rename($tmp, $path)) throw new RuntimeException('Не удалось сохранить результат.');
            } finally { if (is_file($tmp)) unlink($tmp); }
        }
        foreach (glob($dir . '/*.audio') ?: [] as $audio) {
            $id = basename($audio, '.audio');
            if (!isset($store['jobs'][$id]) || !in_array($store['jobs'][$id]['status'], ['queued','processing'], true)) @unlink($audio);
        }
        return $result;
    } finally { flock($lock, LOCK_UN); fclose($lock); }
}

function asr_test_public(array $job): array
{
    unset($job['claim_token'], $job['audio_hash']);
    return $job;
}

function asr_test_enqueue(array $data, string $audio): array
{
    $backend = $data['backend'] ?? '';
    $id = $data['id'] ?? '';
    if (!is_string($id) || !preg_match('/^[a-f0-9]{32}$/D', $id) || !in_array($backend, ['gigaam','whisper'], true)) throw new InvalidArgumentException('Выберите движок распознавания.');
    if (strlen($audio) < 32 || strlen($audio) > ASR_TEST_MAX_BYTES) throw new InvalidArgumentException('Загрузите аудиофайл до 20 МБ.');
    return asr_test_transaction(static function(array &$store, string $dir) use ($id, $backend, $audio): array {
        $hash = hash('sha256', $audio);
        if (isset($store['jobs'][$id])) {
            $job = $store['jobs'][$id];
            if ($job['backend'] !== $backend || $job['audio_hash'] !== $hash) throw new InvalidArgumentException('Этот запрос уже использован.');
            return ['job'=>asr_test_public($job)];
        }
        if (count(array_filter($store['jobs'], static fn($j)=>in_array($j['status'], ['queued','processing'], true))) >= 5) throw new InvalidArgumentException('Очередь заполнена. Попробуйте позже.');
        if (count($store['jobs']) >= 200) throw new InvalidArgumentException('Лимит проверок за сутки исчерпан.');
        $free = disk_free_space($dir);
        if ($free !== false && $free < strlen($audio) + 32*1024*1024) throw new RuntimeException('Недостаточно места для записи.');
        if (file_put_contents($dir . '/' . $id . '.audio', $audio, LOCK_EX) !== strlen($audio)) throw new RuntimeException('Не удалось сохранить запись.');
        chmod($dir . '/' . $id . '.audio', 0600);
        $job = ['id'=>$id, 'backend'=>$backend, 'audio_hash'=>$hash, 'status'=>'queued', 'created_at'=>time()];
        $store['jobs'][$id] = $job;
        return ['job'=>asr_test_public($job)];
    });
}

function asr_test_get(string $id): array
{
    return asr_test_transaction(static function(array &$store) use ($id): array {
        if (!isset($store['jobs'][$id])) throw new InvalidArgumentException('Проверка не найдена или срок хранения истёк.');
        return ['job'=>asr_test_public($store['jobs'][$id]), 'connected'=>$store['worker_seen'] > time()-90];
    });
}

function asr_test_worker(array $data): array
{
    return asr_test_transaction(static function(array &$store) use ($data): array {
        $action = $data['action'] ?? '';
        if ($action === 'claim') {
            if ($store['worker_seen'] < time()-15) $store['worker_seen'] = time();
            foreach ($store['jobs'] as $job) if ($job['status'] === 'processing') return ['job'=>null];
            foreach ($store['jobs'] as &$job) {
                if ($job['status'] !== 'queued') continue;
                $job['status'] = 'processing'; $job['claim_token'] = bin2hex(random_bytes(24));
                return ['job'=>$job];
            }
            return ['job'=>null];
        }
        $id = $data['id'] ?? '';
        if (!is_string($id) || !isset($store['jobs'][$id])) throw new InvalidArgumentException('Проверка не найдена.');
        $job = &$store['jobs'][$id];
        if (!is_string($data['claim_token'] ?? null) || empty($job['claim_token']) || !hash_equals($job['claim_token'], $data['claim_token'])) throw new InvalidArgumentException('Invalid claim.');
        if ($action !== 'complete' || !in_array($data['status'] ?? '', ['completed','failed'], true)) throw new InvalidArgumentException('Invalid result.');
        if (!is_string($data['text'] ?? '') || strlen($data['text'] ?? '') > 100000 || !is_string($data['message'] ?? '') || strlen($data['message'] ?? '') > 1000) throw new InvalidArgumentException('Invalid text.');
        foreach (['duration','elapsed'] as $field) if (!is_numeric($data[$field] ?? 0) || !is_finite((float)($data[$field] ?? 0)) || ($data[$field] ?? 0) < 0) throw new InvalidArgumentException('Invalid duration.');
        if (isset($data['speech_detected']) && !is_bool($data['speech_detected'])) throw new InvalidArgumentException('Invalid speech flag.');
        if ($job['status'] === 'processing') {
            $job = array_merge($job, array_intersect_key($data, array_flip(['status','text','message','duration','elapsed','speech_detected'])));
            $job['completed_at'] = time();
        }
        return ['job'=>asr_test_public($job)];
    });
}

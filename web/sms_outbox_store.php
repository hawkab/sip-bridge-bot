<?php
declare(strict_types=1);

function sms_outbox_transaction(callable $operation): array
{
    ensure_storage_layout();
    $path = DATA_STORAGE_DIR . '/sms-outbox.json';
    $lock = fopen($path . '.lock', 'c');
    if (!$lock) throw new RuntimeException('Не удалось открыть очередь СМС.');
    try {
        if (!flock($lock, LOCK_EX)) throw new RuntimeException('Не удалось заблокировать очередь СМС.');
        $store = is_file($path) ? json_decode((string) file_get_contents($path), true) : ['ports'=>[], 'jobs'=>[], 'worker_seen'=>0, 'connected'=>false];
        if (!is_array($store) || !is_array($store['jobs'] ?? null) || !is_array($store['ports'] ?? null)) {
            throw new RuntimeException('Очередь СМС повреждена. Изменения не сохранены.');
        }
        $before = $store;
        foreach ($store['jobs'] as &$job) {
            if ($job['status'] === 'sending' && $job['claimed_at'] < time() - 180) {
                $job['status'] = 'unknown';
                $job['message'] = 'Связь с отправителем потеряна. Проверьте получателя перед повторной отправкой.';
            }
            if ($job['status'] === 'queued' && $job['created_at_unix'] < time() - 86400) {
                $job['status'] = 'failed';
                $job['message'] = 'Истёк срок ожидания отправки (24 часа).';
            }
        }
        unset($job);
        $result = $operation($store);
        if ($store !== $before) {
            $json = json_encode($store, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
            $temp = tempnam(DATA_STORAGE_DIR, '.sms-outbox-');
            if ($temp === false) throw new RuntimeException('Не удалось сохранить очередь СМС.');
            try {
                if (file_put_contents($temp, $json, LOCK_EX) !== strlen($json) || !rename($temp, $path)) {
                    throw new RuntimeException('Не удалось сохранить очередь СМС.');
                }
            } finally { if (is_file($temp)) unlink($temp); }
        }
        return $result;
    } finally { flock($lock, LOCK_UN); fclose($lock); }
}

function sms_public_job(array $job): array
{
    unset($job['claim_token'], $job['request_key']);
    return $job;
}

function sms_outbox_state(): array
{
    return sms_outbox_transaction(static function (array &$store): array {
        return ['ports'=>$store['ports'], 'connected'=>$store['connected'] && $store['worker_seen'] > time()-90,
            'jobs'=>array_map('sms_public_job', array_slice(array_reverse($store['jobs']), 0, 50))];
    });
}

function sms_outbox_enqueue(array $data, string $source): array
{
    $key = $data['request_key'] ?? null;
    $number = $data['number'] ?? null;
    $text = $data['text'] ?? null;
    $sender = $data['sender'] ?? null;
    if (!is_string($key) || !preg_match('/^[a-zA-Z0-9_.:-]{16,128}$/D', $key)
        || !is_string($number) || !preg_match('/^\+?[1-9][0-9]{6,14}$/D', $number)
        || !is_string($text) || trim($text) === '' || strlen($text) > 1024
        || strpos($text, "\0") !== false || !preg_match('//u', $text)
        || (!is_string($sender) && !is_int($sender))) {
        throw new InvalidArgumentException('Выберите SIM, укажите международный номер и текст (до 1024 байт UTF-8).');
    }
    return sms_outbox_transaction(static function (array &$store) use ($key, $number, $text, $sender, $source): array {
        $port = null;
        foreach ($store['ports'] as $candidate) {
            if ((string) $candidate['port'] === (string) $sender || ($candidate['number'] !== '' && $candidate['number'] === $sender)) $port = $candidate;
        }
        if (!$port) throw new InvalidArgumentException('SIM не найдена. Обновите список отправителей.');
        foreach ($store['jobs'] as $job) {
            if ($job['request_key'] === $key) {
                if ($job['number'] !== $number || $job['text'] !== $text || $job['port'] !== $port['port'] || $job['source'] !== $source) {
                    throw new InvalidArgumentException('Этот запрос уже использован для другого сообщения.');
                }
                return sms_public_job($job);
            }
        }
        $pending = array_filter($store['jobs'], static fn(array $job): bool => in_array($job['status'], ['queued','sending'], true));
        if (count($pending) >= 50) throw new InvalidArgumentException('В очереди уже 50 сообщений. Дождитесь отправки.');
        $job = ['id'=>bin2hex(random_bytes(16)), 'request_key'=>$key, 'number'=>$number, 'text'=>$text,
            'port'=>$port['port'], 'sender'=>$port['number'], 'source'=>$source, 'status'=>'queued',
            'message'=>'Ожидает отправки', 'created_at'=>date(DATE_ATOM), 'created_at_unix'=>time()];
        $store['jobs'][] = $job;
        return sms_public_job($job);
    });
}

function sms_outbox_heartbeat(array $data): array
{
    $ports = $data['ports'] ?? null;
    if (!is_array($ports) || !$ports || count($ports)>32 || !is_bool($data['connected'] ?? null)) throw new InvalidArgumentException('Invalid gateway configuration.');
    $seen = [];
    foreach ($ports as $port) {
        if (!is_array($port) || !is_int($port['port'] ?? null) || $port['port'] < 1 || $port['port'] > 32
            || isset($seen[$port['port']]) || !is_string($port['number'] ?? null)
            || ($port['number'] !== '' && !preg_match('/^\+[1-9][0-9]{6,14}$/D', $port['number']))) throw new InvalidArgumentException('Invalid SIM port.');
        $seen[$port['port']] = true;
    }
    return sms_outbox_transaction(static function (array &$store) use ($ports, $data): array {
        $store['ports'] = array_map(static fn(array $p): array => ['port'=>$p['port'], 'number'=>$p['number']], $ports);
        $store['connected'] = $data['connected'];
        $store['worker_seen'] = time();
        return ['ok'=>true];
    });
}

function sms_outbox_claim(): array
{
    return sms_outbox_transaction(static function (array &$store): array {
        foreach ($store['jobs'] as &$job) {
            if ($job['status'] !== 'queued') continue;
            $job['status'] = 'sending';
            $job['message'] = 'Отправляется';
            $job['claimed_at'] = time();
            $job['claim_token'] = bin2hex(random_bytes(24));
            return ['job'=>$job];
        }
        return ['job'=>null];
    });
}

function sms_outbox_complete(array $data): array
{
    if (!is_string($data['id'] ?? null) || !is_string($data['claim_token'] ?? null)
        || !in_array($data['status'] ?? null, ['sent','failed','unknown'], true)
        || !is_string($data['message'] ?? null) || strlen($data['message'])>1000
        || !is_string($data['gateway_id'] ?? null) || strlen($data['gateway_id'])>64) throw new InvalidArgumentException('Invalid result.');
    return sms_outbox_transaction(static function (array &$store) use ($data): array {
        foreach ($store['jobs'] as &$job) {
            if ($job['id'] !== $data['id']) continue;
            if (!hash_equals($job['claim_token'] ?? '', $data['claim_token']) || empty($job['claim_token'])) throw new InvalidArgumentException('Invalid claim.');
            if (in_array($job['status'], ['sent','failed'], true)) return ['job'=>sms_public_job($job)];
            $job['status'] = $data['status'];
            $job['message'] = $data['message'];
            $job['gateway_id'] = $data['gateway_id'];
            $job['completed_at'] = date(DATE_ATOM);
            return ['job'=>sms_public_job($job)];
        }
        throw new InvalidArgumentException('Message not found.');
    });
}

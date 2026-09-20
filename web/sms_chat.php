<?php
declare(strict_types=1);

require_once __DIR__ . '/sms_outbox_store.php';

function sms_chat_number(string $number): string
{
    $number = trim($number);
    if (!preg_match('/^\+?[0-9 ()-]+$/D', $number)) return $number;
    $digits = preg_replace('/\D/', '', $number);
    if (strlen($digits) === 11 && $digits[0] === '8') $digits = '7' . substr($digits, 1);
    return '+' . $digits;
}

function sms_chat_context(array $query): array
{
    $state = sms_outbox_state();
    $item = null;
    if (!empty($query['id'])) {
        $item = find_record_by_id(SMS_JSON_PATH, (string) $query['id']);
        if ($item === null) throw new InvalidArgumentException('СМС не найдена.');
        $number = sms_chat_number((string) ($item['number'] ?? ''));
        $local = sms_chat_number((string) ($item['local_number'] ?? ''));
        $port = null;
        foreach ($state['ports'] as $candidate) {
            // A port may contain a different SIM now. Never silently reply from it.
            if ($local !== '' && sms_chat_number($candidate['number']) === $local) $port = $candidate;
        }
    } else {
        $number = sms_chat_number((string) ($query['number'] ?? ''));
        $port = null;
        foreach ($state['ports'] as $candidate) {
            if ((string) $candidate['port'] === (string) ($query['sender'] ?? '')) $port = $candidate;
        }
        if (!$port || !preg_match('/^\+[1-9][0-9]{6,14}$/D', $number)) {
            throw new InvalidArgumentException('Выберите SIM и укажите номер получателя.');
        }
        $local = (string) $port['number'];
    }
    return ['number'=>$number, 'local_number'=>$local, 'sim_port'=>$port['port'] ?? null,
        'can_reply'=>$port !== null && preg_match('/^\+[1-9][0-9]{6,14}$/D', $number) === 1,
        'item'=>$item, 'ports'=>$state['ports'], 'connected'=>$state['connected']];
}

function sms_chat(array $query): array
{
    $context = sms_chat_context($query);
    $messages = [];
    foreach (read_json_array(SMS_JSON_PATH) as $item) {
        $same = $context['local_number'] !== ''
            && sms_chat_number((string) ($item['number'] ?? '')) === $context['number']
            && sms_chat_number((string) ($item['local_number'] ?? '')) === $context['local_number'];
        if (!$same && ($item['id'] ?? '') !== ($context['item']['id'] ?? null)) continue;
        $messages[] = ['id'=>'in-'.$item['id'], 'direction'=>'incoming', 'text'=>$item['text'],
            'timestamp'=>$item['timestamp'], 'status'=>'received'];
    }
    $outgoing = sms_outbox_transaction(static function(array &$store) use ($context): array {
        return array_values(array_filter($store['jobs'], static fn(array $job): bool =>
            $context['local_number'] !== '' && sms_chat_number($job['number']) === $context['number']
            && sms_chat_number($job['sender']) === $context['local_number']));
    });
    foreach ($outgoing as $job) {
        $messages[] = ['id'=>'out-'.$job['id'], 'direction'=>'outgoing', 'text'=>$job['text'],
            'timestamp'=>$job['created_at'], 'status'=>$job['status'], 'message'=>$job['message']];
    }
    $tz = new DateTimeZone('Europe/Moscow');
    usort($messages, static fn($a, $b) =>
        (new DateTimeImmutable($a['timestamp'], $tz))->getTimestamp() <=> (new DateTimeImmutable($b['timestamp'], $tz))->getTimestamp());
    unset($context['item']);
    return $context + ['messages'=>$messages];
}

function sms_reply_payload(array $data): array
{
    if (empty($data['reply_to'])) return $data;
    if (!is_string($data['reply_to'])) throw new InvalidArgumentException('Некорректная карточка СМС.');
    $context = sms_chat_context(['id'=>$data['reply_to']]);
    if (!$context['can_reply']) throw new InvalidArgumentException('Неизвестна принимающая SIM или отправитель не принимает ответы. Создайте новую СМС и выберите получателя.');
    if ((string) ($data['sender'] ?? '') !== (string) $context['sim_port']
        || sms_chat_number((string) ($data['number'] ?? '')) !== $context['number']) {
        throw new InvalidArgumentException('SIM и получатель ответа должны совпадать с карточкой СМС.');
    }
    return array_replace($data, ['sender'=>(string) $context['sim_port'], 'number'=>$context['number']]);
}

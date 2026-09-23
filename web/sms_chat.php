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

function sms_chat_id(array $item): string
{
    $local = sms_chat_number((string) ($item['local_number'] ?? ''));
    $identity = $local !== '' ? 'number:' . $local : 'unknown:' . (string) ($item['sim_port'] ?? '');
    return 'chat-' . hash('sha256', json_encode([$identity, sms_chat_number((string) ($item['number'] ?? ''))], JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR));
}

function sms_chat_outgoing_item(array $job): array
{
    return ['id'=>$job['id'], 'number'=>$job['number'], 'local_number'=>$job['sender'], 'sim_port'=>$job['port'],
        'text'=>$job['text'], 'timestamp'=>$job['created_at'], 'direction'=>'outgoing', 'status'=>$job['status']];
}

function sms_chat_timestamp(string $timestamp): int
{
    if (trim($timestamp) === '') return 0;
    try { return (new DateTimeImmutable($timestamp, new DateTimeZone('Europe/Moscow')))->getTimestamp(); }
    catch (Exception $e) { return 0; }
}

function sms_chat_summaries(string $query = ''): array
{
    $items = read_json_array(SMS_JSON_PATH);
    $state = sms_outbox_transaction(static fn(array &$store): array => $store);
    foreach ($state['jobs'] as $job) {
        if (empty($job['chat_deleted'])) $items[] = sms_chat_outgoing_item($job);
    }
    $chats = [];
    foreach ($items as $item) {
        $id = sms_chat_id($item);
        $time = sms_chat_timestamp((string) ($item['timestamp'] ?? ''));
        if (!isset($chats[$id])) {
            $chats[$id] = ['id'=>$id, 'number'=>sms_chat_number((string) ($item['number'] ?? '')),
                'local_number'=>sms_chat_number((string) ($item['local_number'] ?? '')), 'sim_port'=>$item['sim_port'] ?? null,
                'text'=>'', 'timestamp'=>'', 'message_count'=>0, '_time'=>-1, '_search_text'=>''];
        }
        $chat = &$chats[$id];
        $chat['message_count']++;
        $chat['_search_text'] .= (string) ($item['text'] ?? '') . "\n";
        if ($time >= $chat['_time']) {
            $chat['timestamp'] = (string) ($item['timestamp'] ?? ''); $chat['_time'] = $time;
            $chat['text'] = (string) ($item['text'] ?? ''); $chat['direction'] = $item['direction'] ?? 'incoming';
            $chat['status'] = $item['status'] ?? 'received';
        }
        unset($chat);
    }
    foreach ($chats as $id => &$chat) {
        foreach ($state['ports'] as $port) {
            if ($chat['local_number'] !== '' && sms_chat_number($port['number']) === $chat['local_number']) $chat['sim_port'] = $port['port'];
        }
        // Search every message, not just the last preview. Keep the whole chat.
        if ($query !== '' && !filter_events([array_replace($chat, ['text'=>$chat['_search_text']])], $query, 'sms')) {
            unset($chats[$id]); continue;
        }
        unset($chat['_time'], $chat['_search_text']);
    }
    unset($chat);
    return array_values($chats);
}

function sms_chat_record(string $id): ?array
{
    if (!str_starts_with($id, 'chat-')) return find_record_by_id(SMS_JSON_PATH, $id);
    foreach (sms_chat_summaries() as $chat) if ($chat['id'] === $id) return $chat;
    return null;
}

function delete_sms_chats(array $ids): array
{
    if (!$ids) throw new InvalidArgumentException('Выберите переписки.');
    foreach ($ids as $id) if (!is_string($id) || !preg_match('/^chat-[a-f0-9]{64}$/D', $id)) throw new InvalidArgumentException('Некорректный идентификатор чата.');
    $selected = array_fill_keys($ids, true);
    return with_event_store_lock(SMS_JSON_PATH, static function(array $items) use ($selected): array {
        return sms_outbox_transaction(static function(array &$store) use ($items, $selected): array {
            // Retain completed send receipts for idempotency; never hide a job
            // that could still send an SMS after its chat was deleted.
            foreach ($store['jobs'] as $job) {
                if (isset($selected[sms_chat_id(sms_chat_outgoing_item($job))]) && in_array($job['status'], ['queued','sending'], true)) {
                    throw new InvalidArgumentException('Дождитесь отправки СМС в выбранных чатах.');
                }
            }
            $removed = []; $remaining = [];
            foreach ($items as $item) {
                $id = sms_chat_id($item);
                if (isset($selected[$id])) $removed[$id] = true;
                else $remaining[] = $item;
            }
            foreach ($store['jobs'] as &$job) {
                $id = sms_chat_id(sms_chat_outgoing_item($job));
                if (isset($selected[$id]) && empty($job['chat_deleted'])) {
                    $job['chat_deleted'] = true; $removed[$id] = true;
                }
            }
            unset($job);
            if (count($remaining) !== count($items)) write_event_store(SMS_JSON_PATH, $remaining);
            return ['deleted_ids'=>array_keys($removed), 'recording_cleanup_failed'=>0];
        });
    });
}

function sms_chat_context(array $query): array
{
    $state = sms_outbox_state();
    $item = null;
    $senderSelectable = false;
    if (!empty($query['id'])) {
        $item = sms_chat_record((string) $query['id']);
        if ($item === null) throw new InvalidArgumentException('СМС не найдена.');
        $number = sms_chat_number((string) ($item['number'] ?? ''));
        $local = sms_chat_number((string) ($item['local_number'] ?? ''));
        $port = null;
        foreach ($state['ports'] as $candidate) {
            // A port may contain a different SIM now. Never silently reply from it.
            if ($local !== '' && sms_chat_number($candidate['number']) === $local) $port = $candidate;
        }
        // A known SIM defines this chat even when disconnected. Selecting a
        // different SIM would silently combine two independent conversations.
        $senderSelectable = $local === '';
        if ($senderSelectable && (string) ($query['sender'] ?? '') !== '') {
            foreach ($state['ports'] as $candidate) {
                if ((string) $candidate['port'] === (string) $query['sender']) $port = $candidate;
            }
            if ($port === null) throw new InvalidArgumentException('Выбранная SIM недоступна.');
            // Select the reply sender without inventing the original receiving SIM.
            $local = (string) $port['number'];
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
        'sender_selectable'=>$senderSelectable,
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
        $originalChat = $context['item'] !== null && sms_chat_id($item) === sms_chat_id($context['item']);
        if (!$same && !$originalChat) continue;
        $messages[] = ['id'=>'in-'.$item['id'], 'direction'=>'incoming', 'text'=>$item['text'],
            'timestamp'=>$item['timestamp'], 'status'=>'received'];
    }
    $outgoing = sms_outbox_transaction(static function(array &$store) use ($context): array {
        return array_values(array_filter($store['jobs'], static fn(array $job): bool =>
            empty($job['chat_deleted']) && $context['local_number'] !== '' && sms_chat_number($job['number']) === $context['number']
            && sms_chat_number($job['sender']) === $context['local_number']));
    });
    foreach ($outgoing as $job) {
        $messages[] = ['id'=>'out-'.$job['id'], 'direction'=>'outgoing', 'text'=>$job['text'],
            'timestamp'=>$job['created_at'], 'status'=>$job['status'], 'message'=>$job['message']];
    }
    $tz = new DateTimeZone('Europe/Moscow');
    foreach ($messages as &$message) {
        $message['display_timestamp'] = '—';
        $message['_sort_timestamp'] = 0;
        $timestamp = trim((string) ($message['timestamp'] ?? ''));
        if ($timestamp === '') continue;
        try {
            $date = (new DateTimeImmutable($timestamp, $tz))->setTimezone($tz);
            $message['display_timestamp'] = $date->format('H:i:s d.m.Y');
            $message['_sort_timestamp'] = $date->getTimestamp();
        } catch (Exception $e) {
            // One malformed historical timestamp must not break the chat.
        }
    }
    unset($message);
    usort($messages, static fn($a, $b) => $a['_sort_timestamp'] <=> $b['_sort_timestamp']);
    foreach ($messages as &$message) unset($message['_sort_timestamp']);
    unset($message);
    unset($context['item']);
    return $context + ['messages'=>$messages];
}

function sms_reply_payload(array $data): array
{
    if (empty($data['reply_to'])) return $data;
    if (!is_string($data['reply_to'])) throw new InvalidArgumentException('Некорректная карточка СМС.');
    $context = sms_chat_context(['id'=>$data['reply_to'], 'sender'=>$data['sender'] ?? '']);
    if (!$context['can_reply']) throw new InvalidArgumentException('Выберите SIM для ответа. Ответ на текстовое имя отправителя недоступен.');
    if ((string) ($data['sender'] ?? '') !== (string) $context['sim_port']
        || sms_chat_number((string) ($data['number'] ?? '')) !== $context['number']) {
        throw new InvalidArgumentException('SIM и получатель ответа должны совпадать с карточкой СМС.');
    }
    return array_replace($data, ['sender'=>(string) $context['sim_port'], 'number'=>$context['number']]);
}

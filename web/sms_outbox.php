<?php
declare(strict_types=1);
require_once __DIR__ . '/common.php';
require_once __DIR__ . '/sms_outbox_store.php';
require_api_authentication();
try {
    if ($_SERVER['REQUEST_METHOD'] === 'GET') {
        json_response(['ok'=>true] + sms_outbox_state());
    }
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        header('Allow: GET, POST');
        json_response(['ok'=>false, 'message'=>'Method not allowed.'], 405);
    }
    $data = get_json_request_body();
    switch ($data['action'] ?? '') {
        case 'heartbeat': json_response(sms_outbox_heartbeat($data)); break;
        case 'claim': json_response(['ok'=>true] + sms_outbox_claim()); break;
        case 'complete': json_response(['ok'=>true] + sms_outbox_complete($data)); break;
        case 'enqueue':
            if (!in_array($data['source'] ?? null, ['telegram','email'], true)) throw new InvalidArgumentException('Invalid source.');
            json_response(['ok'=>true,'job'=>sms_outbox_enqueue($data, $data['source'])]); break;
        default: throw new InvalidArgumentException('Unknown action.');
    }
} catch (InvalidArgumentException $error) {
    json_response(['ok'=>false,'message'=>$error->getMessage()], 400);
} catch (RuntimeException $error) {
    json_response(['ok'=>false,'message'=>$error->getMessage()], 500);
}

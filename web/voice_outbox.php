<?php
declare(strict_types=1);
require_once __DIR__ . '/common.php';
require_once __DIR__ . '/voice_outbox_store.php';
require_api_authentication();
try {
    if ($_SERVER['REQUEST_METHOD'] === 'GET') {
        if (isset($_GET['audio'])) voice_audio_response((string) $_GET['audio']);
        json_response(['ok'=>true] + voice_state());
    }
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') json_response(['ok'=>false], 405);
    $data = strpos($_SERVER['CONTENT_TYPE'] ?? '', 'multipart/form-data') === 0 ? $_POST : get_json_request_body();
    if (($data['action'] ?? '') === 'enqueue') {
        json_response(['ok'=>true, 'job'=>voice_enqueue($data, 'telegram', voice_uploaded_audio())]);
    }
    if (($data['action'] ?? '') === 'cancel') json_response(['ok'=>true] + voice_cancel((string) ($data['id'] ?? '')));
    json_response(['ok'=>true] + voice_worker_action($data));
} catch (InvalidArgumentException $error) {
    json_response(['ok'=>false, 'message'=>$error->getMessage()], 400);
} catch (RuntimeException $error) {
    json_response(['ok'=>false, 'message'=>$error->getMessage()], 500);
}

<?php
declare(strict_types=1);
require_once __DIR__ . '/common.php';
require_api_authentication();
require_once __DIR__ . '/event_metadata_store.php';
header('Cache-Control: no-store');
try {
    $kind = $_GET['kind'] ?? '';
    if (!in_array($kind, ['calls','sms'], true)) throw new InvalidArgumentException('Invalid event kind.');
    if ($_SERVER['REQUEST_METHOD'] === 'GET') json_response(['ok'=>true,'records'=>event_metadata_snapshot($kind)]);
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') json_response(['ok'=>false], 405);
    $body = json_decode(file_get_contents('php://input'), true, 512, JSON_THROW_ON_ERROR);
    if (!is_array($body['updates'] ?? null)) throw new InvalidArgumentException('Expected updates.');
    json_response(['ok'=>true] + update_event_metadata($kind, $body['updates']));
} catch (InvalidArgumentException | JsonException $error) {
    json_response(['ok'=>false,'message'=>$error->getMessage()],400);
} catch (Throwable $error) {
    error_log('Event metadata update failed: ' . get_class($error));
    json_response(['ok'=>false,'message'=>'Не удалось обновить данные.'],500);
}

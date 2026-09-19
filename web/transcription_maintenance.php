<?php
declare(strict_types=1);
require_once __DIR__ . '/common.php';
require_api_authentication();
require_once __DIR__ . '/transcription_maintenance_store.php';
header('Cache-Control: no-store');

try {
    if ($_SERVER['REQUEST_METHOD'] === 'GET') {
        $id = $_GET['audio_id'] ?? null;
        if ($id === null) json_response(['ok' => true, 'calls' => transcription_snapshot()]);
        if (!is_string($id)) throw new InvalidArgumentException('Invalid call ID.');
        with_event_store_lock(CALLS_JSON_PATH, static function (array $items) use ($id): void {
            foreach ($items as $record) {
                if (($record['id'] ?? null) !== $id) continue;
                $path = transcription_recording_path($record);
                if ($path === null) break;
                header('Content-Type: audio/wav');
                header('Content-Length: ' . filesize($path));
                readfile($path);
                return;
            }
            json_response(['ok' => false, 'message' => 'Recording not found.'], 404);
        });
        exit;
    }
    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        $body = json_decode(file_get_contents('php://input'), true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($body) || !is_array($body['updates'] ?? null)) throw new InvalidArgumentException('Expected updates.');
        json_response(['ok' => true] + replace_call_transcriptions($body['updates']));
    }
    header('Allow: GET, POST');
    json_response(['ok' => false, 'message' => 'Method not allowed.'], 405);
} catch (InvalidArgumentException | JsonException $error) {
    json_response(['ok' => false, 'message' => $error->getMessage()], 400);
} catch (Throwable $error) {
    error_log('Transcription maintenance failed: ' . get_class($error));
    // This administrative API is authenticated before any operation. Return the
    // diagnostic to the operator without exposing a stack trace or credentials.
    json_response(['ok' => false, 'message' => $error->getMessage(), 'error_type' => get_class($error)], 500);
}

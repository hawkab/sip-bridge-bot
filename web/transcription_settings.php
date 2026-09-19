<?php
declare(strict_types=1);
require_once __DIR__ . '/common.php';
require_once __DIR__ . '/transcription_settings_store.php';

// Bot reads only. Writes require an authenticated UI session and CSRF token.
require_api_authentication();
if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    header('Allow: GET');
    json_response(['ok' => false, 'message' => 'Method not allowed.'], 405);
}
try {
    json_response(['ok' => true, 'settings' => read_transcription_settings()]);
} catch (RuntimeException $error) {
    json_response(['ok' => false, 'message' => $error->getMessage()], 500);
}

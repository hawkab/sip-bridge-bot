<?php
declare(strict_types=1);
require_once __DIR__ . '/common.php';
require_once __DIR__ . '/transcription_test_store.php';
require_api_authentication();
try {
    if ($_SERVER['REQUEST_METHOD'] === 'GET' && isset($_GET['audio'])) {
        $id = (string) $_GET['audio'];
        if (!preg_match('/^[a-f0-9]{32}$/D', $id)) json_response(['ok'=>false], 404);
        $path = DATA_STORAGE_DIR . '/transcription-tests/' . $id . '.audio';
        if (!is_file($path)) json_response(['ok'=>false], 404);
        header('Content-Type: application/octet-stream'); header('Cache-Control: no-store');
        header('Content-Length: ' . filesize($path)); readfile($path); exit;
    }
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') json_response(['ok'=>false], 405);
    json_response(['ok'=>true] + asr_test_worker(get_json_request_body()));
} catch (InvalidArgumentException $e) { json_response(['ok'=>false, 'message'=>$e->getMessage()], 400);
} catch (RuntimeException $e) { json_response(['ok'=>false, 'message'=>$e->getMessage()], 500); }

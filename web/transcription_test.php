<?php
declare(strict_types=1);
require_once __DIR__ . '/common.php';
require_once __DIR__ . '/transcription_settings_store.php';
require_once __DIR__ . '/transcription_test_store.php';
ensure_access_or_404();
header('Cache-Control: no-store');
header('Referrer-Policy: same-origin');
$action = (string) ($_GET['action'] ?? '');
if ($action !== '') {
    require_user_authenticated_json();
    try {
        if ($action === 'status' && $_SERVER['REQUEST_METHOD'] === 'GET') json_response(['ok'=>true] + asr_test_get((string) ($_GET['id'] ?? '')));
        if ($action !== 'enqueue' || $_SERVER['REQUEST_METHOD'] !== 'POST') json_response(['ok'=>false], 405);
        if (!is_string($_POST['csrf_token'] ?? null) || !hash_equals(transcription_settings_csrf_token(), $_POST['csrf_token'])) json_response(['ok'=>false, 'message'=>'Обновите страницу и повторите проверку.'], 403);
        $upload = $_FILES['audio'] ?? null;
        if (!is_array($upload) || ($upload['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_OK || !is_uploaded_file($upload['tmp_name']) || $upload['size'] > ASR_TEST_MAX_BYTES) throw new InvalidArgumentException('Файл не загружен или превышает допустимый размер.');
        $audio = file_get_contents($upload['tmp_name']);
        if ($audio === false) throw new RuntimeException('Не удалось прочитать запись.');
        json_response(['ok'=>true] + asr_test_enqueue($_POST, $audio));
    } catch (InvalidArgumentException $e) { json_response(['ok'=>false, 'message'=>$e->getMessage()], 400);
    } catch (RuntimeException $e) { json_response(['ok'=>false, 'message'=>$e->getMessage()], 500); }
}
if (!is_user_authenticated()) { header('Location: ' . get_app_url(true)); exit; }
$backend = $_GET['backend'] ?? read_transcription_settings()['backend'];
if (!in_array($backend, ['gigaam','whisper'], true)) $backend = 'gigaam';
function asr_ini_bytes(string $value): int {
    $value = trim($value); $number = (float) $value;
    return (int) ($number * (1024 ** (['k'=>1,'m'=>2,'g'=>3][strtolower(substr($value, -1))] ?? 0)));
}
$maxBytes = ASR_TEST_MAX_BYTES;
foreach (['upload_max_filesize','post_max_size'] as $setting) {
    $limit = asr_ini_bytes((string) ini_get($setting));
    if ($limit > 0) $maxBytes = min($maxBytes, max(0, $limit-8192));
}
$config = ['backend'=>$backend, 'csrf'=>transcription_settings_csrf_token(), 'maxBytes'=>$maxBytes];
session_write_close();
?>
<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Проверка транскрибации</title>
<link rel="stylesheet" href="transcription_test.css?v=<?= hash_file('sha256', __DIR__ . '/transcription_test.css') ?>">
</head><body>
<main class="tester">
    <a class="back" href="<?= htmlspecialchars(get_app_url(false, ['page'=>'settings']), ENT_QUOTES) ?>">← Настройки</a>
    <h1>Проверка транскрибации</h1>
    <section class="panel">
        <label for="engine">Движок</label>
        <select id="engine"><option value="gigaam">GigaAM</option><option value="whisper">faster-whisper</option></select>
        <div class="record-row"><button id="record" type="button">● Записать голос</button><span id="timer" role="status" aria-live="off"></span></div>
        <div id="dropzone" class="dropzone">
            <span class="drop-hint">Перетащите аудио сюда</span>
            <label class="file-button" for="audioFile">Выбрать файл</label>
            <input id="audioFile" type="file" accept="audio/*,.wav,.mp3,.m4a,.ogg,.webm,.flac,.aac,.opus">
            <span class="hint">До <?= htmlspecialchars((string) (floor($maxBytes/1048576*10)/10)) ?> МБ</span>
        </div>
        <div id="preview" hidden><p id="filename"></p><audio id="player" controls preload="metadata"></audio></div>
        <button id="transcribe" class="primary" type="button" disabled>Распознать</button>
        <p id="status" role="status" aria-live="polite" hidden></p>
        <p id="error" role="alert" hidden></p>
    </section>
    <section id="result" class="panel" aria-labelledby="resultTitle" hidden>
        <div class="result-heading"><h2 id="resultTitle">Результат</h2><button id="copy" type="button">Скопировать</button></div>
        <p id="resultMeta" class="hint"></p><div id="resultText"></div>
    </section>
</main>
<script id="testConfig" type="application/json"><?= json_encode($config, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT) ?></script>
<script src="transcription_test.js?v=<?= hash_file('sha256', __DIR__ . '/transcription_test.js') ?>" defer></script>
</body></html>

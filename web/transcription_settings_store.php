<?php
declare(strict_types=1);

function read_transcription_settings(): array
{
    $path = DATA_STORAGE_DIR . '/transcription-settings.json';
    if (!is_file($path)) {
        return ['backend' => 'gigaam', 'updated_at' => null];
    }
    $settings = json_decode((string) file_get_contents($path), true);
    if (!is_array($settings) || !in_array($settings['backend'] ?? null, ['whisper', 'gigaam'], true)) {
        throw new RuntimeException('Не удалось прочитать настройки распознавания.');
    }
    return ['backend' => $settings['backend'], 'updated_at' => $settings['updated_at'] ?? null];
}

function save_transcription_settings(string $backend): array
{
    if (!in_array($backend, ['whisper', 'gigaam'], true)) {
        throw new InvalidArgumentException('Выберите faster-whisper или GigaAM.');
    }
    ensure_storage_layout();
    $settings = ['backend' => $backend, 'updated_at' => date(DATE_ATOM)];
    $temporary = tempnam(DATA_STORAGE_DIR, '.transcription-settings-');
    if ($temporary === false) {
        throw new RuntimeException('Не удалось сохранить настройки.');
    }
    try {
        $encoded = json_encode($settings, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
        if (file_put_contents($temporary, $encoded . PHP_EOL, LOCK_EX) === false
            || !rename($temporary, DATA_STORAGE_DIR . '/transcription-settings.json')) {
            throw new RuntimeException('Не удалось сохранить настройки.');
        }
    } finally {
        if (is_file($temporary)) {
            unlink($temporary);
        }
    }
    return $settings;
}

function transcription_settings_csrf_token(): string
{
    start_app_session();
    if (empty($_SESSION['transcription_settings_csrf'])) {
        $_SESSION['transcription_settings_csrf'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['transcription_settings_csrf'];
}

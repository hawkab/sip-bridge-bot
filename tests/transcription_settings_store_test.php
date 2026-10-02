<?php
declare(strict_types=1);
define('DATA_STORAGE_DIR',sys_get_temp_dir().'/asr-settings-'.bin2hex(random_bytes(8)));
function ensure_storage_layout(): void { if (!is_dir(DATA_STORAGE_DIR)) mkdir(DATA_STORAGE_DIR,0700); }
require __DIR__.'/../web/transcription_settings_store.php';
try {
    foreach (['tone','nemotron','whisper','gigaam'] as $backend) {
        save_transcription_settings($backend);
        if (read_transcription_settings()['backend']!==$backend) throw new RuntimeException('Selection not persisted');
    }
    try { save_transcription_settings('unknown'); throw new RuntimeException('Invalid backend accepted'); }
    catch (InvalidArgumentException $expected) {}
    if (read_transcription_settings()['backend']!=='gigaam') throw new RuntimeException('Invalid selection changed settings');
    echo "PASS: all engines persist; invalid selection preserves settings\n";
} finally {
    foreach (glob(DATA_STORAGE_DIR.'/*') ?: [] as $file) unlink($file);
    if (is_dir(DATA_STORAGE_DIR)) rmdir(DATA_STORAGE_DIR);
}

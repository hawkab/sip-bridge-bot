<?php
declare(strict_types=1);

const APP_SESSION_LIFETIME = 30 * 24 * 60 * 60;

function send_app_session_cookie(): void
{
    $params = session_get_cookie_params();
    if (!setcookie(session_name(), session_id(), [
        'expires' => $_SESSION['app_session_expires_at'],
        'path' => $params['path'],
        'domain' => $params['domain'],
        'secure' => $params['secure'],
        'httponly' => true,
        'samesite' => 'Lax',
    ])) {
        throw new RuntimeException('Не удалось сохранить cookie сессии.');
    }
}

function start_persistent_app_session(): void
{
    if (session_status() === PHP_SESSION_ACTIVE) return;

    // Shared hosting may clean its default directory using another site's short
    // gc_maxlifetime. Keep these sessions inside the existing protected storage.
    $directory = DATA_STORAGE_DIR . '/sessions';
    if (!is_dir($directory) && !mkdir($directory, 0700, true) && !is_dir($directory)) {
        throw new RuntimeException('Не удалось создать хранилище сессий.');
    }
    if (!is_writable($directory)) {
        throw new RuntimeException('Хранилище сессий недоступно для записи.');
    }
    $settings = [
        'session.save_handler' => 'files',
        'session.save_path' => $directory,
        'session.gc_maxlifetime' => (string) APP_SESSION_LIFETIME,
        'session.gc_probability' => '1',
        'session.gc_divisor' => '100',
        'session.use_strict_mode' => '1',
        'session.use_only_cookies' => '1',
        'session.use_cookies' => '1',
        'session.use_trans_sid' => '0',
    ];
    foreach ($settings as $key => $value) {
        if (ini_set($key, $value) === false) {
            throw new RuntimeException('Не удалось настроить хранилище сессий.');
        }
    }
    session_name('sip_pwa_session');
    session_set_cookie_params([
        'lifetime' => APP_SESSION_LIFETIME,
        'path' => '/',
        'domain' => '',
        'httponly' => true,
        'samesite' => 'Lax',
        'secure' => !empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off',
    ]);
    if (!session_start()) {
        throw new RuntimeException('Не удалось открыть сессию.');
    }
    $now = time();
    $expires = $_SESSION['app_session_expires_at'] ?? null;
    if ($expires !== null && (!is_int($expires) || $expires <= $now)) {
        // Enforce the deadline even if GC has not run or a client replays an
        // expired cookie. Polling must not extend authenticated access forever.
        $_SESSION = [];
        if (!session_regenerate_id(true)) {
            throw new RuntimeException('Не удалось обновить сессию.');
        }
        $expires = null;
    }
    if ($expires === null) $_SESSION['app_session_expires_at'] = $now + APP_SESSION_LIFETIME;
    send_app_session_cookie();
}

function complete_app_login(): void
{
    start_persistent_app_session();
    // A successful password check starts a fresh 30-day period and invalidates
    // the anonymous session ID. Call only after verifying the password.
    if (!session_regenerate_id(true)) {
        throw new RuntimeException('Не удалось обновить сессию.');
    }
    $_SESSION['user_authenticated'] = true;
    $_SESSION['app_session_expires_at'] = time() + APP_SESSION_LIFETIME;
    send_app_session_cookie();
}

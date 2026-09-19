<?php
declare(strict_types=1);

// No production credentials needed: php tests/web_session_test.php
if (isset($argv[1])) {
    define('DATA_STORAGE_DIR', $argv[2]);
    $_SERVER['HTTPS'] = 'on';
    if (!empty($argv[3])) $_COOKIE['sip_pwa_session'] = $argv[3];
    ini_set('session.gc_maxlifetime', '1440');
    require __DIR__ . '/../web/app_session.php';
    start_persistent_app_session();
    if ($argv[1] === 'login') complete_app_login();
    if ($argv[1] === 'expire') $_SESSION['app_session_expires_at'] = time() - 1;
    if ($argv[1] === 'gc') session_gc();
    $result = ['id'=>session_id(), 'authenticated'=>!empty($_SESSION['user_authenticated']),
        'expires'=>$_SESSION['app_session_expires_at'], 'params'=>session_get_cookie_params(),
        'gc'=>(int) ini_get('session.gc_maxlifetime'), 'path'=>session_save_path()];
    if ($argv[1] === 'logout') {
        $_SESSION = [];
        session_destroy();
    } else {
        session_write_close();
    }
    echo json_encode($result, JSON_THROW_ON_ERROR);
    exit;
}

function check(bool $ok, string $message): void {
    if (!$ok) throw new RuntimeException($message);
}
function request_session(string $root, string $action, string $id = ''): array {
    $process = proc_open([PHP_BINARY, __FILE__, $action, $root, $id],
        [1=>['pipe','w'], 2=>['pipe','w']], $pipes);
    $output = stream_get_contents($pipes[1]);
    $error = stream_get_contents($pipes[2]);
    fclose($pipes[1]); fclose($pipes[2]);
    check(proc_close($process) === 0 && $error === '', 'Session worker failed: ' . $error);
    return json_decode($output, true, 512, JSON_THROW_ON_ERROR);
}
$root = sys_get_temp_dir() . '/sip-session-test-' . bin2hex(random_bytes(6));
mkdir($root, 0700);
try {
    $anonymous = request_session($root, 'read', str_repeat('a', 32));
    check($anonymous['id'] !== str_repeat('a', 32), 'Reject uninitialized client session ID');
    check(!$anonymous['authenticated'], 'Anonymous requests stay unauthenticated');
    $login = request_session($root, 'login', $anonymous['id']);
    check($login['authenticated'] && $login['id'] !== $anonymous['id'], 'Rotate ID after password verification');
    check(!is_file($root . '/sessions/sess_' . $anonymous['id']), 'Invalidate old anonymous ID');
    check(abs($login['expires'] - time() - 2592000) < 5, 'Absolute 30-day login deadline');
    check($login['gc'] === 2592000 && $login['params']['lifetime'] === 2592000, 'Both server storage and cookie last 30 days');
    check($login['params']['secure'] && $login['params']['httponly'] && $login['params']['samesite'] === 'Lax', 'Cookie security preserved');
    check($login['path'] === $root . '/sessions', 'Use isolated private session storage');
    $sessionFile = $root . '/sessions/sess_' . $login['id'];
    check((fileperms($root . '/sessions') & 0777) === 0700 && (fileperms($sessionFile) & 0777) === 0600, 'Private storage permissions');
    touch($sessionFile, time() - 29 * 86400);
    $reopened = request_session($root, 'gc', $login['id']);
    check($reopened['authenticated'] && $reopened['id'] === $login['id'], 'Session survives 29 days and GC');
    check($reopened['expires'] === $login['expires'], 'Polling cannot silently extend the deadline');
    request_session($root, 'expire', $login['id']);
    $expired = request_session($root, 'read', $login['id']);
    check(!$expired['authenticated'] && $expired['id'] !== $login['id'], 'Replayed expired session is rejected before GC');
    $again = request_session($root, 'login', $expired['id']);
    request_session($root, 'logout', $again['id']);
    check(!request_session($root, 'read', $again['id'])['authenticated'], 'Logout invalidates replay of the old cookie');
    $obsolete = $root . '/sessions/sess_' . str_repeat('b', 32);
    file_put_contents($obsolete, '');
    touch($obsolete, time() - 31 * 86400);
    request_session($root, 'gc');
    check(!is_file($obsolete), 'GC removes abandoned sessions older than 30 days');
    echo "PASS: 30-day cookie/storage, secure flags, strict IDs, login rotation, 29-day persistence, fixed expiry, logout and GC\n";
} finally {
    foreach (glob($root . '/sessions/*') as $file) unlink($file);
    if (is_dir($root . '/sessions')) rmdir($root . '/sessions');
    rmdir($root);
}

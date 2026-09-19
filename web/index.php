<?php
declare(strict_types=1);

require_once __DIR__ . '/common.php';
require_once __DIR__ . '/transcription_settings_store.php';

ensure_access_or_404();

$action = isset($_GET['action']) ? trim((string) $_GET['action']) : '';

if ($action === 'manifest') {
    $manifest = [
        'id' => get_app_url(false),
        'name' => 'HANS WAKABA SIP CLIENT',
        'short_name' => 'WAKABA SIP',
        'description' => 'Просмотр звонков, СМС и push-уведомления о новых событиях.',
        'start_url' => get_app_url(true),
        'scope' => get_base_url() . '/',
        'display' => 'standalone',
        'display_override' => ['standalone', 'minimal-ui', 'browser'],
        'background_color' => '#f8f9fa',
        'theme_color' => '#0d6efd',
        'lang' => 'ru',
        'orientation' => 'portrait-primary',
        'icons' => [
            [
                'src' => 'icons/192-any.png',
                'sizes' => '192x192',
                'type' => 'image/png',
                'purpose' => 'any',
            ],
            [
                'src' => 'icons/512-any.png',
                'sizes' => '512x512',
                'type' => 'image/png',
                'purpose' => 'any',
            ],
            [
                'src' => 'icons/192-mask.png',
                'sizes' => '192x192',
                'type' => 'image/png',
                'purpose' => 'maskable',
            ],
            [
                'src' => 'icons/512-mask.png',
                'sizes' => '512x512',
                'type' => 'image/png',
                'purpose' => 'maskable',
            ],
        ],
    ];

    http_response_code(200);
    header('Content-Type: application/manifest+json; charset=utf-8');
    header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
    echo json_encode($manifest, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
    exit;
}

if ($action === 'login' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $data = get_json_request_body();
    $login = trim((string) ($data['login'] ?? ''));
    $password = (string) ($data['password'] ?? '');

    if (hash_equals(APP_LOGIN, $login) && password_verify($password, APP_PASSWORD_HASH)) {
        start_app_session();
        $_SESSION['user_authenticated'] = true;
        json_response([
            'ok' => true,
            'authenticated' => true,
        ]);
    }

    json_response([
        'ok' => false,
        'message' => 'Неверный логин или пароль.',
    ], 401);
}

if ($action === 'logout' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    logout_user();
    json_response([
        'ok' => true,
    ]);
}

if ($action === 'session') {
    json_response([
        'ok' => true,
        'authenticated' => is_user_authenticated(),
    ]);
}

if ($action !== '') {
    require_user_authenticated_json();

    switch ($action) {
        case 'get_transcription_settings':
            try {
                json_response(['ok' => true, 'settings' => read_transcription_settings(),
                    'csrf_token' => transcription_settings_csrf_token()]);
            } catch (RuntimeException $error) {
                json_response(['ok' => false, 'message' => $error->getMessage()], 500);
            }
            break;

        case 'save_transcription_settings':
            if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
                header('Allow: POST');
                json_response(['ok' => false, 'message' => 'Требуется POST-запрос.'], 405);
            }
            $data = get_json_request_body();
            if (!is_string($data['csrf_token'] ?? null)
                || !hash_equals(transcription_settings_csrf_token(), $data['csrf_token'])) {
                json_response(['ok' => false, 'message' => 'Обновите страницу настроек и повторите сохранение.'], 403);
            }
            if (!is_string($data['backend'] ?? null)) {
                json_response(['ok' => false, 'message' => 'Выберите движок распознавания.'], 400);
            }
            try {
                json_response(['ok' => true, 'settings' => save_transcription_settings($data['backend'])]);
            } catch (InvalidArgumentException $error) {
                json_response(['ok' => false, 'message' => $error->getMessage()], 400);
            } catch (RuntimeException $error) {
                json_response(['ok' => false, 'message' => $error->getMessage()], 500);
            }
            break;

        case 'list_calls':
            $items = read_json_array(CALLS_JSON_PATH);
            $items = filter_by_number($items, (string) ($_GET['number'] ?? ''));
            $items = sort_items(
                $items,
                (string) ($_GET['sortBy'] ?? 'timestamp'),
                (string) ($_GET['sortDirection'] ?? 'desc')
            );
            $paginated = paginate_items(
                $items,
                (int) ($_GET['page'] ?? 1),
                (int) ($_GET['pageSize'] ?? 10)
            );

            foreach ($paginated['items'] as &$item) {
                $recordingFile = basename((string) ($item['recording_file'] ?? ''));
                $item['hasRecording'] = $recordingFile !== '' && is_file(RECORDINGS_DIR . '/' . $recordingFile);
                $item['downloadUrl'] = $item['hasRecording']
                    ? APP_ENTRY_SCRIPT . '?action=download_recording&file=' . rawurlencode($recordingFile)
                    : null;
                $item = decorate_call_item_for_ui($item);
            }
            unset($item);

            json_response([
                'ok' => true,
                'items' => $paginated['items'],
                'meta' => $paginated['meta'],
            ]);
            break;

        case 'list_sms':
            $items = read_json_array(SMS_JSON_PATH);
            $items = filter_by_number($items, (string) ($_GET['number'] ?? ''));
            $items = sort_items(
                $items,
                (string) ($_GET['sortBy'] ?? 'timestamp'),
                (string) ($_GET['sortDirection'] ?? 'desc')
            );
            $paginated = paginate_items(
                $items,
                (int) ($_GET['page'] ?? 1),
                (int) ($_GET['pageSize'] ?? 10)
            );

            foreach ($paginated['items'] as &$item) {
                $item['preview'] = sms_preview((string) ($item['text'] ?? ''), 100);
                $item = decorate_sms_item_for_ui($item);
            }
            unset($item);

            json_response([
                'ok' => true,
                'items' => $paginated['items'],
                'meta' => $paginated['meta'],
            ]);
            break;

        case 'get_event':
            $view = trim((string) ($_GET['view'] ?? ''));
            $id = trim((string) ($_GET['id'] ?? ''));

            if ($view === '' || $id === '') {
                json_response([
                    'ok' => false,
                    'message' => 'view and id are required.',
                ], 400);
            }

            $path = null;
            switch ($view) {
                case 'call':
                    $path = CALLS_JSON_PATH;
                    break;
                case 'sms':
                    $path = SMS_JSON_PATH;
                    break;
                case 'error':
                    $path = ERRORS_JSON_PATH;
                    break;
                default:
                    json_response([
                        'ok' => false,
                        'message' => 'Unknown view.',
                    ], 404);
            }

            $item = find_record_by_id($path, $id);
            if ($item === null) {
                json_response([
                    'ok' => false,
                    'message' => 'Record not found.',
                ], 404);
            }

            if ($view === 'call') {
                $recordingFile = basename((string) ($item['recording_file'] ?? ''));
                $item['hasRecording'] = $recordingFile !== '' && is_file(RECORDINGS_DIR . '/' . $recordingFile);
                $item['downloadUrl'] = $item['hasRecording']
                    ? APP_ENTRY_SCRIPT . '?action=download_recording&file=' . rawurlencode($recordingFile)
                    : null;
                $item = decorate_call_item_for_ui($item);
            } elseif ($view === 'sms') {
                $item['preview'] = sms_preview((string) ($item['text'] ?? ''), 100);
                $item = decorate_sms_item_for_ui($item);
            }

            json_response([
                'ok' => true,
                'view' => $view,
                'item' => $item,
            ]);
            break;

        case 'latest_snapshot':
            $calls = sort_items(read_json_array(CALLS_JSON_PATH), 'timestamp', 'desc');
            $sms = sort_items(read_json_array(SMS_JSON_PATH), 'timestamp', 'desc');

            $calls = array_slice($calls, 0, 20);
            $sms = array_slice($sms, 0, 20);

            foreach ($calls as &$item) {
                $item = decorate_call_item_for_ui($item);
            }
            unset($item);

            foreach ($sms as &$item) {
                $item['preview'] = sms_preview((string) ($item['text'] ?? ''), 100);
                $item = decorate_sms_item_for_ui($item);
            }
            unset($item);

            json_response([
                'ok' => true,
                'calls' => $calls,
                'sms' => $sms,
            ]);
            break;

        case 'download_recording':
            $file = basename((string) ($_GET['file'] ?? ''));
            if ($file === '') {
                not_found();
            }

            $path = RECORDINGS_DIR . '/' . $file;
            if (!is_file($path)) {
                not_found();
            }

            header('Content-Description: File Transfer');
            header('Content-Type: audio/wav');
            header('Content-Disposition: attachment; filename="' . rawurlencode($file) . '"');
            header('Content-Length: ' . (string) filesize($path));
            header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
            readfile($path);
            exit;

        default:
            json_response([
                'ok' => false,
                'message' => 'Unknown action.',
            ], 404);
    }
}

$isAuthenticated = is_user_authenticated();
$appConfig = [
    'entryScript' => APP_ENTRY_SCRIPT,
    'subscribeUrl' => get_base_url() . '/subscribe.php?' . http_build_query([
        APP_ACCESS_PARAM => APP_ACCESS_VALUE,
    ], '', '&', PHP_QUERY_RFC3986),
    'manifestUrl' => get_app_url(true, [
        'action' => 'manifest',
    ]),
    'vapidPublicKey' => VAPID_PUBLIC_KEY,
    'appUrl' => get_app_url(true),
    'notificationPollMs' => 15000,
    'initialView' => isset($_GET['view']) ? trim((string) $_GET['view']) : '',
    'initialId' => isset($_GET['id']) ? trim((string) ($_GET['id'] ?? '')) : '',
];
?>
<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="theme-color" content="#212529">
    <title>Calls / SMS</title>
    <link rel="manifest" href="<?= htmlspecialchars(get_app_url(true, ['action' => 'manifest']), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?>">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background: #f8f9fa;
        }
        .app-shell {
            min-height: 100vh;
        }
        .sidebar {
            width: 240px;
            min-height: 100vh;
        }
        .pointer {
            cursor: pointer;
        }
        .phone-link,
        .sortable-header {
            color: #0d6efd;
            text-decoration: none;
            cursor: pointer;
        }
        .phone-link:hover,
        .sortable-header:hover {
            text-decoration: underline;
        }
        .login-wrapper {
            min-height: 100vh;
        }
        .recording-icon {
            font-size: 1.25rem;
            line-height: 1;
        }
        .header-sort {
            display: inline-flex;
            align-items: center;
            gap: .35rem;
            background: none;
            border: none;
            padding: 0;
            font: inherit;
            color: inherit;
        }
        .sort-indicator {
            font-size: .8rem;
            color: #6c757d;
            min-width: 1rem;
            display: inline-block;
        }
        .table > :not(caption) > * > * {
            vertical-align: middle;
        }
        .recent-row > * {
            background-color: #eaf7ea !important;
        }
        .transcript-thread {
            display: flex;
            flex-direction: column;
            gap: .75rem;
        }
        .transcript-bubble-wrap {
            display: flex;
        }
        .transcript-bubble-wrap.is-right {
            justify-content: flex-end;
        }
        .transcript-bubble {
            max-width: min(78%, 760px);
            border-radius: 1rem;
            padding: .75rem .9rem;
            box-shadow: 0 2px 10px rgba(15, 23, 42, .08);
            border: 1px solid rgba(148, 163, 184, .18);
            background: #f8fafc;
        }
        .transcript-bubble-wrap.is-right .transcript-bubble {
            background: #dbeafe;
        }
        .transcript-meta {
            display: flex;
            flex-wrap: wrap;
            gap: .35rem .5rem;
            font-size: .8rem;
            color: #64748b;
            margin-bottom: .35rem;
            justify-content: space-between;
        }
        .transcript-bubble-wrap.is-right .transcript-meta {
            justify-content: space-between;
        }
        .transcript-speaker {
            font-weight: 700;
            color: #1e293b;
            font-size: 15px;
        }
        .transcript-time {
            white-space: nowrap;
        }
        .transcript-text {
            white-space: pre-wrap;
            color: #0f172a;
            line-height: 1.45;
        }
        .notif-toggle {
            display: inline-flex;
            align-items: center;
            gap: .65rem;
            border: none;
            background: transparent;
            padding: 0;
            color: #334155;
        }
        .notif-toggle:disabled {
            opacity: .6;
        }
        .notif-toggle__label {
            font-size: .875rem;
            color: #475569;
        }
        .notif-toggle__switch {
            position: relative;
            width: 3.25rem;
            height: 1.95rem;
            border-radius: 999px;
            background: #d1d5db;
            box-shadow: inset 0 0 0 1px rgba(15, 23, 42, .08);
            transition: background-color .2s ease;
            flex-shrink: 0;
        }
        .notif-toggle__thumb {
            position: absolute;
            top: 2px;
            left: 2px;
            width: 1.7rem;
            height: 1.7rem;
            border-radius: 50%;
            background: #fff;
            box-shadow: 0 2px 8px rgba(15, 23, 42, .18);
            transition: transform .2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: .9rem;
        }
        .notif-toggle.is-on .notif-toggle__switch {
            background: #34c759;
        }
        .notif-toggle.is-on .notif-toggle__thumb {
            transform: translateX(1.3rem);
        }
        .notif-toggle.is-blocked .notif-toggle__switch {
            background: #f59e0b;
        }
        .pagination-wrap {
            gap: .75rem;
        }
        @media (max-width: 767.98px) {
            .sidebar {
                width: 100%;
                min-height: auto;
            }
            .pagination-wrap {
                flex-direction: column;
                align-items: flex-start !important;
            }
        }
        img.red {
          filter: brightness(0) saturate(100%) invert(14%) sepia(99%) saturate(7480%) hue-rotate(1deg) brightness(101%) contrast(119%);
          width: 16px;
          height: 16px;
        }
        img.green {
          filter: brightness(0) saturate(100%) invert(36%) sepia(98%) saturate(746%) hue-rotate(83deg) brightness(95%) contrast(101%);
          width: 16px;
          height: 16px;
        }
    </style>
</head>
<body>
<div id="app"></div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
(() => {
    const APP_CONFIG = <?= json_encode($appConfig, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) ?>;

    const state = {
        authenticated: <?= $isAuthenticated ? 'true' : 'false' ?>,
        activeMenu: 'calls',
        settings: { backend: 'gigaam', csrfToken: '', loading: true, saving: false, error: '', message: '' },
        calls: {
            items: [],
            search: '',
            sortBy: 'timestamp',
            sortDirection: 'desc',
            page: 1,
            pageSize: 10,
            meta: null,
        },
        sms: {
            items: [],
            search: '',
            sortBy: 'timestamp',
            sortDirection: 'desc',
            page: 1,
            pageSize: 10,
            meta: null,
        },
        knownCallIds: new Set(),
        knownSmsIds: new Set(),
        pollTimer: null,
        pollInFlight: false,
        detailView: APP_CONFIG.initialView || '',
        detailItem: null,
        serviceWorkerListenersBound: false,
        domEventsBound: false,
    };

    const app = document.getElementById('app');

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function getSortIndicator(sortBy, sortDirection, column) {
        if (sortBy !== column) {
            return '↕';
        }
        return sortDirection === 'asc' ? '↑' : '↓';
    }

    function renderTranscriptionThread(items) {
        if (!Array.isArray(items) || items.length === 0) {
            return '<div class="text-muted">Отсутствует</div>';
        }

        const bubbles = items.map((item) => {
            const speaker = escapeHtml(item.speaker || 'SPEAKER');
            const text = escapeHtml(item.text || '');
            const startHms = escapeHtml(item.start_hms || '');
            const endHms = escapeHtml(item.end_hms || '');
            const side = (item.channel || '') === 'right' ? 'is-right' : 'is-left';
            const timeText = startHms && endHms ? `${startHms} - ${endHms}` : '';
            return `
                <div class="transcript-bubble-wrap ${side}">
                    <div class="transcript-bubble">
                        <div class="transcript-meta">
                            <span class="transcript-speaker">${speaker}</span>
                            ${timeText ? `<span class="transcript-time">${timeText}</span>` : ''}
                        </div>
                        <div class="transcript-text">${text}</div>
                    </div>
                </div>
            `;
        }).join('');

        return `<div class="transcript-thread">${bubbles}</div>`;
    }

    function buildActionUrl(action, extra = {}) {
        const url = new URL(window.location.href);
        url.search = '';
        if (action) {
            url.searchParams.set('action', action);
        }
        Object.entries(extra).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') {
                url.searchParams.set(key, String(value));
            }
        });
        return url.toString();
    }

    function getServiceWorkerConfig() {
        return {
            subscribeUrl: APP_CONFIG.subscribeUrl,
            vapidPublicKey: APP_CONFIG.vapidPublicKey,
            appUrl: APP_CONFIG.appUrl,
        };
    }

    async function syncServiceWorkerConfig(registration) {
        if (!registration || !registration.active) {
            return;
        }

        registration.active.postMessage({
            type: 'SET_PWA_CONFIG',
            payload: getServiceWorkerConfig(),
        });
    }

    async function parseResponse(response) {
        const text = await response.text();
        let data = {};

        try {
            data = text ? JSON.parse(text) : {};
        } catch (error) {
            throw new Error('Некорректный ответ сервера.');
        }

        if (!response.ok) {
            throw new Error(data.message || data.error || 'Ошибка запроса.');
        }

        return data;
    }

    async function apiGet(action, extra = {}) {
        const response = await fetch(buildActionUrl(action, extra), {
            credentials: 'same-origin',
            cache: 'no-store',
        });
        return parseResponse(response);
    }

    async function apiPost(action, payload = {}) {
        const response = await fetch(buildActionUrl(action), {
            method: 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });
        return parseResponse(response);
    }

    async function copyToClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            showToast('Номер скопирован');
        } catch (error) {
            showToast('Не удалось скопировать номер', true);
        }
    }

    function showToast(message, isError = false) {
        const toastId = 'appToast';
        let toastContainer = document.getElementById('toastContainer');
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.id = 'toastContainer';
            toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
            document.body.appendChild(toastContainer);
        }

        toastContainer.innerHTML = `
            <div id="${toastId}" class="toast align-items-center text-bg-${isError ? 'danger' : 'success'} border-0" role="alert" aria-live="assertive" aria-atomic="true">
                <div class="d-flex">
                    <div class="toast-body">${escapeHtml(message)}</div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
                </div>
            </div>
        `;

        const toastElement = document.getElementById(toastId);
        const toast = new bootstrap.Toast(toastElement, { delay: 2000 });
        toast.show();
    }

    function cleanupModalArtifacts() {
        document.querySelectorAll('.modal-backdrop').forEach((backdrop) => backdrop.remove());
        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('overflow');
        document.body.style.removeProperty('padding-right');
    }

    function renderLogin() {
        app.innerHTML = `
            <div class="container-fluid login-wrapper d-flex align-items-center justify-content-center">
                <div class="card shadow-sm" style="max-width: 420px; width: 100%;">
                    <div class="card-body p-4">
                        <h1 class="h4 mb-3">Вход</h1>
                        <form id="loginForm">
                            <div class="mb-3">
                                <label class="form-label">Логин</label>
                                <input type="text" name="login" class="form-control" autocomplete="username" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Пароль</label>
                                <input type="password" name="password" class="form-control" autocomplete="current-password" required>
                            </div>
                            <div class="d-grid">
                                <button type="submit" class="btn btn-dark">Войти</button>
                            </div>
                            <div id="loginError" class="text-danger small mt-3 d-none"></div>
                        </form>
                    </div>
                </div>
            </div>
        `;

        document.getElementById('loginForm').addEventListener('submit', async (event) => {
            event.preventDefault();
            const form = event.currentTarget;
            const errorBox = document.getElementById('loginError');
            errorBox.classList.add('d-none');

            try {
                await apiPost('login', {
                    login: form.login.value.trim(),
                    password: form.password.value,
                });
                state.authenticated = true;
                await initializeApp();
            } catch (error) {
                errorBox.textContent = error.message || 'Ошибка входа';
                errorBox.classList.remove('d-none');
            }
        });
    }

    function renderDetailCard() {
        if (!state.detailView || !state.detailItem) {
            return '';
        }

        if (state.detailView === 'call') {
            const item = state.detailItem;
            return `
                <div class="card shadow-sm">
                    <div class="card-body">
                        <h2 class="h5 mb-3">Детали звонка</h2>
                        <dl class="row mb-0">
                            <dt class="col-sm-3">Дата</dt><dd class="col-sm-9">${escapeHtml(item.displayTimestamp || item.timestamp || '')}</dd>
                            <dt class="col-sm-3">Тип</dt><dd class="col-sm-9">${escapeHtml(item.type || '')}</dd>
                            <dt class="col-sm-3">Номер</dt><dd class="col-sm-9"><span class="phone-link" data-phone="${escapeHtml(item.number || '')}">${escapeHtml(item.number || '')}</span></dd>
                            <dt class="col-sm-3">Длительность</dt><dd class="col-sm-9">${escapeHtml(String(item.duration ?? ''))} сек.</dd>
                            <dt class="col-sm-3">Запись</dt>
                            <dd class="col-sm-9">
                                ${item.hasRecording && item.downloadUrl
                                    ? `<audio controls preload="metadata"><source src="${escapeHtml(item.downloadUrl)}" type="audio/wav"><a class="text-decoration-none" href="${escapeHtml(item.downloadUrl)}">Скачать WAV</a></audio>`
                                    : '<span class="text-muted">Отсутствует</span>'}
                            </dd>
                        </dl>
                        <div class="mt-3">
                            <label class="form-label" style="font-weight:700">Транскрибация</label>
                            ${renderTranscriptionThread(item.transcription)}
                        </div>
                    </div>
                </div>
            `;
        }

        if (state.detailView === 'sms') {
            const item = state.detailItem;
            return `
                <div class="card shadow-sm">
                    <div class="card-body">
                        <h2 class="h5 mb-3">Детали СМС</h2>
                        <dl class="row">
                            <dt class="col-sm-3">Дата</dt><dd class="col-sm-9">${escapeHtml(item.displayTimestamp || item.timestamp || '')}</dd>
                            <dt class="col-sm-3">Номер</dt><dd class="col-sm-9"><span class="phone-link" data-phone="${escapeHtml(item.number || '')}">${escapeHtml(item.number || '')}</span></dd>
                            <dt class="col-sm-3">Текст</dt><dd class="col-sm-9"><textarea class="form-control" rows="3" readonly>${escapeHtml(item.text || '')}</textarea></dd>
                        </dl>
                    </div>
                </div>
            `;
        }

        if (state.detailView === 'error') {
            const item = state.detailItem;
            const contextText = item.context ? JSON.stringify(item.context, null, 2) : '';
            return `
                <div class="card shadow-sm border-danger">
                    <div class="card-body">
                        <h2 class="h5 text-danger mb-3">Ошибка сохранения</h2>
                        <dl class="row">
                            <dt class="col-sm-3">Дата</dt><dd class="col-sm-9">${escapeHtml(item.created_at || '')}</dd>
                            <dt class="col-sm-3">Источник</dt><dd class="col-sm-9">${escapeHtml(item.scope || '')}</dd>
                            <dt class="col-sm-3">Сообщение</dt><dd class="col-sm-9">${escapeHtml(item.message || '')}</dd>
                        </dl>
                        <div>
                            <label class="form-label">Контекст</label>
                            <textarea class="form-control" rows="12" readonly>${escapeHtml(contextText)}</textarea>
                        </div>
                    </div>
                </div>
            `;
        }

        return '';
    }

    function isSamePayload(left, right) {
        return JSON.stringify(left ?? null) === JSON.stringify(right ?? null);
    }

    function applyDetailState(view, item) {
        const normalizedItem = item || null;
        const hasChanged = state.detailView !== view || !isSamePayload(state.detailItem, normalizedItem);

        state.detailView = view;
        state.detailItem = normalizedItem;

        if (view === 'sms') {
            state.activeMenu = 'sms';
        } else {
            state.activeMenu = 'calls';
        }

        return hasChanged;
    }

    async function loadDetail(view, id, pushState = false) {
        const response = await apiGet('get_event', { view, id });
        const hasChanged = applyDetailState(view, response.item || null);

        if (pushState) {
            const url = new URL(window.location.href);
            url.searchParams.set('view', view);
            url.searchParams.set('id', id);
            window.history.pushState({}, '', url.toString());
        }

        return hasChanged;
    }

    function clearDetail(pushState = false) {
        state.detailView = '';
        state.detailItem = null;
        if (pushState) {
            const url = new URL(window.location.href);
            url.searchParams.delete('view');
            url.searchParams.delete('id');
            window.history.pushState({}, '', url.toString());
        }
    }

    function renderCallsTable() {
        const sortBy = state.calls.sortBy;
        const sortDirection = state.calls.sortDirection;
        const rows = state.calls.items.map((item) => `
            <tr class="${item.isRecent ? 'recent-row' : ''}">
                <td><span class="phone-link" data-detail-view="call" data-detail-id="${escapeHtml(item.id || '')}">${escapeHtml(item.displayTimestamp || item.timestamp || '')}</span></td>
                <td>${escapeHtml(item.type || '') === 'входящий' ? '<img class="red" src="icons/income_call.png" title="Входящий"x/>' : '<img class="green" src="icons/outcome_call.png" title="Исходящий"/>'}</td>
                <td><span class="phone-link" data-phone="${escapeHtml(item.number || '')}">${escapeHtml(item.number || '')}</span></td>
                <td>${escapeHtml(String(item.duration ?? ''))} сек.</td>
            </tr>
        `).join('');

        return `
            <div class="card shadow-sm">
                <div class="card-body p-0">
                    <div class="table-responsive">
                        <table class="table table-hover align-middle mb-0">
                            <thead class="table-light">
                                <tr>
                                    <th>
                                        <button type="button" class="header-sort sortable-header" data-sort-target="calls" data-sort-by="timestamp">
                                            Дата <span class="sort-indicator">${getSortIndicator(sortBy, sortDirection, 'timestamp')}</span>
                                        </button>
                                    </th>
                                    <th>Вх / исх</th>
                                    <th>
                                        <button type="button" class="header-sort sortable-header" data-sort-target="calls" data-sort-by="number">
                                            Номер <span class="sort-indicator">${getSortIndicator(sortBy, sortDirection, 'number')}</span>
                                        </button>
                                    </th>
                                    <th>Длительность</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${rows || '<tr><td colspan="5" class="text-center text-muted py-4">Нет данных</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            ${renderFooter('calls')}
        `;
    }

    function renderSmsTable() {
        const sortBy = state.sms.sortBy;
        const sortDirection = state.sms.sortDirection;
        const rows = state.sms.items.map((item) => `
            <tr class="${item.isRecent ? 'recent-row' : ''}">
                <td><span class="phone-link" data-detail-view="sms" data-detail-id="${escapeHtml(item.id || '')}">${escapeHtml(item.displayTimestamp || item.timestamp || '')}</span></td>
                <td><span class="phone-link" data-phone="${escapeHtml(item.number || '')}">${escapeHtml(item.number || '')}</span></td>
                <td>${escapeHtml(item.preview || '')}</td>
            </tr>
        `).join('');

        return `
            <div class="card shadow-sm">
                <div class="card-body p-0">
                    <div class="table-responsive">
                        <table class="table table-hover align-middle mb-0">
                            <thead class="table-light">
                                <tr>
                                    <th>
                                        <button type="button" class="header-sort sortable-header" data-sort-target="sms" data-sort-by="timestamp">
                                            Дата <span class="sort-indicator">${getSortIndicator(sortBy, sortDirection, 'timestamp')}</span>
                                        </button>
                                    </th>
                                    <th>
                                        <button type="button" class="header-sort sortable-header" data-sort-target="sms" data-sort-by="number">
                                            Номер <span class="sort-indicator">${getSortIndicator(sortBy, sortDirection, 'number')}</span>
                                        </button>
                                    </th>
                                    <th>Текст</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${rows || '<tr><td colspan="3" class="text-center text-muted py-4">Нет данных</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            ${renderFooter('sms')}
        `;
    }

    function renderPaginationButtons(meta, target) {
        if (!meta) {
            return '';
        }

        const buttons = [];
        const pages = [];
        const start = Math.max(1, meta.page - 2);
        const end = Math.min(meta.totalPages, meta.page + 2);

        for (let page = start; page <= end; page += 1) {
            pages.push(page);
        }

        buttons.push(`
            <button type="button" class="btn btn-sm btn-outline-secondary" data-page-target="${target}" data-page="${Math.max(1, meta.page - 1)}" ${meta.hasPrev ? '' : 'disabled'}>&laquo;</button>
        `);

        pages.forEach((page) => {
            buttons.push(`
                <button type="button" class="btn btn-sm ${page === meta.page ? 'btn-dark' : 'btn-outline-secondary'}" data-page-target="${target}" data-page="${page}">${page}</button>
            `);
        });

        buttons.push(`
            <button type="button" class="btn btn-sm btn-outline-secondary" data-page-target="${target}" data-page="${Math.min(meta.totalPages, meta.page + 1)}" ${meta.hasNext ? '' : 'disabled'}>&raquo;</button>
        `);

        return buttons.join('');
    }

    function renderFooter(target) {
        const source = state[target];
        const meta = source.meta;
        const metaText = meta
            ? `Показано ${source.items.length} из ${meta.total}. Страница ${meta.page} из ${meta.totalPages}.`
            : '';

        return `
            <div class="d-flex justify-content-between align-items-center mt-3 pagination-wrap">
                <div class="text-secondary small">${escapeHtml(metaText)}</div>
                <div class="d-flex align-items-center gap-2 flex-wrap">
                    <label class="small text-secondary" for="${target}PageSize">На странице</label>
                    <select id="${target}PageSize" class="form-select form-select-sm" style="width: auto;">
                        <option value="10" ${source.pageSize === 10 ? 'selected' : ''}>10</option>
                        <option value="25" ${source.pageSize === 25 ? 'selected' : ''}>25</option>
                        <option value="50" ${source.pageSize === 50 ? 'selected' : ''}>50</option>
                    </select>
                    <div class="btn-group" role="group" aria-label="Пагинация">
                        ${renderPaginationButtons(meta, target)}
                    </div>
                </div>
            </div>
        `;
    }

    function renderToolbar(target) {
        if (target === 'settings') return '';
        if (state.detailView && state.detailItem) {
            return `
                <div class="d-flex flex-wrap gap-2 align-items-center mb-3">
                    <button id="backToListBtnTop" class="btn btn-outline-secondary" type="button">← Назад к списку</button>
                </div>
            `;
        }

        const source = state[target];
        return `
            <div class="d-flex flex-wrap gap-2 align-items-center mb-3">
                <input id="${target}Search" class="form-control" style="max-width: 320px;" placeholder="Поиск по номеру" value="${escapeHtml(source.search)}">
                <button id="${target}SearchBtn" class="btn btn-outline-secondary" type="button">Найти</button>
                <button id="${target}ClearBtn" class="btn btn-outline-secondary" type="button">Сбросить</button>
                <button id="${target}ReloadBtn" class="btn btn-outline-secondary ms-auto" type="button">Обновить</button>
            </div>
        `;
    }

    function renderMainContent() {
        if (state.activeMenu === 'settings') return renderSettings();
        return state.detailView && state.detailItem
            ? renderDetailCard()
            : (state.activeMenu === 'calls' ? renderCallsTable() : renderSmsTable());
    }

    function refreshMainArea(options = {}) {
        const {
            updateTitle = false,
            updateToolbar = false,
        } = options;

        const titleElement = document.getElementById('pageTitle');
        if (updateTitle && titleElement) {
            titleElement.textContent = menuTitle();
        }

        const toolbarElement = document.getElementById('toolbarContainer');
        if (updateToolbar && toolbarElement) {
            toolbarElement.innerHTML = renderToolbar(state.activeMenu);
        }

        const contentElement = document.getElementById('contentContainer');
        if (contentElement) {
            contentElement.innerHTML = renderMainContent();
        }

        const notificationBtn = document.getElementById('notificationBtn');
        if (notificationBtn) {
            updateNotificationButton(notificationBtn).catch((error) => console.error(error));
        }
    }

    function renderShell() {
        app.innerHTML = `
            <div class="container-fluid app-shell">
                <div class="row flex-md-nowrap">
                    <aside class="col-md-auto bg-dark text-white sidebar p-3">
                        <div class="d-flex justify-content-between align-items-center mb-4">
                            <strong>Меню</strong>
                            <button id="logoutBtn" class="btn btn-sm btn-outline-light">Выход</button>
                        </div>
                        <div class="nav nav-pills flex-column gap-2">
                            <button class="btn ${state.activeMenu === 'calls' ? 'btn-light text-dark' : 'btn-outline-light'} text-start" data-menu="calls">Звонки</button>
                            <button class="btn ${state.activeMenu === 'sms' ? 'btn-light text-dark' : 'btn-outline-light'} text-start" data-menu="sms">СМС</button>
                            <button class="btn ${state.activeMenu === 'settings' ? 'btn-light text-dark' : 'btn-outline-light'} text-start" data-menu="settings">Настройки</button>
                        </div>
                    </aside>
                    <main class="col p-3 p-md-4">
                        <div class="d-flex justify-content-between align-items-center mb-3">
                            <h1 id="pageTitle" class="h4 m-0">${menuTitle()}</h1>
                            <button id="notificationBtn" class="notif-toggle" type="button" aria-label="Уведомления"></button>
                        </div>

                        <div id="toolbarContainer">${renderToolbar(state.activeMenu)}</div>
                        <div id="contentContainer">${renderMainContent()}</div>
                    </main>
                </div>
            </div>

        `;

        ensureDomEventBindings();
        const notificationBtn = document.getElementById('notificationBtn');
        if (notificationBtn) {
            updateNotificationButton(notificationBtn).catch((error) => console.error(error));
        }
    }

    function ensureDomEventBindings() {
        if (state.domEventsBound) {
            return;
        }

        app.addEventListener('click', async (event) => {
            if (event.target.closest('#saveTranscriptionSettings')) {
                const selected = document.getElementById('transcriptionBackend');
                if (!selected || state.settings.saving) return;
                state.settings.backend = selected.value;
                state.settings.saving = true;
                state.settings.error = '';
                state.settings.message = '';
                refreshMainArea();
                try {
                    const response = await apiPost('save_transcription_settings', {
                        backend: state.settings.backend, csrf_token: state.settings.csrfToken,
                    });
                    state.settings.backend = response.settings.backend;
                    state.settings.message = 'Сохранено. Выбранный движок будет использоваться для следующих записей.';
                } catch (error) {
                    state.settings.error = error.message;
                } finally {
                    state.settings.saving = false;
                    if (state.activeMenu === 'settings') refreshMainArea();
                }
                return;
            }
            if (event.target.closest('#reloadTranscriptionSettings')) {
                await loadSettings();
                refreshMainArea();
                return;
            }
            const menuButton = event.target.closest('[data-menu]');
            if (menuButton) {
                clearDetail(true);
                state.activeMenu = menuButton.dataset.menu || 'calls';
                renderShell();
                await loadCurrentMenu();
                refreshMainArea();
                return;
            }

            const phoneElement = event.target.closest('[data-phone]');
            if (phoneElement) {
                await copyToClipboard(phoneElement.dataset.phone || '');
                return;
            }

            const detailElement = event.target.closest('[data-detail-view][data-detail-id]');
            if (detailElement) {
                const view = detailElement.dataset.detailView;
                const id = detailElement.dataset.detailId;
                if (!view || !id) {
                    return;
                }
                await loadDetail(view, id, true);
                renderShell();
                return;
            }

            const backButton = event.target.closest('#backToListBtn, #backToListBtnTop');
            if (backButton) {
                clearDetail(true);
                await loadCurrentMenu();
                renderShell();
                return;
            }

            const sortElement = event.target.closest('[data-sort-target]');
            if (sortElement) {
                const target = sortElement.dataset.sortTarget;
                const sortBy = sortElement.dataset.sortBy;
                if (!target || !sortBy) {
                    return;
                }
                toggleSort(target, sortBy);
                await loadTarget(target);
                renderShell();
                return;
            }

            const pageElement = event.target.closest('[data-page-target]');
            if (pageElement) {
                const target = pageElement.dataset.pageTarget;
                const page = Number(pageElement.dataset.page || '1');
                if (!target) {
                    return;
                }
                state[target].page = page;
                await loadTarget(target);
                renderShell();
                return;
            }

            const searchButton = event.target.closest('#callsSearchBtn, #smsSearchBtn');
            if (searchButton) {
                const target = searchButton.id.startsWith('sms') ? 'sms' : 'calls';
                const searchInput = document.getElementById(`${target}Search`);
                state[target].search = searchInput ? searchInput.value.trim() : '';
                state[target].page = 1;
                await loadTarget(target);
                renderShell();
                return;
            }

            const clearButton = event.target.closest('#callsClearBtn, #smsClearBtn');
            if (clearButton) {
                const target = clearButton.id.startsWith('sms') ? 'sms' : 'calls';
                const searchInput = document.getElementById(`${target}Search`);
                state[target].search = '';
                if (searchInput) {
                    searchInput.value = '';
                }
                state[target].page = 1;
                await loadTarget(target);
                renderShell();
                return;
            }

            const reloadButton = event.target.closest('#callsReloadBtn, #smsReloadBtn');
            if (reloadButton) {
                const target = reloadButton.id.startsWith('sms') ? 'sms' : 'calls';
                await loadTarget(target);
                renderShell();
                return;
            }

            const logoutButton = event.target.closest('#logoutBtn');
            if (logoutButton) {
                try {
                    await apiPost('logout');
                } finally {
                    state.authenticated = false;
                    stopPolling();
                    cleanupModalArtifacts();
                    renderLogin();
                }
                return;
            }

            const notificationButton = event.target.closest('#notificationBtn');
            if (notificationButton) {
                try {
                    await enablePush();
                    await updateNotificationButton(notificationButton);
                } catch (error) {
                    showToast(error.message || 'Не удалось включить уведомления', true);
                }
            }
        });

        app.addEventListener('change', async (event) => {
            const pageSizeElement = event.target.closest('#callsPageSize, #smsPageSize');
            if (!pageSizeElement) {
                return;
            }

            const target = pageSizeElement.id.startsWith('sms') ? 'sms' : 'calls';
            state[target].pageSize = Number(pageSizeElement.value || '10');
            state[target].page = 1;
            await loadTarget(target);
            renderShell();
        });

        app.addEventListener('keydown', async (event) => {
            const searchInput = event.target.closest('#callsSearch, #smsSearch');
            if (!searchInput || event.key !== 'Enter') {
                return;
            }

            event.preventDefault();
            const target = searchInput.id.startsWith('sms') ? 'sms' : 'calls';
            state[target].search = searchInput.value.trim();
            state[target].page = 1;
            await loadTarget(target);
            renderShell();
        });

        state.domEventsBound = true;
    }

    function toggleSort(target, sortBy) {
        if (state[target].sortBy === sortBy) {
            state[target].sortDirection = state[target].sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
            state[target].sortBy = sortBy;
            state[target].sortDirection = sortBy === 'timestamp' ? 'desc' : 'asc';
        }
        state[target].page = 1;
    }

    function applyListState(target, response) {
        const items = response.items || [];
        const meta = response.meta || null;
        const hasChanged = !isSamePayload(state[target].items, items) || !isSamePayload(state[target].meta, meta);

        state[target].items = items;
        state[target].meta = meta;

        return hasChanged;
    }

    async function loadCalls() {
        const response = await apiGet('list_calls', {
            number: state.calls.search,
            sortBy: state.calls.sortBy,
            sortDirection: state.calls.sortDirection,
            page: state.calls.page,
            pageSize: state.calls.pageSize,
        });
        return applyListState('calls', response);
    }

    async function loadSms() {
        const response = await apiGet('list_sms', {
            number: state.sms.search,
            sortBy: state.sms.sortBy,
            sortDirection: state.sms.sortDirection,
            page: state.sms.page,
            pageSize: state.sms.pageSize,
        });
        return applyListState('sms', response);
    }

    async function loadTarget(target) {
        if (target === 'calls') {
            return loadCalls();
        }
        return loadSms();
    }

    async function loadCurrentMenu() {
        if (state.activeMenu === 'settings') return loadSettings();
        return loadTarget(state.activeMenu);
    }

    function menuTitle() {
        return { calls: 'Звонки', sms: 'СМС', settings: 'Настройки' }[state.activeMenu] || 'Звонки';
    }

    function renderSettings() {
        const s = state.settings;
        return `<section class="card border-0 shadow-sm" style="max-width: 720px;">
            <div class="card-body p-4">
                <h2 class="h5 mb-3">Распознавание речи</h2>
                <p class="text-secondary">Выберите движок для новых записей звонков. Существующие расшифровки сохранятся.</p>
                <label class="form-label fw-semibold" for="transcriptionBackend">Движок распознавания</label>
                <select id="transcriptionBackend" class="form-select mb-3" ${s.loading || s.saving || !s.csrfToken ? 'disabled' : ''}>
                    <option value="gigaam" ${s.backend === 'gigaam' ? 'selected' : ''}>GigaAM</option>
                    <option value="whisper" ${s.backend === 'whisper' ? 'selected' : ''}>faster-whisper</option>
                </select>
                <p class="small text-secondary">GigaAM — рекомендуется для русской речи. faster-whisper — альтернативный движок распознавания.</p>
                ${s.loading ? '<p role="status">Загрузка настроек…</p>' : ''}
                ${s.error ? `<div class="alert alert-danger" role="alert">${escapeHtml(s.error)}</div>` : ''}
                ${s.message ? `<div class="alert alert-success" role="status">${escapeHtml(s.message)}</div>` : ''}
                <div class="d-flex gap-2">
                    <button id="saveTranscriptionSettings" class="btn btn-primary" type="button" ${s.loading || s.saving || !s.csrfToken ? 'disabled' : ''}>${s.saving ? 'Сохранение…' : 'Сохранить'}</button>
                    <button id="reloadTranscriptionSettings" class="btn btn-outline-secondary" type="button" ${s.loading || s.saving ? 'disabled' : ''}>Обновить</button>
                </div>
            </div>
        </section>`;
    }

    async function loadSettings() {
        state.settings.loading = true;
        state.settings.error = '';
        state.settings.message = '';
        state.settings.csrfToken = '';
        if (state.activeMenu === 'settings') refreshMainArea();
        try {
            const response = await apiGet('get_transcription_settings');
            state.settings.backend = response.settings.backend;
            state.settings.csrfToken = response.csrf_token;
        } catch (error) {
            state.settings.error = error.message;
        } finally {
            state.settings.loading = false;
        }
    }

function buildEventUrl(view, id) {
    const url = new URL(APP_CONFIG.appUrl, window.location.origin);
    url.searchParams.set('view', view);
    url.searchParams.set('id', id);
    return url.toString();
}

function normalizeComparableUrl(value) {
    try {
        const url = new URL(value, window.location.origin);
        url.hash = '';
        return url.toString();
    } catch (error) {
        return '';
    }
}

async function handleDeepLinkUrl(targetUrl, replaceState = true) {
    let url;

    try {
        url = new URL(targetUrl, window.location.origin);
    } catch (error) {
        console.error(error);
        return false;
    }

    if (url.origin !== window.location.origin) {
        window.location.href = url.toString();
        return true;
    }

    if (replaceState) {
        window.history.replaceState({}, '', url.toString());
    }

    const view = url.searchParams.get('view') || '';
    const id = url.searchParams.get('id') || '';

    if (!state.authenticated) {
        window.location.href = url.toString();
        return true;
    }

    if (view && id) {
        await loadDetail(view, id, false);
        renderShell();
        return true;
    }

    clearDetail(false);
    await loadCurrentMenu();
    renderShell();
    return true;
}

async function showNotification(title, body, url = APP_CONFIG.appUrl) {
    if (Notification.permission !== 'granted') {
        return;
    }

    if ('serviceWorker' in navigator) {
        const registration = await navigator.serviceWorker.getRegistration();
        if (registration) {
            await registration.showNotification(title, {
                body,
                icon: 'icons/512-any.png',
                badge: 'icons/192-any.png',
                data: {
                    url,
                },
            });
            return;
        }
    }

    new Notification(title, {
        body,
        data: {
            url,
        },
    });
}

async function bootstrapKnownIds() {
    const snapshot = await apiGet('latest_snapshot');
    state.knownCallIds = new Set((snapshot.calls || []).map((item) => item.id));
    state.knownSmsIds = new Set((snapshot.sms || []).map((item) => item.id));
}

async function pollUpdates() {
    if (state.pollInFlight) {
        return;
    }

    state.pollInFlight = true;

    try {
        const snapshot = await apiGet('latest_snapshot');
        const latestCalls = snapshot.calls || [];
        const latestSms = snapshot.sms || [];

        const nextCallIds = new Set(latestCalls.map((item) => item.id));
        const nextSmsIds = new Set(latestSms.map((item) => item.id));

        for (const item of latestCalls) {
            if (state.knownCallIds.size > 0 && !state.knownCallIds.has(item.id)) {
                await showNotification(
                    'Новый звонок',
                    `${item.type || ''}: ${item.number || ''}`.trim(),
                    buildEventUrl('call', item.id)
                );
            }
        }

        for (const item of latestSms) {
            if (state.knownSmsIds.size > 0 && !state.knownSmsIds.has(item.id)) {
                await showNotification(
                    'Новое СМС',
                    `${item.number || ''}: ${item.preview || ''}`.trim(),
                    buildEventUrl('sms', item.id)
                );
            }
        }

        state.knownCallIds = nextCallIds;
        state.knownSmsIds = nextSmsIds;

        // Poll notifications without discarding an unsaved engine selection.
        if (state.activeMenu === 'settings') return;

        let hasVisualChanges = false;

        if (state.detailView && state.detailItem && state.detailItem.id) {
            hasVisualChanges = await loadDetail(state.detailView, state.detailItem.id, false);
        } else {
            hasVisualChanges = await loadCurrentMenu();
        }

        if (hasVisualChanges) {
            refreshMainArea();
        }
    } catch (error) {
        console.error(error);
    } finally {
        state.pollInFlight = false;
    }
}

    function stopPolling() {
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
        }
    }

    function startPolling() {
        stopPolling();
        state.pollTimer = setInterval(() => {
            pollUpdates();
        }, APP_CONFIG.notificationPollMs || 15000);
    }

    function urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
        const rawData = atob(base64);
        const outputArray = new Uint8Array(rawData.length);

        for (let i = 0; i < rawData.length; i += 1) {
            outputArray[i] = rawData.charCodeAt(i);
        }

        return outputArray;
    }

    async function registerServiceWorker() {
        if (!('serviceWorker' in navigator)) {
            return null;
        }

        const registration = await navigator.serviceWorker.register('sw.js?v=20260920-01', { scope: './' });
        await navigator.serviceWorker.ready;
        await registration.update().catch(() => null);
        await syncServiceWorkerConfig(registration);

        if (!state.serviceWorkerListenersBound) {
            navigator.serviceWorker.addEventListener('controllerchange', async () => {
                try {
                    const readyRegistration = await navigator.serviceWorker.ready;
                    await syncServiceWorkerConfig(readyRegistration);
                } catch (error) {
                    console.error(error);
                }
            });

            navigator.serviceWorker.addEventListener('message', async (event) => {
                const message = event.data && typeof event.data === 'object' ? event.data : {};

                if (message.type !== 'OPEN_NOTIFICATION_TARGET' || !message.url) {
                    return;
                }

                try {
                    await handleDeepLinkUrl(String(message.url), true);
                } catch (error) {
                    console.error(error);
                }
            });

            state.serviceWorkerListenersBound = true;
        }

        return registration;
    }

    async function updateNotificationButton(button) {
        if (!button) {
            return;
        }

        const setToggleState = (stateName, label) => {
            button.classList.remove('is-on', 'is-off', 'is-blocked');
            button.classList.add(stateName);
            button.innerHTML = `
                <span class="notif-toggle__label">${escapeHtml(label)}</span>
                <span class="notif-toggle__switch" aria-hidden="true">
                    <span class="notif-toggle__thumb"></span>
                </span>
            `;
        };

        if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
            setToggleState('is-blocked', 'Недоступно');
            button.disabled = true;
            return;
        }

        button.disabled = false;

        const registration = await navigator.serviceWorker.ready;
        await syncServiceWorkerConfig(registration);

        const subscription = await registration.pushManager.getSubscription();
        if (subscription) {
            setToggleState('is-on', 'Уведомления');
            return;
        }

        if (Notification.permission === 'denied') {
            setToggleState('is-blocked', 'Заблокировано');
            button.disabled = true;
            return;
        }

        setToggleState('is-off', 'Уведомления');
    }

    async function enablePush() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
            throw new Error('Браузер не поддерживает Web Push.');
        }

        const permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            throw new Error('Уведомления не разрешены.');
        }

        const registration = await navigator.serviceWorker.ready;
        await syncServiceWorkerConfig(registration);

        let subscription = await registration.pushManager.getSubscription();

        if (!subscription) {
            subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: urlBase64ToUint8Array(APP_CONFIG.vapidPublicKey),
            });
        }

        const response = await fetch(APP_CONFIG.subscribeUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(subscription),
        });

        await parseResponse(response);
        showToast('Уведомления включены');
    }

    async function initializeApp() {
        await registerServiceWorker();
        await loadCalls();
        await loadSms();
        await bootstrapKnownIds();

        if (APP_CONFIG.initialView && APP_CONFIG.initialId) {
            try {
                await loadDetail(APP_CONFIG.initialView, APP_CONFIG.initialId, false);
            } catch (error) {
                console.error(error);
                clearDetail(false);
            }
        }

        renderShell();
        startPolling();
    }

    window.addEventListener('popstate', async () => {
        if (!state.authenticated) {
            return;
        }

        const url = new URL(window.location.href);
        const view = url.searchParams.get('view') || '';
        const id = url.searchParams.get('id') || '';

        if (view && id) {
            try {
                await loadDetail(view, id, false);
            } catch (error) {
                console.error(error);
            }
        } else {
            clearDetail(false);
            await loadCurrentMenu();
        }

        renderShell();
    });

    document.addEventListener('visibilitychange', async () => {
        if (!state.authenticated) {
            return;
        }

        if (document.visibilityState === 'visible') {
            startPolling();
            try {
                await pollUpdates();
            } catch (error) {
                console.error(error);
            }
            return;
        }

        stopPolling();
    });

    async function bootstrapApp() {
        await registerServiceWorker();

        if (!state.authenticated) {
            try {
                const session = await apiGet('session');
                state.authenticated = !!session.authenticated;
            } catch (error) {
                console.error(error);
            }
        }

        if (state.authenticated) {
            await initializeApp();
        } else {
            renderLogin();
        }
    }

    bootstrapApp();
})();
</script>
</body>
</html>

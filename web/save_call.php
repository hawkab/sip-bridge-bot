<?php
declare(strict_types=1);

require_once __DIR__ . '/common.php';
require_once __DIR__ . '/event_store.php';

require_api_authentication();

if (!function_exists('extract_transcription_entries_from_payload')) {
    function extract_transcription_entries_from_payload(array $data): array
    {
        if (array_key_exists('transcription_json', $data)) {
            return normalize_transcription_entries($data['transcription_json']);
        }

        if (array_key_exists('transcription', $data)) {
            return normalize_transcription_entries($data['transcription']);
        }

        return [];
    }
}

const SHORTENER_ENDPOINT_URL = 'https://olshansky.im/s/shorten.php';
const SHORTENER_CONNECT_TIMEOUT_SECONDS = 5;
const SHORTENER_REQUEST_TIMEOUT_SECONDS = 10;

function shorten_view_url(string $url): string
{
    $shortUrl = shorten_view_url_via_curl($url);
    if ($shortUrl !== null) {
        return $shortUrl;
    }

    $shortUrl = shorten_view_url_via_stream($url);
    if ($shortUrl !== null) {
        return $shortUrl;
    }

    return $url;
}

function shorten_view_url_via_curl(string $url): ?string
{
    if (!function_exists('curl_init')) {
        return null;
    }

    $ch = curl_init(SHORTENER_ENDPOINT_URL);
    if ($ch === false) {
        return null;
    }

    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_HTTPHEADER => ['Content-Type: text/plain'],
        CURLOPT_POSTFIELDS => $url,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => true,
        CURLOPT_CONNECTTIMEOUT => SHORTENER_CONNECT_TIMEOUT_SECONDS,
        CURLOPT_TIMEOUT => SHORTENER_REQUEST_TIMEOUT_SECONDS,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_MAXREDIRS => 5,
    ]);

    $response = curl_exec($ch);
    if (!is_string($response)) {
        curl_close($ch);
        return null;
    }

    $statusCode = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    $headerSize = (int) curl_getinfo($ch, CURLINFO_HEADER_SIZE);
    curl_close($ch);

    if ($statusCode < 200 || $statusCode >= 300) {
        return null;
    }

    $headers = substr($response, 0, $headerSize);
    $body = substr($response, $headerSize);

    return extract_shortened_url_from_response($body, $headers, $url);
}

function shorten_view_url_via_stream(string $url): ?string
{
    if (!function_exists('stream_context_create')) {
        return null;
    }

    $context = stream_context_create([
        'http' => [
            'method' => 'POST',
            'header' => "Content-Type: text/plain\r\n",
            'content' => $url,
            'timeout' => SHORTENER_REQUEST_TIMEOUT_SECONDS,
            'ignore_errors' => true,
        ],
    ]);

    $body = @file_get_contents(SHORTENER_ENDPOINT_URL, false, $context);
    if (!is_string($body) || $body === '') {
        return null;
    }

    $headers = '';
    $statusCode = 0;
    if (isset($http_response_header) && is_array($http_response_header)) {
        $headers = implode("\r\n", $http_response_header);
        foreach ($http_response_header as $headerLine) {
            if (preg_match('#^HTTP/\S+\s+(\d{3})#', $headerLine, $matches) === 1) {
                $statusCode = (int) $matches[1];
                break;
            }
        }
    }

    if ($statusCode < 200 || $statusCode >= 300) {
        return null;
    }

    return extract_shortened_url_from_response($body, $headers, $url);
}

function extract_shortened_url_from_response(string $body, string $headers, string $fallbackUrl): ?string
{
    $contentType = '';
    foreach (preg_split('/\r\n|\r|\n/', $headers) as $headerLine) {
        if (stripos($headerLine, 'Content-Type:') === 0) {
            $contentType = trim(substr($headerLine, strlen('Content-Type:')));
            break;
        }
    }

    $candidate = null;
    $trimmedBody = trim($body);

    $isJson = $trimmedBody !== ''
        && (
            str_contains(strtolower($contentType), 'application/json')
            || str_starts_with($trimmedBody, '{')
            || str_starts_with($trimmedBody, '[')
        );

    if ($isJson) {
        $decoded = json_decode($trimmedBody, true);
        if (is_array($decoded)) {
            foreach (['short_url', 'url', 'short', 'link', 'view_url'] as $key) {
                if (isset($decoded[$key]) && is_string($decoded[$key])) {
                    $candidate = trim($decoded[$key]);
                    break;
                }
            }
        }
    }

    if ($candidate === null) {
        $candidate = $trimmedBody;
    }

    if ($candidate === '' || filter_var($candidate, FILTER_VALIDATE_URL) === false) {
        return null;
    }

    return $candidate !== '' ? $candidate : $fallbackUrl;
}

function get_shortened_event_view_url(string $kind, string $id): string
{
    return shorten_view_url(get_event_view_url($kind, $id, true));
}

function respond_call_save_failure(string $message, array $context = [], int $statusCode = 200): void
{
    $error = create_error_record('call_save', $message, $context);

    json_response([
        'saved' => false,
        'view_url' => get_shortened_event_view_url('error', (string) $error['id']),
    ], $statusCode);
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    respond_call_save_failure('Method Not Allowed.', [
        'method' => (string) ($_SERVER['REQUEST_METHOD'] ?? ''),
    ], 405);
}

$contentType = strtolower((string) ($_SERVER['CONTENT_TYPE'] ?? ''));
$data = str_contains($contentType, 'application/json') ? get_json_request_body() : $_POST;

$type = trim((string) ($data['type'] ?? ''));
$timestampInput = trim((string) ($data['timestamp'] ?? ''));
$number = trim((string) ($data['number'] ?? ''));
$duration = (int) ($data['duration'] ?? 0);
$transcription = extract_transcription_entries_from_payload(is_array($data) ? $data : []);

if ($type === '' || $timestampInput === '' || $number === '') {
    respond_call_save_failure('type, timestamp and number are required.', [
        'payload_keys' => array_keys($data),
    ]);
}

$typeLower = function_exists('mb_strtolower') ? mb_strtolower($type) : strtolower($type);
if (!in_array($typeLower, ['входящий', 'исходящий'], true)) {
    respond_call_save_failure('type must be "входящий" or "исходящий".', [
        'type' => $type,
    ]);
}

$timestamp = parse_timestamp_to_unix($timestampInput);
if ($timestamp <= 0) {
    respond_call_save_failure('Invalid timestamp.', [
        'timestamp' => $timestampInput,
    ]);
}

$recordingFile = null;

try {
    if (isset($_FILES['recording']) && ($_FILES['recording']['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_NO_FILE) {
        $recordingFile = save_uploaded_recording($_FILES['recording'], $number, $timestamp);
    } elseif (!empty($data['recording_base64'])) {
        $recordingName = (string) ($data['recording_name'] ?? 'recording.wav');
        $recordingFile = save_recording_from_base64((string) $data['recording_base64'], $number, $timestamp, $recordingName);
    }
} catch (Throwable $e) {
    respond_call_save_failure($e->getMessage());
}

$record = [
    'id' => bin2hex(random_bytes(8)),
    'timestamp' => format_timestamp_moscow($timestamp),
    'type' => $typeLower,
    'number' => $number,
    'local_number' => preg_match('/^\+[1-9][0-9]{6,14}$/D', (string) ($data['local_number'] ?? '')) ? $data['local_number'] : '',
    'sim_port' => filter_var($data['sim_port'] ?? null, FILTER_VALIDATE_INT, ['options'=>['min_range'=>1, 'max_range'=>32]]) ?: null,
    'duration' => max(0, $duration),
    'recording_file' => $recordingFile,
    'transcription' => $transcription,
    'transcription_channels' => normalize_transcription_channels($data['transcription_channels'] ?? []),
    'created_at' => format_timestamp_moscow(time()),
];

try {
    append_event_record(CALLS_JSON_PATH, $record);
} catch (Throwable $e) {
    respond_call_save_failure('Failed to save call.', [
        'exception' => $e->getMessage(),
    ]);
}

$viewUrl = get_shortened_event_view_url('call', (string) $record['id']);
$pushUrl = get_event_view_url('call', (string) $record['id'], true);

try {
    $durationText = $record['duration'] > 0 ? (' (' . $record['duration'] . ' сек)') : '';
    send_push_to_all([
        'title' => 'Новый звонок',
        'body' => sprintf('%s %s%s', mb_safe_title($typeLower), $number, $durationText),
        'url' => $pushUrl,
        'tag' => 'call-' . normalize_number($number),
        'type' => 'call',
    ]);
} catch (Throwable $e) {
    // Intentionally ignore push delivery errors in API response.
}

json_response([
    'saved' => true,
    'view_url' => $viewUrl,
]);

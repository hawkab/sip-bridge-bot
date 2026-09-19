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

function respond_sms_save_failure(string $message, array $context = [], int $statusCode = 200): void
{
    $error = create_error_record('sms_save', $message, $context);

    json_response([
        'saved' => false,
        'view_url' => get_shortened_event_view_url('error', (string) $error['id']),
    ], $statusCode);
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    respond_sms_save_failure('Method Not Allowed.', [
        'method' => (string) ($_SERVER['REQUEST_METHOD'] ?? ''),
    ], 405);
}

$data = get_json_request_body();

$timestampInput = trim((string) ($data['timestamp'] ?? ''));
$number = trim((string) ($data['number'] ?? ''));
$textBase64 = (string) ($data['text'] ?? '');
$transcription = extract_transcription_entries_from_payload(is_array($data) ? $data : []);

if ($timestampInput === '' || $number === '' || $textBase64 === '') {
    respond_sms_save_failure('timestamp, number and text are required.', [
        'payload_keys' => array_keys($data),
    ]);
}

$timestamp = parse_timestamp_to_unix($timestampInput);
if ($timestamp <= 0) {
    respond_sms_save_failure('Invalid timestamp.', [
        'timestamp' => $timestampInput,
    ]);
}

$decodedText = base64_decode($textBase64, true);
if ($decodedText === false) {
    respond_sms_save_failure('text must be a valid base64 string.');
}

$record = [
    'id' => bin2hex(random_bytes(8)),
    'timestamp' => format_timestamp_moscow($timestamp),
    'number' => $number,
    'text' => $decodedText,
    'transcription' => $transcription,
    'created_at' => format_timestamp_moscow(time()),
];

try {
    append_event_record(SMS_JSON_PATH, $record);
} catch (Throwable $e) {
    respond_sms_save_failure('Failed to save sms.', [
        'exception' => $e->getMessage(),
    ]);
}

$viewUrl = get_shortened_event_view_url('sms', (string) $record['id']);
$pushUrl = get_event_view_url('sms', (string) $record['id'], true);

try {
    send_push_to_all([
        'title' => 'Новое СМС',
        'body' => sprintf('%s: %s', $number, sms_preview($decodedText, 120)),
        'url' => $pushUrl,
        'tag' => 'sms-' . normalize_number($number),
        'type' => 'sms',
    ]);
} catch (Throwable $e) {
    // Intentionally ignore push delivery errors in API response.
}

json_response([
    'saved' => true,
    'view_url' => $viewUrl,
]);

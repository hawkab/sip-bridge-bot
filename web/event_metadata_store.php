<?php
declare(strict_types=1);
require_once __DIR__ . '/event_store.php';

function event_metadata_path(string $kind): string
{
    if (!in_array($kind, ['calls','sms'], true)) throw new InvalidArgumentException('Invalid event kind.');
    return $kind === 'calls' ? CALLS_JSON_PATH : SMS_JSON_PATH;
}
function event_metadata_version(array $record): string
{
    return hash('sha256', json_encode($record, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));
}
function event_metadata_snapshot(string $kind): array
{
    return with_event_store_lock(event_metadata_path($kind), static fn(array $items): array =>
        array_map(static fn(array $record): array => ['record'=>$record,'version'=>event_metadata_version($record)], $items));
}
function update_event_metadata(string $kind, array $updates): array
{
    $path = event_metadata_path($kind);
    $selected = [];
    foreach ($updates as $update) {
        if (!is_array($update) || !is_string($update['id'] ?? null) || !is_string($update['version'] ?? null)
            || !preg_match('/^[a-f0-9]{64}$/D', $update['version']) || isset($selected[$update['id']])
            || !is_array($update['metadata'] ?? null) || !$update['metadata']) throw new InvalidArgumentException('Invalid update.');
        $meta = $update['metadata'];
        if (array_diff(array_keys($meta), ['local_number','sim_port','transcription_channels'])) throw new InvalidArgumentException('Unsupported metadata.');
        if (isset($meta['local_number']) && (!is_string($meta['local_number']) || !preg_match('/^\+[1-9][0-9]{6,14}$/D', $meta['local_number']))) throw new InvalidArgumentException('Invalid local number.');
        if (isset($meta['sim_port']) && (!is_int($meta['sim_port']) || $meta['sim_port'] < 1 || $meta['sim_port'] > 32)) throw new InvalidArgumentException('Invalid SIM.');
        if (isset($meta['transcription_channels'])) {
            if ($kind !== 'calls') throw new InvalidArgumentException('Channels belong to calls.');
            $meta['transcription_channels'] = normalize_transcription_channels($meta['transcription_channels']);
        }
        $selected[$update['id']] = ['version'=>$update['version'], 'metadata'=>$meta];
    }
    return with_event_store_lock($path, static function(array $items) use ($path, $kind, $selected): array {
        $before = $items; $updated = []; $conflicts = []; $seen = [];
        foreach ($items as &$item) {
            $id = $item['id'] ?? '';
            if (!isset($selected[$id])) continue;
            $seen[$id] = true; $update = $selected[$id];
            if (!hash_equals(event_metadata_version($item), $update['version'])) { $conflicts[$id] = 'record_changed'; continue; }
            $replacement = array_replace($item, $update['metadata']);
            if ($replacement !== $item) { $item = $replacement; $updated[] = $id; }
        }
        unset($item);
        foreach (array_diff_key($selected, $seen) as $id=>$_) $conflicts[$id] = 'record_missing';
        $backup = null;
        if ($updated) {
            $backup = $kind . '-before-metadata-' . gmdate('Ymd-His') . '-' . bin2hex(random_bytes(6)) . '.json';
            $backupPath = dirname($path) . '/' . $backup;
            $handle = fopen($backupPath, 'x');
            if ($handle === false) throw new RuntimeException('Cannot create backup.');
            try {
                $json = json_encode($before, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
                if (fwrite($handle, $json) !== strlen($json)) throw new RuntimeException('Backup incomplete.');
            } finally { fclose($handle); }
            chmod($backupPath,0600);
            write_event_store($path, $items);
        }
        return ['updated_ids'=>$updated,'conflicts'=>$conflicts,'backup'=>$backup];
    });
}

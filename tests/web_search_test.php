<?php
declare(strict_types=1);
require __DIR__ . '/../web/event_store.php';

$items = [
    ['id'=>'phone', 'number'=>'+7 (999) 111-11-11'],
    ['id'=>'other', 'number'=>'79992222222'],
    ['id'=>'alpha', 'number'=>'Bank'],
    ['id'=>'cyrillic', 'number'=>'Банк'],
    ['id'=>'literal', 'number'=>'Service.123'],
];
foreach ([
    ['', ['phone','other','alpha','cyrillic','literal']],
    ['  ', ['phone','other','alpha','cyrillic','literal']],
    ['йцйцйцйц', []],
    ['йц7999', []],
    [' +7 (999) 111-11-11 ', ['phone']],
    ['2222', ['other']],
    ['0000', []],
    ['+()-', []],
    ['bank', ['alpha']],
    ['БАНК', ['cyrillic']],
    ['Service.123', ['literal']],
    ['.*', []],
    ['[', []],
] as [$query, $expected]) {
    foreach (['calls', 'sms'] as $kind) {
        $actual = array_column(filter_events($items, $query, $kind), 'id');
        if ($actual !== $expected) throw new RuntimeException('Incorrect search: ' . $query);
    }
}
$sms = [
    ['id'=>'sip', 'number'=>'+79991111111', 'text'=>str_repeat('Начало сообщения. ', 20) . 'SIP test chat SIM 1'],
    ['id'=>'code', 'number'=>'+79992222222', 'text'=>'Код 4826. Цена: 12.50'],
    ['id'=>'regex', 'text'=>'Символы .* и [тест]'],
];
$calls = [
    ['id'=>'left', 'transcription'=>[['channel'=>'left','text'=>'Здравствуйте, Светлана!']]],
    ['id'=>'right', 'transcription'=>[['channel'=>'right','text'=>'Это СВЕТЛАНА. Код 4826']]],
    ['id'=>'json', 'transcription'=>json_encode([['text'=>'Светлана на связи']], JSON_UNESCAPED_UNICODE)],
    ['id'=>'conversation', 'transcription'=>['conversation'=>[['text'=>'Светлана'],['text'=>'на связи']]]],
    ['id'=>'legacy', 'transcription'=>'Светлана на связи'],
    ['id'=>'metadata', 'transcription'=>[['speaker'=>'Светлана','channel'=>'right','text'=>'Алло']]],
    ['id'=>'empty', 'transcription'=>null],
];
foreach ([
    ['sms', $sms, 'SIP', ['sip']],
    ['sms', $sms, 'sip', ['sip']],
    ['sms', $sms, '4826', ['code']],
    ['sms', $sms, '12.50', ['code']],
    ['sms', $sms, '.*', ['regex']],
    ['sms', $sms, '[тест]', ['regex']],
    ['sms', $sms, 'йцйцйцйц', []],
    ['calls', $calls, 'сВеТлАнА', ['left','right','json','conversation','legacy']],
    ['calls', $calls, 'Светлана на связи', ['json','conversation','legacy']],
    ['calls', $calls, '4826', ['right']],
    ['calls', $calls, 'right', []],
    ['calls', $calls, 'йцйцйцйц', []],
] as [$kind, $records, $query, $expected]) {
    $actual = array_column(filter_events($records, $query, $kind), 'id');
    if ($actual !== $expected) throw new RuntimeException('Incorrect content search: ' . $kind . ' ' . $query);
}
echo "PASS: phone formatting, full SMS text, both transcript channels, legacy formats, case-insensitive Cyrillic, numeric and literal content, missing queries\n";

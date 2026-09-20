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
    $actual = array_column(filter_events_by_number($items, $query), 'id');
    if ($actual !== $expected) throw new RuntimeException('Incorrect search: ' . $query);
}
echo "PASS: empty, missing, formatted phone, mixed text, sender names and literal search\n";

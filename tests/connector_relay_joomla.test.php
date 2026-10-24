<?php
// Modalita' firewall del connettore Joomla (1.43.0): la logica del giro (Support/Relay.php),
// senza Joomla. Uso: php tests/connector_relay_joomla.test.php
require __DIR__ . '/../connectors/joomla/plg_system_tdpanopticon/src/Support/Relay.php';
use TastiereDigitali\Plugin\System\Tdpanopticon\Support\Relay;

function check($cond, $msg) { if (!$cond) { fwrite(STDERR, "FALLITO: $msg\n"); exit(1); } }

$file = sys_get_temp_dir() . '/tdpanop_relay_' . bin2hex(random_bytes(4)) . '.json';
$relay = new Relay($file);
$sent = [];
$replies = [];
$ran = [];
$refreshes = [];
$T = 1_800_000_000;
$deps = function ($now, $extra = []) use (&$sent, &$replies, &$ran, &$refreshes) {
    return $extra + [
        'hub' => 'https://sentinel.example.test', 'url' => 'https://joomla.example.test/', 'version' => '1.43.0', 'now' => $now,
        'post' => function ($hub, $body, $timeout) use (&$sent, &$replies) { $sent[] = $body; return array_shift($replies) ?? ['_error' => 'x']; },
        'status' => function ($refresh) use (&$refreshes) { $refreshes[] = $refresh; return ['cms' => 'joomla', 'extensions' => []]; },
        'run' => function ($job, $hub) use (&$ran) { $ran[] = $job; return ['ok' => true, 'new' => '5.2.1']; },
    ];
};

check(Relay::cleanHub('https://sentinel.example.test/') === 'https://sentinel.example.test', 'indirizzo pulito');
check(Relay::cleanHub('javascript:alert(1)') === '' && Relay::cleanHub('https://x.test/?a=1') === '' && Relay::cleanHub('http://x.test') === '', 'indirizzi non validi, solo https');
check($relay->tick($deps($T, ['hub' => ''])) === 'wait', 'senza indirizzo niente giro');

$relay->noteContact($T - 600);
check($relay->tick($deps($T)) === 'wait', 'Sentinel lo cerca regolarmente: niente ping');

// Sentinel non lo cerca da 40 minuti
$st = $relay->state(); $st['contact'] = $T - 2400; $relay->save($st);
$replies = [['mode' => 'direct']];
check($relay->tick($deps($T)) === 'ping', 'ping quando Sentinel tace');
check($sent[0]['kind'] === 'ping' && $sent[0]['cms'] === 'joomla', 'ping leggero');
check($relay->tick($deps($T + 60)) === 'wait', 'un ping ogni mezz\'ora');

// il pannello chiede la modalita' firewall
$sent = [];
$replies = [
    ['mode' => 'relay', 'tick' => 300, 'ping' => 1800],
    ['mode' => 'relay', 'jobs' => [['id' => '9-a', 'action' => 'refresh'], ['id' => '9-b', 'action' => 'update', 'type' => 'component', 'slug' => 'com_akeeba']]],
    ['mode' => 'relay', 'jobs' => []],
];
check($relay->tick($deps($T + 1900)) === 'report:1', 'giro con un aggiornamento');
check($ran[0]['slug'] === 'com_akeeba', 'aggiornamento eseguito');
check(array_column($sent[2]['results'], 'id') === ['9-a', '9-b'] && $sent[2]['wait'] === true, 'esiti consegnati');
check($refreshes === [false, true, false], 'ricerca degli aggiornamenti fatta subito, stato nuovo dopo il lavoro');
check(isset($sent[2]['results'][0]['result']['status']['extensions']), 'l\'esito della ricerca porta lo stato fresco');

// senza poter chiudere la risposta al visitatore: solo stato, mai lavori
$sent = []; $ran = [];
$replies = [['mode' => 'relay', 'jobs' => []]];
check($relay->tick($deps($T + 2300, ['jobs' => false])) === 'report', 'solo stato');
check($sent[0]['nojobs'] === true && $ran === [], 'nessun lavoro senza fastcgi_finish_request');

check($sent[0]['jobs_ok'] === false, 'il pannello sa che qui non si eseguono lavori');

// tempo finito: il lavoro non cominciato torna in coda
$sent = []; $ran = [];
$replies = [['mode' => 'relay', 'jobs' => [['id' => '9-c', 'action' => 'update', 'type' => 'component', 'slug' => 'x']]], ['mode' => 'relay', 'jobs' => []]];
$relay->tick($deps($T + 2610, ['budget' => -1]));
check($ran === [] && ($sent[1]['results'][0]['result']['_requeue'] ?? false) === true, 'lavoro rimesso in coda');

// il pannello rifiuta questa copia: solo ping
$sent = [];
$replies = [['_error' => 'HTTP 403', '_code' => 403]];
check($relay->tick($deps($T + 2920)) === 'report-failed', 'report rifiutato');
check($relay->state()['mode'] === 'direct', 'rifiutato: fuori dalla modalita\' firewall');
$st = $relay->state(); $st['mode'] = 'relay'; $st['tick_at'] = 0; $relay->save($st);

// un giro alla volta
$h = fopen($file . '.lock', 'c'); flock($h, LOCK_EX);
check($relay->tick($deps($T + 2700)) === 'busy', 'lucchetto');
flock($h, LOCK_UN); fclose($h);

// ritorno alla strada diretta
$replies = [['mode' => 'direct', 'jobs' => []]];
$relay->tick($deps($T + 2700));
check($relay->state()['mode'] === 'direct', 'fuori dalla modalita\' firewall');

// stato completo ogni status_every, negli altri giri solo la domanda
$st = $relay->state(); $st['mode'] = 'relay'; $st['tick_at'] = 0; $st['status_at'] = 0; $relay->save($st);
$sent = [];
$replies = [['mode' => 'relay', 'jobs' => [], 'status_every' => 1800]];
$relay->tick($deps($T + 5000));
check(isset($sent[0]['status']), 'primo giro: stato completo');
$sent = [];
$replies = [['mode' => 'relay', 'jobs' => [], 'status_every' => 1800]];
$relay->tick($deps($T + 5300));
check(!isset($sent[0]['status']), '5 minuti dopo: solo la domanda');

// file di stato perso (pulizia della tmp): si riparte dalla modalita' diretta
@unlink($file);
check($relay->state()['mode'] === 'direct', 'stato perso: modalita\' diretta');
@unlink($file . '.lock');
echo "Modalita' firewall del connettore Joomla: ping, stato, lavori, esiti e tempi corretti.\n";

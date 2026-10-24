<?php
// Modalita' firewall del connettore WordPress (2.41.0): il sito chiama Sentinel quando un
// firewall davanti al sito blocca la strada diretta. Ping leggero fuori modalita', stato,
// lavori ed esiti in modalita' firewall, indirizzo del pannello imparato da Sentinel.
// Uso: php tests/connector_relay.test.php
define('ABSPATH', '/tmp/sentinel-qa/');
$mu = sys_get_temp_dir() . '/sentinel-qa-mu-' . bin2hex(random_bytes(4));
@mkdir($mu, 0777, true);
define('WPMU_PLUGIN_DIR', $mu);

$GLOBALS['options'] = [];
function add_action(...$unused) {}
function add_filter(...$unused) { return true; }
function register_activation_hook(...$unused) {}
function register_rest_route(...$unused) {}
function get_option($name, $default = false) { return $GLOBALS['options'][$name] ?? $default; }
function update_option($name, $value, $autoload = null) { $GLOBALS['options'][$name] = $value; return true; }
function add_option($name, $value, $unused = '', $autoload = null) {
    if (array_key_exists($name, $GLOBALS['options'])) { return false; }
    $GLOBALS['options'][$name] = $value;
    return true;
}
function delete_option($name) { unset($GLOBALS['options'][$name]); return true; }
function home_url($path = '') { return 'https://www.primapiuma.test' . $path; }
function get_locale() { return 'it_IT'; }

$connector = realpath(__DIR__ . '/../connectors/wordpress/td-panopticon/td-panopticon.php');
define('WP_PLUGIN_DIR', dirname(dirname($connector)));
require $connector;

function check($cond, $msg) { if (!$cond) { fwrite(STDERR, "FALLITO: $msg\n"); exit(1); } }

class FakeRequest {
    public $h;
    function __construct(array $h) { $this->h = $h; }
    function get_header($k) { return $this->h[$k] ?? null; }
}

// pannello finto: risposte in coda, richieste registrate
$GLOBALS['sent'] = [];
$GLOBALS['replies'] = [];
$post = function ($hub, $body, $timeout) {
    $GLOBALS['sent'][] = ['hub' => $hub, 'body' => $body];
    return array_shift($GLOBALS['replies']) ?? ['_error' => 'nessuna risposta'];
};
$ran = [];
$run = function ($job, $hub) use (&$ran) { $ran[] = $job; return ['ok' => true, 'new' => '9.5.1']; };
$statusCalls = [];
$status = function ($refresh, $auto) use (&$statusCalls) {
    $statusCalls[] = [$refresh, $auto];
    return ['cms' => 'wp', 'extensions' => [], 'core' => ['current' => '6.8.3']];
};
$deps = function ($now, $extra = []) use ($post, $run, $status) {
    return $extra + ['post' => $post, 'run' => $run, 'status' => $status, 'now' => $now];
};
$T = 1_800_000_000;

// 1) senza indirizzo del pannello non si chiama nessuno
check(tdpanop_relay_tick($deps($T)) === 'wait', 'senza indirizzo del pannello niente giro');
check($GLOBALS['sent'] === [], 'nessuna chiamata senza indirizzo');

// 2) l'indirizzo arriva con una chiamata autenticata di Sentinel
tdpanop_relay_note_contact(new FakeRequest(['x_sentinel_hub' => 'https://sentinel.example.test/']));
check(tdpanop_relay_hub() === 'https://sentinel.example.test', 'indirizzo imparato da X-Sentinel-Hub');
check(get_option(TDPANOP_RELAY_SEEN) > 0, 'contatto del pannello annotato');
tdpanop_relay_note_contact(new FakeRequest(['x_sentinel_hub' => 'ftp://altro.test']));
tdpanop_relay_note_contact(new FakeRequest(['x_sentinel_hub' => 'http://in-chiaro.test']));
tdpanop_relay_note_contact(new FakeRequest(['x_sentinel_hub' => 'https://evil.test/?x=1']));
check(tdpanop_relay_hub() === 'https://sentinel.example.test', 'indirizzi non validi ignorati');

// 3) Sentinel lo cerca regolarmente: nessun ping
update_option(TDPANOP_RELAY_SEEN, $T - 600);
check(tdpanop_relay_tick($deps($T)) === 'wait', 'contatto di 10 minuti fa: niente ping');

// 4) Sentinel non lo cerca da 40 minuti: ping, il pannello dice "diretta"
update_option(TDPANOP_RELAY_SEEN, $T - 2400);
$GLOBALS['replies'] = [['mode' => 'direct', 'tick' => 300, 'ping' => 1800, 'jobs' => []]];
check(tdpanop_relay_tick($deps($T)) === 'ping', 'ping quando Sentinel tace');
check($GLOBALS['sent'][0]['body']['kind'] === 'ping', 'il ping e\' leggero');
check(!isset($GLOBALS['sent'][0]['body']['status']), 'il ping non porta lo stato');
check($GLOBALS['sent'][0]['body']['url'] === 'https://www.primapiuma.test/', 'il ping dice chi e\' il sito');
check(strlen($GLOBALS['sent'][0]['body']['fp']) === 16, 'il ping porta l\'impronta dell\'installazione');
check(tdpanop_relay_tick($deps($T + 60)) === 'wait', 'un ping ogni mezz\'ora, non di piu\'');
check(!isset($GLOBALS['options'][TDPANOP_RELAY_LOCK]), 'lucchetto liberato');

// 5) pannello irraggiungibile: niente raffica di tentativi
$GLOBALS['sent'] = [];
$GLOBALS['replies'] = [['_error' => 'HTTP 502']];
check(strpos(tdpanop_relay_tick($deps($T + 1900)), 'ping-failed: HTTP 502') === 0, 'pannello giu\'');
check(tdpanop_relay_tick($deps($T + 1960)) === 'wait', 'dopo un ping fallito si aspetta il giro dopo');

// 6) il pannello ha trovato il firewall: modalita' firewall, stato, lavori ed esiti
$GLOBALS['sent'] = [];
$GLOBALS['replies'] = [
    ['mode' => 'relay', 'tick' => 300, 'ping' => 1800],
    ['mode' => 'relay', 'jobs' => [
        ['id' => '7-a', 'action' => 'refresh'],
        ['id' => '7-b', 'action' => 'update', 'type' => 'plugin', 'slug' => 'woocommerce'],
    ], 'wp_auto_updates' => 'block'],
    ['mode' => 'relay', 'jobs' => []],
];
$out = tdpanop_relay_tick($deps($T + 4000));
check($out === 'report:1', "giro completo con un aggiornamento (fatto: $out)");
check(count($ran) === 1 && $ran[0]['slug'] === 'woocommerce', 'aggiornamento eseguito');
$second = $GLOBALS['sent'][2]['body'];
check($second['kind'] === 'report' && $second['wait'] === true, 'dopo un lavoro si aspetta il prossimo');
check(array_column($second['results'], 'id') === ['7-a', '7-b'], 'esiti consegnati, ricalcolo compreso');
check($second['results'][1]['result']['new'] === '9.5.1', 'esito dell\'aggiornamento');
check($statusCalls[0] === [false, null] && $statusCalls[1] === [true, 'block'], 'ricerca degli aggiornamenti subito, con la scelta degli aggiornamenti automatici');
check(isset($second['results'][0]['result']['status']['extensions']), 'l\'esito della ricerca porta lo stato fresco');
check(tdpanop_relay_state()['mode'] === 'relay', 'modalita\' firewall ricordata');

// 7) in modalita' firewall: ogni 5 minuti, anche se il pannello non chiama
check(tdpanop_relay_tick($deps($T + 4100)) === 'wait', 'prima dei 5 minuti niente');
$GLOBALS['sent'] = [];
$GLOBALS['replies'] = [['mode' => 'relay', 'jobs' => []]];
check(tdpanop_relay_tick($deps($T + 4310)) === 'report', 'dopo 5 minuti lo stato');
check($GLOBALS['sent'][0]['body']['kind'] === 'report', 'subito il report, senza ping');

// 8) tempo finito: gli esiti partono, nuovi lavori no
$GLOBALS['sent'] = [];
$ran = [];
$GLOBALS['replies'] = [
    ['mode' => 'relay', 'jobs' => [['id' => '7-c', 'action' => 'update', 'type' => 'core', 'slug' => '']]],
    ['mode' => 'relay', 'jobs' => []],
];
tdpanop_relay_tick($deps($T + 4700, ['budget' => -1]));
check($ran === [], 'col tempo finito non si comincia un lavoro');
check($GLOBALS['sent'][0]['body']['nojobs'] === true, 'il pannello sa che non deve consegnare lavori');
check(($GLOBALS['sent'][1]['body']['results'][0]['result']['_requeue'] ?? false) === true, 'il lavoro non cominciato torna in coda');

// 8b) il pannello rifiuta questa copia (sito tolto, token cambiato, altra copia): solo ping
$GLOBALS['sent'] = [];
$GLOBALS['replies'] = [['_error' => 'HTTP 409', '_code' => 409]];
check(strpos(tdpanop_relay_tick($deps($T + 5000)), 'report-failed: HTTP 409') === 0, 'report rifiutato');
check(tdpanop_relay_state()['mode'] === 'direct', 'rifiutato: fuori dalla modalita\' firewall');
update_option(TDPANOP_RELAY_STATE, array_merge(tdpanop_relay_state(), ['mode' => 'relay', 'tick_at' => 0]));

// 9) il pannello torna alla strada diretta
$GLOBALS['replies'] = [['mode' => 'direct', 'jobs' => []]];
tdpanop_relay_tick($deps($T + 5100));
check(tdpanop_relay_state()['mode'] === 'direct', 'fuori dalla modalita\' firewall');

// 10) un giro gia' in corso: il secondo non parte
update_option(TDPANOP_RELAY_SEEN, 0);
$GLOBALS['options'][TDPANOP_RELAY_LOCK] = $T + 9000;
check(tdpanop_relay_tick($deps($T + 9010)) === 'busy', 'un giro alla volta');
check(tdpanop_relay_tick($deps($T + 9950)) !== 'busy', 'lucchetto di un giro morto a meta\' superato dopo 15 minuti');

// 10b) stato completo ogni status_every (Impostazioni del pannello), negli altri giri solo la domanda
update_option(TDPANOP_RELAY_STATE, array_merge(tdpanop_relay_state(), ['mode' => 'relay', 'tick_at' => 0, 'status_at' => 0, 'status_every' => 1800]));
delete_option(TDPANOP_RELAY_LOCK);
$GLOBALS['sent'] = []; $statusCalls = [];
$GLOBALS['replies'] = [['mode' => 'relay', 'jobs' => [], 'status_every' => 1800, 'tick' => 300]];
tdpanop_relay_tick($deps($T + 12000));
check(isset($GLOBALS['sent'][0]['body']['status']), 'primo giro: stato completo');
$GLOBALS['sent'] = [];
$GLOBALS['replies'] = [['mode' => 'relay', 'jobs' => [], 'status_every' => 1800, 'tick' => 300]];
tdpanop_relay_tick($deps($T + 12300));
check(!isset($GLOBALS['sent'][0]['body']['status']), '5 minuti dopo: solo la domanda, leggera');
$GLOBALS['sent'] = []; $ran = [];
$GLOBALS['replies'] = [
    ['mode' => 'relay', 'jobs' => [['id' => '7-z', 'action' => 'update', 'type' => 'plugin', 'slug' => 'b']], 'status_every' => 1800],
    ['mode' => 'relay', 'jobs' => [], 'status_every' => 1800],
];
tdpanop_relay_tick($deps($T + 12600));
check(!isset($GLOBALS['sent'][0]['body']['status']) && isset($GLOBALS['sent'][1]['body']['status']), 'dopo un lavoro: lo stato nuovo');
$GLOBALS['sent'] = [];
$GLOBALS['replies'] = [['mode' => 'relay', 'jobs' => [], 'status_every' => 1800]];
tdpanop_relay_tick($deps($T + 14500));
check(isset($GLOBALS['sent'][0]['body']['status']), 'passati 30 minuti: di nuovo lo stato completo');

// 10c) il pannello chiede lo stato (non ne ha uno recente): un giro in piu' con lo stato
update_option(TDPANOP_RELAY_STATE, array_merge(tdpanop_relay_state(), ['mode' => 'relay', 'tick_at' => 0, 'status_at' => $T + 15000, 'status_every' => 3600]));
$GLOBALS['sent'] = [];
$GLOBALS['replies'] = [['mode' => 'relay', 'jobs' => [], 'need_status' => true, 'status_every' => 3600], ['mode' => 'relay', 'jobs' => [], 'need_status' => false, 'status_every' => 3600]];
tdpanop_relay_tick($deps($T + 15400));
check(!isset($GLOBALS['sent'][0]['body']['status']) && isset($GLOBALS['sent'][1]['body']['status']), 'stato mandato quando il pannello lo chiede');
check(count($GLOBALS['sent']) === 2, 'e poi basta');

// 10d) piu' richieste di ricerca nello stesso giro: una sola ricerca
$GLOBALS['sent'] = []; $statusCalls = [];
$GLOBALS['replies'] = [['mode' => 'relay', 'jobs' => [['id' => '7-r1', 'action' => 'refresh'], ['id' => '7-r2', 'action' => 'refresh']], 'status_every' => 3600],
                       ['mode' => 'relay', 'jobs' => []]];
tdpanop_relay_tick($deps($T + 15800));
check(count(array_filter($statusCalls, function ($c) { return $c[0] === true; })) === 1, 'una sola ricerca per giro');
check(count($GLOBALS['sent'][1]['body']['results']) === 2, 'esito per entrambe');

// 10e) pulsante: solo chiamata e stato, nessun lavoro (pagina veloce)
$GLOBALS['sent'] = []; $ran = [];
$GLOBALS['replies'] = [['mode' => 'relay', 'jobs' => [['id' => '7-x', 'action' => 'update', 'type' => 'plugin', 'slug' => 'c']]], ['mode' => 'relay', 'jobs' => []]];
tdpanop_relay_tick($deps($T + 15810, ['force' => true, 'nojobs' => true, 'status_now' => true]));
check($ran === [] && $GLOBALS['sent'][0]['body']['nojobs'] === true && isset($GLOBALS['sent'][0]['body']['status']), 'pulsante: stato subito, lavori al giro normale');

// 11) pulsante "Chiama Sentinel TD ora": parte anche se non e' ora, e l'esito resta
update_option(TDPANOP_RELAY_SEEN, $T + 20000);
$GLOBALS['options'][TDPANOP_RELAY_LOCK] = 0;
delete_option(TDPANOP_RELAY_LOCK);
update_option(TDPANOP_RELAY_STATE, array_merge(tdpanop_relay_state(), ['mode' => 'direct', 'ping_at' => $T + 20000]));
check(tdpanop_relay_tick($deps($T + 20010)) === 'wait', 'senza pulsante: non e\' ora');
$GLOBALS['replies'] = [['mode' => 'direct']];
check(tdpanop_relay_tick($deps($T + 20020, ['force' => true])) === 'ping', 'col pulsante: chiamata subito');
$st = tdpanop_relay_state();
check($st['last'] === 'ping' && $st['last_at'] === $T + 20020, 'esito e ora dell\'ultima chiamata salvati');
check(tdpanop_relay_outcome_text('ping-failed: HTTP 403') === 'chiamata non riuscita: HTTP 403', 'esito a parole');

echo "Modalita' firewall del connettore WordPress: ping, stato, lavori, esiti e tempi corretti.\n";

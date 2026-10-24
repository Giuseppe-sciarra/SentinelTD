<?php
// Modalita' firewall, connettore WordPress 2.41.1: plugin e temi si aggiornano nel BACKEND del
// sito (admin-ajax.php richiamato puntando alla macchina stessa, senza passare dal firewall),
// cosi' i prodotti a licenza trovano il loro aggiornatore. Se non va, come prima.
// Uso: php tests/connector_relay_admin.test.php
define('ABSPATH', '/tmp/sentinel-qa/');
$mu = sys_get_temp_dir() . '/sentinel-qa-mu-' . bin2hex(random_bytes(4));
@mkdir($mu, 0777, true);
define('WPMU_PLUGIN_DIR', $mu);

$GLOBALS['options'] = ['td_panopticon_token' => 'tok-del-sito'];
$GLOBALS['actions'] = [];
function add_action($tag, $cb = null, $prio = 10, $args = 1) { $GLOBALS['actions'][$tag][] = $cb; return true; }
function remove_action($tag, $cb, $prio = 10) {
    $GLOBALS['actions'][$tag] = array_values(array_filter($GLOBALS['actions'][$tag] ?? [], function ($x) use ($cb) { return $x !== $cb; }));
    return true;
}
function add_filter(...$unused) { return true; }
function register_activation_hook(...$unused) {}
function register_rest_route(...$unused) {}
function get_option($name, $default = false) { return $GLOBALS['options'][$name] ?? $default; }
function update_option($name, $value, $autoload = null) { $GLOBALS['options'][$name] = $value; return true; }
function admin_url($path = '') { return 'https://www.primapiuma.test/wp-admin/' . $path; }
function add_query_arg(array $args, $url) { return $url . '?' . http_build_query($args); }
function is_wp_error($x) { return $x instanceof WP_Error; }
function wp_remote_retrieve_response_code($r) { return $r['code']; }
function wp_remote_retrieve_body($r) { return $r['body']; }
class WP_Error { public $m; function __construct($c = '', $m = '') { $this->m = $m; } function get_error_message() { return $this->m; } }
class WP_REST_Request {
    public $p = [];
    function __construct(...$unused) {}
    function set_param($k, $v) { $this->p[$k] = $v; }
    function get_param($k) { return $this->p[$k] ?? null; }
}
// "rete" finta: per ogni tentativo l'IP fissato dal hook e la risposta
$GLOBALS['calls'] = [];
$GLOBALS['answers'] = [];
function wp_remote_post($url, $args) {
    // il hook che fissa l'indirizzo: host e IP letti dalle sue variabili, poi provato davvero
    $pin = null;
    foreach ($GLOBALS['actions']['http_api_curl'] ?? [] as $cb) {
        $vars = (new ReflectionFunction($cb))->getStaticVariables();
        $pin = $vars['host'] . '->' . $vars['ip'];
        if (function_exists('curl_init')) {
            $cb(curl_init(), $args, $url);       // non deve dare errori su un handle vero
        }
    }
    $GLOBALS['calls'][] = ['url' => $url, 'args' => $args, 'pin' => $pin];
    return array_shift($GLOBALS['answers']) ?? new WP_Error('x', 'nessuna risposta');
}

$connector = realpath(__DIR__ . '/../connectors/wordpress/td-panopticon/td-panopticon.php');
define('WP_PLUGIN_DIR', dirname(dirname($connector)));
require $connector;

function check($cond, $msg) { if (!$cond) { fwrite(STDERR, "FALLITO: $msg\n"); exit(1); } }

$_SERVER['SERVER_ADDR'] = '10.0.0.5';
$job = ['id' => '7-a', 'action' => 'update', 'type' => 'plugin', 'slug' => 'elementor-pro', 'expected' => '3.25.0', 'backup' => '1'];

// 1) il backend risponde dal primo indirizzo (127.0.0.1)
$GLOBALS['answers'] = [['code' => 200, 'body' => json_encode(['ok' => true, 'new' => '3.25.0', 'context' => 'admin'])]];
$r = tdpanop_relay_admin_update($job);
check(is_array($r) && $r['ok'] === true && $r['context'] === 'admin', 'aggiornamento fatto nel backend');
$c = $GLOBALS['calls'][0];
check(strpos($c['url'], 'https://www.primapiuma.test/wp-admin/admin-ajax.php?action=tdpanop_update') === 0, 'admin-ajax del sito');
check($c['args']['headers']['X-Sentinel-Token'] === 'tok-del-sito', 'col token del sito');
check($c['args']['body']['slug'] === 'elementor-pro' && $c['args']['body']['expected'] === '3.25.0', 'con i dati dell\'aggiornamento');
check($c['args']['redirection'] === 0 && $c['args']['sslverify'] === false, 'nessun redirect, macchina locale');
check($c['pin'] === 'www.primapiuma.test->127.0.0.1', 'puntato alla macchina stessa, non al firewall');
check(empty($GLOBALS['actions']['http_api_curl']), 'il hook che fissa l\'indirizzo viene tolto');

// 2) 127.0.0.1 non risponde, l'indirizzo del server si'
$GLOBALS['calls'] = [];
$GLOBALS['answers'] = [new WP_Error('x', 'connection refused'), ['code' => 200, 'body' => json_encode(['ok' => false, 'manual' => true, 'reason' => 'no_package'])]];
$r = tdpanop_relay_admin_update($job);
check(count($GLOBALS['calls']) === 2 && $r['reason'] === 'no_package', 'secondo indirizzo provato, esito vero (anche se negativo) tenuto');
check($GLOBALS['calls'][1]['pin'] === 'www.primapiuma.test->10.0.0.5', 'secondo tentativo sull\'indirizzo del server');

// 3) nessuna risposta del connettore (pagina del server, "0", dati persi): null
$GLOBALS['calls'] = [];
$GLOBALS['answers'] = [['code' => 200, 'body' => '0'], ['code' => 200, 'body' => json_encode(['ok' => false, 'error' => 'type non valido'])]];
check(tdpanop_relay_admin_update($job) === null, 'nessun esito del connettore: null');

// 3b) "type non valido" dal primo indirizzo: si prova il secondo
$GLOBALS['calls'] = [];
$GLOBALS['answers'] = [['code' => 200, 'body' => json_encode(['ok' => false, 'error' => 'type non valido']),], ['code' => 200, 'body' => json_encode(['ok' => true, 'new' => '1.2'])]];
$r = tdpanop_relay_admin_update($job);
check(is_array($r) && $r['ok'] === true && count($GLOBALS['calls']) === 2, 'dati persi sul primo indirizzo: secondo indirizzo');

// 4) nel giro: prima il backend, se non va dentro il giro; core e traduzioni sempre dentro il giro
$seen = [];
$deps = [
    'admin'  => function ($j) use (&$seen) { $seen[] = 'admin'; return $GLOBALS['admin_reply']; },
    'update' => function ($req) use (&$seen) { $seen[] = 'giro:' . $req->get_param('type'); return ['ok' => true, 'new' => 'x']; },
];
$GLOBALS['admin_reply'] = ['ok' => true, 'new' => '3.25.0', 'context' => 'admin'];
$r = tdpanop_relay_run($job, 'https://sentinel.example.test', $deps);
check($seen === ['admin'] && $r['context'] === 'admin', 'plugin: nel backend');
$seen = [];
$GLOBALS['admin_reply'] = null;
tdpanop_relay_run($job, 'https://sentinel.example.test', $deps);
check($seen === ['admin', 'giro:plugin'], 'backend non raggiungibile: dentro il giro come prima');
$seen = [];
tdpanop_relay_run(['id' => '7-b', 'action' => 'update', 'type' => 'core', 'slug' => ''], 'https://sentinel.example.test', $deps);
tdpanop_relay_run(['id' => '7-c', 'action' => 'update', 'type' => 'language', 'slug' => 'wp-translations'], 'https://sentinel.example.test', $deps);
check($seen === ['giro:core', 'giro:language'], 'core e traduzioni dentro il giro');

// 5) ricerca degli aggiornamenti dal backend (plugin a licenza compresi)
$GLOBALS['calls'] = [];
$GLOBALS['answers'] = [['code' => 200, 'body' => json_encode(['cms' => 'wp', 'extensions' => [['slug' => 'elementor-pro', 'update' => true]], 'context' => 'admin'])]];
$st = tdpanop_relay_status(true, 'block');
check(($st['context'] ?? '') === 'admin' && $st['extensions'][0]['slug'] === 'elementor-pro', 'ricerca fatta nel backend');
check(strpos($GLOBALS['calls'][0]['url'], 'action=tdpanop_status') !== false, 'admin-ajax, azione di ricerca');
check($GLOBALS['calls'][0]['args']['body']['refresh'] === '1' && $GLOBALS['calls'][0]['args']['body']['wp_auto_updates'] === 'block', 'ricerca forzata, con la scelta degli aggiornamenti automatici');
check($GLOBALS['calls'][0]['pin'] === 'www.primapiuma.test->127.0.0.1', 'puntata alla macchina stessa');

echo "Modalita' firewall: plugin e temi aggiornati nel backend del sito, senza passare dal firewall.\n";

<?php
// Aggiornamento del core WordPress dal connettore (2.41.7).
// - download fermo: WordPress dava 300 secondi a ogni download, quanto il limite di php-fpm, che
//   fermava PHP a meta' lavoro e lasciava il core bloccato 15 minuti. Ora solo i download su file
//   hanno al massimo 150 secondi e si interrompono dopo 30 secondi senza dati.
// - l'errore del download arriva al pannello col motivo vero (cURL error 28), non solo "Download non riuscito."
// - "[locked]" dice da quando WordPress tiene il blocco e quando lo libera da solo
// - se il pannello smette di aspettare, PHP non si ferma a meta' (ignore_user_abort)
// Uso: php tests/connector_core_update.test.php   (si rilancia da solo per gli altri casi)
$case = $argv[1] ?? 'scaricamento';
$root = sys_get_temp_dir() . '/sentinel-qa-core-' . bin2hex(random_bytes(4));
define('ABSPATH', $root . '/');
define('WPMU_PLUGIN_DIR', $root . '/mu');
define('MINUTE_IN_SECONDS', 60);
foreach (['wp-admin/includes', 'mu'] as $d) { @mkdir($root . '/' . $d, 0777, true); }
foreach (['file', 'misc', 'plugin', 'update', 'class-wp-upgrader', 'class-core-upgrader'] as $f) {
    file_put_contents($root . "/wp-admin/includes/$f.php", "<?php\n");
}

$GLOBALS['options'] = [];
$GLOBALS['hooks'] = [];
$GLOBALS['upgrade'] = null;       // cosa restituisce Core_Upgrader::upgrade()
function add_action($h, $cb = null, $p = 10, $n = 1) { $GLOBALS['hooks'][$h][] = $cb; return true; }
function add_filter($h, $cb = null, $p = 10, $n = 1) { $GLOBALS['hooks'][$h][] = $cb; return true; }
function remove_filter(...$u) { return true; }
function register_activation_hook(...$u) {} function register_rest_route(...$u) {}
function apply_filters($t, $v, ...$a) { return $v; }
function do_action(...$a) {}
function get_option($n, $d = false) { return array_key_exists($n, $GLOBALS['options']) ? $GLOBALS['options'][$n] : $d; }
function update_option($n, $v, $a = null) { $GLOBALS['options'][$n] = $v; return true; }
function delete_option($n) { unset($GLOBALS['options'][$n]); return true; }
function wp_cache_delete($k, $g = '') { return true; }
function get_site_transient($n) { return (object) ['checked' => [], 'updates' => [], 'translations' => []]; }
function delete_site_transient($n) { return true; }
function wp_update_plugins() {} function wp_update_themes() {} function wp_version_check() {}
function wp_upload_dir($t = null, $c = true) { return ['basedir' => ABSPATH . 'wp-content/uploads']; }
function wp_date($f, $ts) { return gmdate($f, $ts + 3600); }   // sito in Europe/Rome (ora solare)
function wp_strip_all_tags($s) { return strip_tags($s); }
function get_bloginfo($k) { return '7.1.3'; }
function get_core_updates() { return [(object) ['response' => 'upgrade', 'current' => '7.1.4']]; }
function is_wp_error($x) { return $x instanceof WP_Error; }
class WP_Error {
    private $c, $m, $d;
    function __construct($c = '', $m = '', $d = '') { $this->c = $c; $this->m = $m; $this->d = $d; }
    function get_error_message() { return $this->m; } function get_error_code() { return $this->c; } function get_error_data() { return $this->d; }
}
class WP_REST_Response { public $data; function __construct($d = null, $s = 200) { $this->data = $d; } function get_data() { return $this->data; } }
class WP_REST_Request { private $p; function __construct($p = []) { $this->p = $p; } function get_param($k) { return $this->p[$k] ?? null; } }
class Automatic_Upgrader_Skin { public $messages = []; function get_upgrade_messages() { return $this->messages; } }
class Core_Upgrader { function __construct($skin) {} function upgrade($u) { return $GLOBALS['upgrade']; } }

$connector = realpath(__DIR__ . '/../connectors/wordpress/td-panopticon/td-panopticon.php');
define('WP_PLUGIN_DIR', dirname(dirname($connector)));
require $connector;
function check($cond, $msg) { if (!$cond) { fwrite(STDERR, "FALLITO ($GLOBALS[case]): $msg\n"); exit(1); } }
function run_core() { return tdpanop_update(new WP_REST_Request(['type' => 'core', 'slug' => 'wordpress', 'backup' => '0']))->get_data(); }

if ($case === 'scaricamento') {
    $GLOBALS['upgrade'] = '7.1.4';
    ignore_user_abort(false);
    $r = run_core();
    check($r['ok'] === true && $r['new'] === '7.1.4', 'core aggiornato: ' . json_encode($r));
    check(ignore_user_abort() === 1, 'PHP va fino in fondo anche se il pannello smette di aspettare');
    check(in_array('tdpanop_download_args', $GLOBALS['hooks']['http_request_args'] ?? [], true)
          && in_array('tdpanop_download_curl', $GLOBALS['hooks']['http_api_curl'] ?? [], true), 'limiti sui download attivi');
    $a = tdpanop_download_args(['stream' => true, 'filename' => '/tmp/x', 'timeout' => 300], 'https://downloads.wordpress.org/release/wordpress-7.1.4-no-content.zip');
    check($a['timeout'] === 150, 'download del pacchetto: al massimo 150 secondi, non 300');
    $a = tdpanop_download_args(['stream' => true, 'timeout' => 60], 'https://example.test/x.zip');
    check($a['timeout'] === 60, 'un limite piu\' corto resta com\'e\'');
    $a = tdpanop_download_args(['timeout' => 300], 'https://api.wordpress.org/core/version-check/1.7/');
    check($a['timeout'] === 300, 'le chiamate normali non si toccano');
    if (function_exists('curl_init')) {
        tdpanop_download_curl(curl_init(), ['stream' => true], 'https://downloads.wordpress.org/x.zip');   // niente errori
    }
    echo "Download dei pacchetti: al massimo 150 secondi, fermo da 30 secondi = interrotto; PHP non si ferma a meta'.\n";
} elseif ($case === 'scaricamento-fermo') {
    $GLOBALS['upgrade'] = new WP_Error('download_failed', 'Download non riuscito.',
        'cURL error 28: Operation too slow. Less than 1024 bytes/sec transferred the last 30 seconds');
    $r = run_core();
    check($r['ok'] === false, 'non riuscito');
    check(strpos($r['error'], 'Download non riuscito.: cURL error 28: Operation too slow') === 0, 'motivo vero del download: ' . $r['error']);
    check(strpos($r['error'], 'interrotto dal connettore prima del limite di tempo del server') !== false
          && substr($r['error'], -17) === '[download_failed]', 'spiegato e col codice: ' . $r['error']);
    echo "Download fermo: errore col motivo vero, non solo \"Download non riuscito.\"\n";
} elseif ($case === 'bloccato') {
    $GLOBALS['options']['core_updater.lock'] = gmmktime(18, 3, 21, 10, 28, 2026);    // 19:03:21 a Roma
    $GLOBALS['upgrade'] = new WP_Error('locked', 'Attualmente è in corso un altro aggiornamento.');
    $r = run_core();
    check($r['ok'] === false && $r['error'] === 'Attualmente è in corso un altro aggiornamento. (blocco messo da WordPress il 28/10/2026 19:03, si libera da solo il 28/10/2026 19:18) [locked]',
          'da quando e fino a quando: ' . $r['error']);
    echo "Core bloccato: l'errore dice da quando e quando si libera.\n";
}
if ($case === 'scaricamento') {
    foreach (['scaricamento-fermo', 'bloccato'] as $next) {
        passthru(escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' ' . $next, $code);
        if ($code !== 0) { exit($code); }
    }
}

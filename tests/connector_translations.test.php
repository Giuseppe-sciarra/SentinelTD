<?php
// Traduzioni WordPress: il connettore dice "aggiornate" solo se i file sono stati scritti davvero.
// - installata e non piu' proposta          -> "traduzioni aggiornate (1/1)"
// - installata ma WordPress la ripropone identica (stessa data) -> "aggiornate (1/1)" e da quel
//   momento non risulta piu' da aggiornare (prima: "0/1" ad ogni giro, con email e Telegram)
// - non installata (download fallito)        -> ok=false col motivo dell'installatore
// Uso: php tests/connector_translations.test.php   (si rilancia da solo per gli altri casi)
$case = $argv[1] ?? 'installata';
$root = sys_get_temp_dir() . '/sentinel-qa-tr-' . bin2hex(random_bytes(4));
define('ABSPATH', $root . '/');
define('WP_LANG_DIR', $root . '/wp-content/languages');
define('WPMU_PLUGIN_DIR', $root . '/mu');
foreach (['wp-admin/includes', 'wp-content/languages/plugins', 'mu'] as $d) { @mkdir($root . '/' . $d, 0777, true); }
foreach (['file', 'misc', 'plugin', 'update', 'class-wp-upgrader', 'class-language-pack-upgrader'] as $f) {
    file_put_contents($root . "/wp-admin/includes/$f.php", "<?php\n");
}

$GLOBALS['options'] = [];
$offer = ['type' => 'plugin', 'slug' => 'wp-mail-logging', 'language' => 'it_IT', 'version' => '1.17.1',
          'updated' => '2026-10-01 10:00:00', 'package' => 'https://downloads.wordpress.org/x.zip', 'autoupdate' => true];
$GLOBALS['transient'] = [$offer];
function add_action(...$u) {} function add_filter(...$u) { return true; } function register_activation_hook(...$u) {} function register_rest_route(...$u) {}
function apply_filters($t, $v, ...$a) { return $v; }
function get_option($n, $d = false) { return $GLOBALS['options'][$n] ?? $d; }
function update_option($n, $v, $a = null) { $GLOBALS['options'][$n] = $v; return true; }
function delete_option($n) { unset($GLOBALS['options'][$n]); return true; }
function get_site_transient($n) { return $n === 'update_plugins' ? (object) ['translations' => $GLOBALS['transient']] : (object) ['translations' => []]; }
function delete_site_transient($n) { return true; }
function wp_update_plugins() { if (!empty($GLOBALS['ran']) && isset($GLOBALS['after'])) { $GLOBALS['transient'] = $GLOBALS['after']; } }
function wp_update_themes() {} function wp_version_check() {} function wp_clean_update_cache() {}
function wp_get_translation_updates() { return array_map(fn($t) => (object) $t, $GLOBALS['transient']); }
function wp_get_installed_translations($dom) { return []; }
function wp_strip_all_tags($s) { return strip_tags($s); }
function is_wp_error($x) { return $x instanceof WP_Error; }
class WP_Error { private $m; function __construct($c = '', $m = '') { $this->m = $m; } function get_error_message() { return $this->m; } }
class WP_REST_Response { public $data; function __construct($d = null, $s = 200) { $this->data = $d; } function get_data() { return $this->data; } }
class WP_REST_Request { private $p; function __construct($p = []) { $this->p = $p; } function get_param($k) { return $this->p[$k] ?? null; } }
class Automatic_Upgrader_Skin { public $messages = []; function get_upgrade_messages() { return $this->messages; } }
class Language_Pack_Upgrader {
    private $skin; function __construct($skin) { $this->skin = $skin; }
    function bulk_upgrade($updates = [], $args = []) {
        $GLOBALS['ran'] = true;
        if ($GLOBALS['case'] === 'fallita') { $this->skin->messages[] = 'Download non riuscito. <b>cURL error 28</b>'; return [null]; }
        foreach ($updates as $u) { file_put_contents(WP_LANG_DIR . "/plugins/{$u->slug}-{$u->language}.mo", 'mo'); }
        return [['ok' => true]];
    }
}

$connector = realpath(__DIR__ . '/../connectors/wordpress/td-panopticon/td-panopticon.php');
define('WP_PLUGIN_DIR', dirname(dirname($connector)));
require $connector;
function check($cond, $msg) { if (!$cond) { fwrite(STDERR, "FALLITO ($GLOBALS[case]): $msg\n"); exit(1); } }

if ($case === 'installata') {
    $GLOBALS['after'] = [];                                  // dopo l'installazione WordPress non la propone piu'
} elseif ($case === 'riproposta') {
    $GLOBALS['after'] = [$offer];                            // WordPress ripropone lo stesso pacchetto
}
check(count(tdpanop_pending_translations()) === 1, 'prima: una traduzione da installare');
$r = tdpanop_update(new WP_REST_Request(['type' => 'language', 'slug' => 'wp-translations', 'backup' => '0']))->get_data();

if ($case === 'fallita') {
    check($r['ok'] === false, 'niente scritto: non e\' un aggiornamento riuscito');
    check(strpos($r['error'], 'wp-mail-logging (it_IT)') !== false && strpos($r['error'], 'cURL error 28') !== false, 'motivo e traduzione nel messaggio: ' . $r['error']);
    check(count(tdpanop_pending_translations()) === 1, 'resta da installare');
    echo "Traduzione non installata: errore col motivo dell'installatore.\n";
    exit(0);
}
check($r['ok'] === true && $r['new'] === 'traduzioni aggiornate (1/1)', 'installata: ' . json_encode($r));
if ($case === 'riproposta') {
    check(count(tdpanop_pending_translations()) === 0, 'installata e riproposta identica: non e\' piu\' da aggiornare');
    $GLOBALS['transient'] = [array_merge($offer, ['updated' => '2026-10-20 08:00:00'])];
    check(count(tdpanop_pending_translations()) === 1, 'esce un pacchetto nuovo: torna da aggiornare');
    echo "Traduzione riproposta identica: contata una volta, poi non torna piu'.\n";
    exit(0);
}
check(count(tdpanop_pending_translations()) === 0, 'installata e sparita');
echo "Traduzione installata: aggiornate (1/1).\n";
foreach (['riproposta', 'fallita'] as $next) {
    passthru(escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' ' . $next, $code);
    if ($code !== 0) { exit($code); }
}

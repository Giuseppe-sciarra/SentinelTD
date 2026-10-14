<?php
// Traduzioni WordPress: il connettore dice "aggiornate" solo se, ricontrollato con
// wordpress.org, WordPress non le propone piu'.
// - installata e non piu' proposta            -> "traduzioni aggiornate (1/1): WP Mail Logging (it_IT)"
// - installata ma riproposta subito (2 giri)  -> fallito, con le date; resta da aggiornare
//   (il 2.39 se la ricordava e la nascondeva: Sentinel diceva 1/1, il backend la mostrava ancora)
// - al primo giro arriva un pacchetto piu' nuovo -> secondo giro, "(al secondo tentativo)"
// - non installata (download fallito)          -> fallito col motivo dell'installatore
// - wordpress.org non risponde al ricontrollo  -> si giudica dai file scritti
// - memoria del 2.39 rimasta sul sito          -> cancellata, la traduzione torna visibile
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
          'updated' => '2026-10-12 08:00:00', 'package' => 'https://downloads.wordpress.org/x.zip', 'autoupdate' => true];
$newer = array_merge($offer, ['updated' => '2026-10-14 09:00:00']);
$GLOBALS['transient'] = [$offer];
$GLOBALS['installs'] = 0;
$GLOBALS['afters'] = [];          // cosa propone WordPress dopo la 1a, 2a installazione
$GLOBALS['refresh_fails'] = false;
$GLOBALS['cache_deleted'] = [];
function add_action(...$u) {} function add_filter(...$u) { return true; } function remove_filter(...$u) { return true; }
function register_activation_hook(...$u) {} function register_rest_route(...$u) {}
function apply_filters($t, $v, ...$a) { return $v; }
function get_option($n, $d = false) { return array_key_exists($n, $GLOBALS['options']) ? $GLOBALS['options'][$n] : $d; }
function update_option($n, $v, $a = null) { $GLOBALS['options'][$n] = $v; return true; }
function delete_option($n) { unset($GLOBALS['options'][$n]); return true; }
function wp_cache_delete($k, $g = '') { $GLOBALS['cache_deleted'][] = $g; return true; }
function get_site_transient($n) {
    $broken = $GLOBALS['refresh_fails'] && $GLOBALS['installs'] > 0;
    if ($n === 'update_core') { return (object) ($broken ? ['translations' => []] : ['updates' => [(object) ['response' => 'latest']], 'translations' => []]); }
    if ($n === 'update_themes') { return (object) ($broken ? ['translations' => []] : ['checked' => [], 'translations' => []]); }
    return (object) ($broken ? ['translations' => []] : ['checked' => ['wp-mail-logging/wp-mail-logging.php' => '1.17.1'], 'translations' => $GLOBALS['transient']]);
}
function delete_site_transient($n) { return true; }
function wp_update_plugins() {
    $i = $GLOBALS['installs'];
    if ($i > 0) { $GLOBALS['transient'] = $GLOBALS['afters'][$i - 1] ?? $GLOBALS['transient']; }
}
function wp_update_themes() {} function wp_version_check() {} function wp_clean_update_cache() {}
function wp_get_translation_updates() { return array_map(fn($t) => (object) $t, $GLOBALS['transient']); }
function wp_get_installed_translations($dom) { return []; }
function wp_get_pomo_file_data($f) {
    preg_match('/PO-Revision-Date: (.+)/', (string) @file_get_contents($f), $m);
    return ['PO-Revision-Date' => $m[1] ?? ''];
}
function get_plugins() { return ['wp-mail-logging/wp-mail-logging.php' => ['Name' => 'WP Mail Logging', 'Version' => '1.17.1']]; }
function wp_strip_all_tags($s) { return strip_tags($s); }
function is_wp_error($x) { return $x instanceof WP_Error; }
class WP_Error { private $m; function __construct($c = '', $m = '') { $this->m = $m; } function get_error_message() { return $this->m; } }
class WP_REST_Response { public $data; function __construct($d = null, $s = 200) { $this->data = $d; } function get_data() { return $this->data; } }
class WP_REST_Request { private $p; function __construct($p = []) { $this->p = $p; } function get_param($k) { return $this->p[$k] ?? null; } }
class Automatic_Upgrader_Skin { public $messages = []; function get_upgrade_messages() { return $this->messages; } }
class Language_Pack_Upgrader {
    private $skin; function __construct($skin) { $this->skin = $skin; }
    function bulk_upgrade($updates = [], $args = []) {
        $GLOBALS['installs']++;
        if ($GLOBALS['case'] === 'fallita') { $this->skin->messages[] = 'Download non riuscito. <b>cURL error 28</b>'; return [null]; }
        foreach ($updates as $u) {
            $base = WP_LANG_DIR . "/plugins/{$u->slug}-{$u->language}";
            file_put_contents("$base.mo", 'mo');
            // il pacchetto scaricato dichiara una data piu' vecchia di quella annunciata
            file_put_contents("$base.po", "PO-Revision-Date: 2026-10-01 10:00:00+0000\n");
        }
        return [['ok' => true]];
    }
}

$connector = realpath(__DIR__ . '/../connectors/wordpress/td-panopticon/td-panopticon.php');
define('WP_PLUGIN_DIR', dirname(dirname($connector)));
require $connector;
function check($cond, $msg) { if (!$cond) { fwrite(STDERR, "FALLITO ($GLOBALS[case]): $msg\n"); exit(1); } }
function run_update() { return tdpanop_update(new WP_REST_Request(['type' => 'language', 'slug' => 'wp-translations', 'backup' => '0']))->get_data(); }

if ($case === 'installata') {
    $GLOBALS['afters'] = [[]];                                   // dopo l'installazione non la propone piu'
    check(count(tdpanop_pending_translations()) === 1, 'prima: una traduzione da installare');
    $r = run_update();
    check($r['ok'] === true && $r['new'] === 'traduzioni aggiornate (1/1): WP Mail Logging (it_IT)', 'installata: ' . json_encode($r));
    check(in_array('translation_files', $GLOBALS['cache_deleted'], true), 'elenco dei file di traduzione riletto dal disco');
    check(count(tdpanop_pending_translations()) === 0, 'installata e sparita');
    echo "Traduzione installata: aggiornate (1/1) col nome.\n";
} elseif ($case === 'riproposta') {
    $GLOBALS['afters'] = [[$offer], [$offer]];                   // WordPress la ripropone dopo entrambi i giri
    $r = run_update();
    check($r['ok'] === false, 'riproposta subito dopo: non e\' un aggiornamento riuscito: ' . json_encode($r));
    check(strpos($r['error'], 'WP Mail Logging (it_IT): installata, ma WordPress la ripropone subito') !== false, 'motivo: ' . $r['error']);
    check(strpos($r['error'], 'sul sito è del 01/10/2026 10:00') !== false && strpos($r['error'], 'wordpress.org la dà del 12/10/2026 08:00') !== false, 'date nel messaggio: ' . $r['error']);
    check($GLOBALS['installs'] === 2, 'riprovata una volta col pacchetto proposto dopo il ricontrollo');
    check(count(tdpanop_pending_translations()) === 1, 'resta visibile da aggiornare, come nel backend');
    echo "Traduzione riproposta subito: fallito con le date, resta da aggiornare.\n";
} elseif ($case === 'secondo') {
    $GLOBALS['afters'] = [[$newer], []];                         // nel frattempo esce un pacchetto nuovo
    $r = run_update();
    check($r['ok'] === true && $r['new'] === 'traduzioni aggiornate (1/1): WP Mail Logging (it_IT) (al secondo tentativo)', 'secondo giro: ' . json_encode($r));
    echo "Pacchetto nuovo arrivato nel frattempo: installato al secondo giro.\n";
} elseif ($case === 'fallita') {
    $GLOBALS['afters'] = [[$offer], [$offer]];
    $r = run_update();
    check($r['ok'] === false, 'niente scritto: non e\' un aggiornamento riuscito');
    check(strpos($r['error'], 'WP Mail Logging (it_IT): file non scritto') !== false && strpos($r['error'], 'cURL error 28') !== false, 'motivo e traduzione nel messaggio: ' . $r['error']);
    check(count(tdpanop_pending_translations()) === 1, 'resta da installare');
    echo "Traduzione non installata: errore col motivo dell'installatore.\n";
} elseif ($case === 'nonverificata') {
    $GLOBALS['refresh_fails'] = true;                            // wordpress.org non risponde al ricontrollo
    $r = run_update();
    check($r['ok'] === true && strpos($r['new'], 'traduzioni aggiornate (1/1): WP Mail Logging (it_IT) — non ricontrollate') === 0, 'file scritti, ricontrollo mancato: ' . json_encode($r));
    echo "Ricontrollo non riuscito: giudicata dai file scritti, e lo dice.\n";
} elseif ($case === 'memoria239') {
    $GLOBALS['options']['tdpanop_trans_ghosts'] = ['plugin|wp-mail-logging|it_IT' => $offer['updated']];
    check(count(tdpanop_pending_translations()) === 1, 'la traduzione nascosta dal 2.39 torna visibile');
    check(!array_key_exists('tdpanop_trans_ghosts', $GLOBALS['options']), 'memoria del 2.39 cancellata');
    echo "Memoria del 2.39 cancellata: Sentinel mostra quello che mostra WordPress.\n";
}
if ($case === 'installata') {
    foreach (['riproposta', 'secondo', 'fallita', 'nonverificata', 'memoria239'] as $next) {
        passthru(escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' ' . $next, $code);
        if ($code !== 0) { exit($code); }
    }
}

<?php
// Aggiornamenti automatici di WordPress spenti dal connettore, e test "aggiornamenti in
// background" tolto da Salute del sito, come AUTOMATIC_UPDATER_DISABLED + il mu-plugin
// "TD Site Health Tweaks". Dove ci sono gia', il connettore non aggiunge niente.
// Si accende e si spegne dal pannello: la scelta arriva con il controllo (wp_auto_updates).
// Uso: php tests/connector_autoupdates.test.php   (si rilancia da solo per gli altri casi)
$case = $argv[1] ?? 'nulla';
define('ABSPATH', '/tmp/sentinel-qa/');
$mu = sys_get_temp_dir() . '/sentinel-qa-mu-' . bin2hex(random_bytes(4));
@mkdir($mu, 0777, true);
define('WPMU_PLUGIN_DIR', $mu);
if ($case === 'gia_presenti') {
    define('AUTOMATIC_UPDATER_DISABLED', true);
    file_put_contents($mu . '/td-site-health-tweaks.php', "<?php\n");
} elseif ($case === 'costante_false') {
    define('AUTOMATIC_UPDATER_DISABLED', false);
}

$GLOBALS['filters'] = [];
$GLOBALS['options'] = [];
$GLOBALS['writes'] = 0;
function add_action(...$unused) {}
function register_activation_hook(...$unused) {}
function register_rest_route(...$unused) {}
function add_filter($tag, $cb, $prio = 10, $args = 1) { $GLOBALS['filters'][$tag][] = $cb; return true; }
function apply_filters($tag, $value, ...$args) {
    foreach ($GLOBALS['filters'][$tag] ?? [] as $f) { $value = $f($value, ...$args); }
    return $value;
}
function get_option($name, $default = false) { return $GLOBALS['options'][$name] ?? $default; }
function update_option($name, $value, $autoload = null) { $GLOBALS['options'][$name] = $value; $GLOBALS['writes']++; return true; }

$connector = realpath(__DIR__ . '/../connectors/wordpress/td-panopticon/td-panopticon.php');
define('WP_PLUGIN_DIR', dirname(dirname($connector)));
require $connector;

function check($cond, $msg) { if (!$cond) { fwrite(STDERR, "FALLITO ($GLOBALS[case]): $msg\n"); exit(1); } }
// come WP_Automatic_Updater::is_disabled()
function wp_auto_updates_disabled(): bool {
    return (bool) apply_filters('automatic_updater_disabled', defined('AUTOMATIC_UPDATER_DISABLED') && AUTOMATIC_UPDATER_DISABLED);
}
function health_tests(): array {
    return apply_filters('site_status_tests', ['direct' => ['php_version' => 1], 'async' => ['background_updates' => 1, 'loopback_requests' => 1]]);
}

if ($case === 'gia_presenti') {
    check(empty($GLOBALS['filters']['automatic_updater_disabled']), 'con la costante gia\' a true il connettore non aggiunge il filtro');
    check(empty($GLOBALS['filters']['site_status_tests']), 'con il mu-plugin presente il connettore non tocca Salute del sito');
    check(wp_auto_updates_disabled(), 'aggiornamenti automatici comunque spenti dalla costante');
    tdpanop_apply_auto_updates_choice('allow');
    check(tdpanop_auto_updates_state() === 'wp-config', 'la costante in wp-config vince e il pannello lo vede');
    echo "Costante e mu-plugin gia' presenti: il connettore non fa niente.\n";
    exit(0);
}

// connettore appena installato, il pannello non ha ancora detto niente: spenti
check(wp_auto_updates_disabled(), 'di base aggiornamenti automatici di WordPress spenti');
$t = health_tests();
check(!isset($t['async']['background_updates']), 'test "aggiornamenti in background" tolto da Salute del sito');
check(isset($t['async']['loopback_requests'], $t['direct']['php_version']), 'gli altri test di Salute del sito restano');
check(tdpanop_auto_updates_state() === 'blocked', 'stato per il pannello: blocked');
if ($case === 'costante_false') {
    echo "Costante a false in wp-config: aggiornamenti automatici spenti lo stesso.\n";
    exit(0);
}

// il pannello li lascia a WordPress
tdpanop_apply_auto_updates_choice('allow');
check(!wp_auto_updates_disabled(), 'impostazione spenta nel pannello: WordPress torna ad aggiornare da solo');
check(isset(health_tests()['async']['background_updates']), 'impostazione spenta: Salute del sito torna completa');
check(tdpanop_auto_updates_state() === 'allowed', 'stato per il pannello: allowed');
// stessa scelta di nuovo: nessuna scrittura nel database
$w = $GLOBALS['writes'];
tdpanop_apply_auto_updates_choice('allow');
check($GLOBALS['writes'] === $w, 'la scelta si scrive solo quando cambia');
// parametro assente o sconosciuto (pannello vecchio): resta com'era
tdpanop_apply_auto_updates_choice(null);
tdpanop_apply_auto_updates_choice('boh');
check(tdpanop_auto_updates_state() === 'allowed', 'senza scelta del pannello resta com\'era');
// e si rispengono
tdpanop_apply_auto_updates_choice('block');
check(wp_auto_updates_disabled() && !isset(health_tests()['async']['background_updates']), 'riaccesa nel pannello: di nuovo spenti');

echo "Dal pannello: spenti di base, lasciati a WordPress su richiesta, e di nuovo spenti.\n";
foreach (['gia_presenti', 'costante_false'] as $other) {
    passthru(escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' ' . $other, $code);
    if ($code !== 0) { exit($code); }
}

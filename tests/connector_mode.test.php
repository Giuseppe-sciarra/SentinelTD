<?php
// Connettore WordPress caricato davvero: dove dice di girare (mu-plugin o cartella dei plugin)
// e rifiuto di installare una seconda copia di se stesso.
// Uso: php tests/connector_mode.test.php        (si rilancia da solo per il caso mu-plugin)
define('ABSPATH', '/tmp/sentinel-qa/');
function add_action(...$unused) {}
function add_filter(...$unused) {}
function register_activation_hook(...$unused) {}
function register_rest_route(...$unused) {}

$connector = realpath(__DIR__ . '/../connectors/wordpress/td-panopticon/td-panopticon.php');
$mu = ($argv[1] ?? '') === 'mu';
if ($mu) {
    define('WPMU_PLUGIN_DIR', dirname($connector));                 // il file sta direttamente in mu-plugins
    $plugins = sys_get_temp_dir() . '/sentinel-qa-' . bin2hex(random_bytes(4)) . '/plugins';
    @mkdir($plugins . '/td-panopticon', 0777, true);
    define('WP_PLUGIN_DIR', $plugins);
} else {
    define('WPMU_PLUGIN_DIR', '/tmp/sentinel-qa/wp-content/mu-plugins');
    define('WP_PLUGIN_DIR', dirname(dirname($connector)));           // .../wordpress/td-panopticon/...
}
require $connector;

function check($cond, $msg) { if (!$cond) { fwrite(STDERR, "FALLITO: $msg\n"); exit(1); } }

function make_zip(string $root, bool $self): string {
    $path = sys_get_temp_dir() . '/tdpanop-' . bin2hex(random_bytes(4)) . '.zip';
    $z = new ZipArchive();
    $z->open($path, ZipArchive::CREATE);
    $head = $self ? "<?php\n/**\n * Plugin Name: Sentinel TD Agent\n * Version: 9.9.9\n */\n"
                  : "<?php\n/**\n * Plugin Name: Altro plugin\n */\n";
    $z->addFromString("$root/td-panopticon.php", $head);
    $z->close();
    return $path;
}

$where = tdpanop_running_mode();
if ($mu) {
    check($where === ['mode' => 'mu', 'folder' => ''], 'mu-plugin riconosciuto: ' . json_encode($where));
    $err = tdpanop_self_package_conflict(make_zip('td-panopticon', true));
    check(strpos($err, 'mu-plugin') !== false, 'con il mu-plugin niente copia nuova nei plugin');
    check(tdpanop_self_package_conflict(make_zip('td-panopticon', false)) === '', 'un altro plugin si installa');
    // una copia vecchia gia' presente nei plugin si puo' sostituire (diventa silenziosa)
    file_put_contents(WP_PLUGIN_DIR . '/td-panopticon/td-panopticon.php', "<?php\n");
    check(tdpanop_self_package_conflict(make_zip('td-panopticon', true)) === '', 'copia esistente sostituibile');
    echo "Connettore come mu-plugin: modalità dichiarata, nessuna copia nuova, copie vecchie sostituibili.\n";
    exit(0);
}

check($where === ['mode' => 'plugin', 'folder' => 'td-panopticon'], 'cartella dei plugin riconosciuta: ' . json_encode($where));
check(tdpanop_self_package_conflict(make_zip('td-panopticon', true)) === '', 'aggiornamento nella stessa cartella permesso');
$err = tdpanop_self_package_conflict(make_zip('sentinel-td', true));
check(strpos($err, '"sentinel-td"') !== false, 'copia nuova in un\'altra cartella rifiutata: ' . $err);
check(tdpanop_self_package_conflict(make_zip('sentinel-td', false)) === '', 'un plugin qualsiasi non e\' toccato dalla guardia');
$flat = sys_get_temp_dir() . '/tdpanop-flat-' . bin2hex(random_bytes(4)) . '.zip';
$z = new ZipArchive(); $z->open($flat, ZipArchive::CREATE);
$z->addFromString('td-panopticon.php', "<?php\n/**\n * Plugin Name: Sentinel TD Agent\n */\n"); $z->close();
check(strpos(tdpanop_self_package_conflict($flat), 'senza cartella') !== false, 'zip del connettore senza cartella rifiutato');

// stesso test come mu-plugin, in un processo a parte (le costanti non si ridefiniscono)
passthru(escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' mu', $code);
check($code === 0, 'caso mu-plugin');
echo "Connettore nei plugin: cartella dichiarata, aggiornamento sul posto, nessuna seconda copia.\n";

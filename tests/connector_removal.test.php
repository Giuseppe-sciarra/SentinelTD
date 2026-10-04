<?php
// Load the real WordPress plugin, capture its registered routes, and verify its
// status/update/install/backup APIs remain while diagnostics has disappeared.
define('ABSPATH', '/tmp/sentinel-qa/');
$actions = [];
$routes = [];
function add_action($hook, $callback, ...$unused) { global $actions; $actions[$hook][] = $callback; }
function add_filter(...$unused) {}
function register_activation_hook(...$unused) {}
function register_rest_route($namespace, $route, $options) { global $routes; $routes[$route] = $options; }
require __DIR__ . '/../connectors/wordpress/td-panopticon/td-panopticon.php';
foreach ($actions['rest_api_init'] as $callback) { $callback(); }
foreach (['/status', '/update', '/install', '/backups', '/rollback'] as $route) {
    if (!isset($routes[$route]) || !is_callable($routes[$route]['callback'])) { throw new RuntimeException('Missing API ' . $route); }
}
if (isset($routes['/diagnostics'])) { throw new RuntimeException('Diagnostic API still registered'); }
foreach (['tdpanop_diagnostics', 'tdpanop_core_integrity', 'tdpanop_space_probe', 'tdpanop_space_check', 'tdpanop_space_explanation', 'tdpanop_sizes', 'tdpanop_outside_logs'] as $fn) {
    if (function_exists($fn)) { throw new RuntimeException('Retired diagnostic function still present: ' . $fn); }
}
echo "WordPress real registration passed: normal APIs retained, diagnostics/probes removed.\n";

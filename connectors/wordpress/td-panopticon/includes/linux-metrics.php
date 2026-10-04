<?php
namespace TastiereDigitali\Sentinel;

/** Read-only Linux measurements shared by both CMS connectors (PHP 7.4+). */
final class LinuxMetrics
{
    private static function allowed(string $function): bool
    {
        $disabled = function_exists('ini_get') ? (string) ini_get('disable_functions') : '';
        return function_exists($function)
            && !in_array(strtolower($function), array_map('trim', explode(',', strtolower($disabled))), true);
    }

    private static function native(string $function, array $args = [])
    {
        if (!self::allowed($function)) {
            return null;
        }
        try {
            return @$function(...$args);
        } catch (\Throwable $e) {
            return null;
        }
    }

    private static function read(string $path): string
    {
        try {
            return @is_readable($path) ? (string) @file_get_contents($path, false, null, 0, 131072) : '';
        } catch (\Throwable $e) {
            return '';
        }
    }

    private static function quote(string $value): string
    {
        return "'" . str_replace("'", "'\\''", $value) . "'";
    }

    private static function runner(): callable
    {
        $deadline = microtime(true) + 3.0;  // total budget for all command fallbacks
        return static function (string $command) use ($deadline): string {
            if (PHP_OS_FAMILY !== 'Linux' || microtime(true) >= $deadline) {
                return '';
            }
            // Fixed commands only; a deterministic PATH/locale also covers minimal FPM environments.
            $env = 'LC_ALL=C PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/usr/local/sbin ';
            if (self::allowed('proc_open') && self::allowed('proc_get_status')
                && self::allowed('proc_terminate') && self::allowed('proc_close')) {
                $pipes = [];
                $process = null;
                try {
                    $process = @proc_open(['/bin/sh', '-c', $env . 'exec ' . $command],
                        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['file', '/dev/null', 'a']], $pipes);
                    if (is_resource($process)) {
                        fclose($pipes[0]);
                        stream_set_blocking($pipes[1], false);
                        $output = '';
                        $until = min($deadline, microtime(true) + 1.0);
                        do {
                            $output .= (string) stream_get_contents($pipes[1], max(0, 65536 - strlen($output)));
                            $status = proc_get_status($process);
                            if (!$status['running']) {
                                $output .= (string) stream_get_contents($pipes[1], max(0, 65536 - strlen($output)));
                                break;
                            }
                            if (microtime(true) >= $until || strlen($output) >= 65536) {
                                @proc_terminate($process, 9);
                                $output = '';  // never accept an unfinished command's partial output
                                break;
                            }
                            usleep(10000);
                        } while (true);
                        fclose($pipes[1]);
                        @proc_close($process);
                        return trim($output);
                    }
                } catch (\Throwable $e) {
                    foreach ($pipes as $pipe) {
                        if (is_resource($pipe)) {
                            fclose($pipe);
                        }
                    }
                    if (is_resource($process)) {
                        @proc_terminate($process, 9);
                        @proc_close($process);
                    }
                }
            }
            // Some hosting allows only exec/shell_exec. GNU and BusyBox timeout bound these too.
            $method = self::allowed('shell_exec') ? 'shell_exec' : (self::allowed('exec') ? 'exec' : '');
            if ($method === '') {
                return '';
            }
            $invoke = static function (string $cmd) use ($method): string {
                try {
                    if ($method === 'shell_exec') {
                        return substr((string) @shell_exec($cmd), 0, 65536);
                    }
                    $lines = [];
                    @exec($cmd, $lines);
                    return substr(implode("\n", $lines), 0, 65536);
                } catch (\Throwable $e) {
                    return '';
                }
            };
            if (trim($invoke($env . 'command -v timeout 2>/dev/null')) === '') {
                return '';  // no unbounded shell fallback
            }
            if (microtime(true) + 1.0 > $deadline) {
                return '';
            }
            return trim($invoke($env . 'timeout 1 ' . $command . ' 2>/dev/null'));
        };
    }

    private static function number($value, int $unit = 1): ?int
    {
        if (!is_numeric($value) || (float) $value < 0 || !is_finite((float) $value)
            || (float) $value > PHP_INT_MAX / $unit) {
            return null;
        }
        return (int) ((float) $value * $unit);
    }

    public static function parseMeminfo(string $raw): array
    {
        $out = ['total' => null, 'available' => null, 'swap_total' => null, 'swap_free' => null];
        foreach (['total' => 'MemTotal', 'available' => 'MemAvailable', 'swap_total' => 'SwapTotal', 'swap_free' => 'SwapFree'] as $key => $field) {
            if (preg_match('/^' . $field . ':\s+(\d+)\s*kB/mi', $raw, $m)) {
                $out[$key] = self::number($m[1], 1024);
            }
        }
        if ($out['available'] === null && preg_match('/^MemFree:\s+(\d+)\s*kB/mi', $raw, $m)) {
            $out['available'] = self::number($m[1], 1024);
            foreach (['Buffers', 'Cached'] as $field) {
                if (preg_match('/^' . $field . ':\s+(\d+)\s*kB/mi', $raw, $m)) {
                    $out['available'] += self::number($m[1], 1024) ?? 0;
                }
            }
        }
        return $out;
    }

    public static function parseFree(string $raw, int $unit = 1): array
    {
        $out = ['total' => null, 'available' => null, 'swap_total' => null, 'swap_free' => null];
        $headers = [];
        foreach (preg_split('/\r?\n/', $raw) as $line) {
            if (preg_match('/^\s*total\s+used\s+free\b/i', $line)) {
                $headers = preg_split('/\s+/', strtolower(trim($line)));
            }
            if (preg_match('/^Mem:\s+([\d\s]+)$/i', trim($line), $m)) {
                $v = array_map('intval', preg_split('/\s+/', trim($m[1])));
                if (count($v) < 3) {
                    continue;
                }
                $out['total'] = self::number($v[0], $unit);
                $available = $v[2];
                $idx = array_search('available', $headers, true);
                if ($idx !== false && isset($v[$idx])) {
                    $available = $v[$idx];
                } else {
                    foreach (['buffers', 'cached', 'buff/cache'] as $field) {
                        $idx = array_search($field, $headers, true);
                        if ($idx !== false && isset($v[$idx])) {
                            $available += $v[$idx];
                        }
                    }
                }
                $out['available'] = self::number($available, $unit);
            }
            if (preg_match('/^-\/\+ buffers\/cache:\s+\d+\s+(\d+)/i', trim($line), $m)) {
                $out['available'] = self::number($m[1], $unit);
            }
            if (preg_match('/^Swap:\s+(\d+)\s+\d+\s+(\d+)/i', trim($line), $m)) {
                $out['swap_total'] = self::number($m[1], $unit);
                $out['swap_free'] = self::number($m[2], $unit);
            }
        }
        return $out;
    }

    public static function parseLoad(string $raw): ?array
    {
        $pattern = '/^(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)(?:\s|$)/';
        if (preg_match('/load averages?:\s*(\d+(?:\.\d+)?)[,\s]+(\d+(?:\.\d+)?)[,\s]+(\d+(?:\.\d+)?)/i', $raw, $m)
            || preg_match($pattern, trim($raw), $m)) {
            return [round((float) $m[1], 2), round((float) $m[2], 2), round((float) $m[3], 2)];
        }
        return null;
    }

    public static function parseCpuList(string $raw): int
    {
        $raw = trim($raw);
        if (!preg_match('/^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$/D', $raw)) {
            return 0;
        }
        $count = 0;
        foreach (explode(',', $raw) as $part) {
            $range = array_map('intval', explode('-', $part));
            if (isset($range[1]) && $range[1] < $range[0]) {
                return 0;
            }
            $count += isset($range[1]) ? $range[1] - $range[0] + 1 : 1;
        }
        return $count > 0 && $count <= 1048576 ? $count : 0;
    }

    public static function parseLscpu(string $raw): int
    {
        $cpus = [];
        foreach (preg_split('/\r?\n/', $raw) as $line) {
            if (preg_match('/^(\d+),(?:Y|yes|1)$/i', trim($line), $m)) {
                $cpus[$m[1]] = true;
            }
        }
        if ($cpus) {
            return count($cpus);
        }
        if (preg_match('/^On-line CPU\(s\) list:\s*([0-9,-]+)\s*$/mi', $raw, $m)) {
            return self::parseCpuList($m[1]);
        }
        return preg_match('/^CPU\(s\):\s+(\d+)\s*$/mi', $raw, $m) ? (int) $m[1] : 0;
    }

    public static function parseDf(string $raw): array
    {
        foreach (preg_split('/\r?\n/', $raw) as $line) {
            if (preg_match('/\s(\d+)\s+\d+\s+(-?\d+)\s+\d+%\s+/', $line, $m)) {
                return ['total' => self::number($m[1], 1024), 'free' => self::number(max(0, (int) $m[2]), 1024)];
            }
        }
        return ['total' => null, 'free' => null];
    }

    public static function memoryInfo(?callable $read = null, ?callable $run = null): array
    {
        $read = $read ?? [self::class, 'read'];
        $run = $run ?? self::runner();
        $m = self::parseMeminfo($read('/proc/meminfo'));
        $m['source'] = $m['total'] ? '/proc/meminfo' : '';
        if (!$m['total'] || $m['available'] === null) {
            foreach (['free -b' => 1, 'free -k' => 1024, 'free' => 1024] as $command => $unit) {
                $candidate = self::parseFree($run($command), $unit);
                if ($candidate['total'] && $candidate['available'] !== null) {
                    foreach ($candidate as $key => $value) {
                        if ($value !== null) {
                            $m[$key] = $value;
                        }
                    }
                    $m['source'] = $command;
                    break;
                }
            }
        }
        return $m;
    }

    /** Callbacks make different Linux outputs testable without requiring those distributions. */
    public static function collect(string $root, int $cachedCores = 0, ?callable $read = null, ?callable $run = null, ?array $native = null): array
    {
        $read = $read ?? [self::class, 'read'];
        $run = $run ?? self::runner();
        $native = $native ?? ['load' => self::native('sys_getloadavg'), 'disk_total' => self::native('disk_total_space', [$root]), 'disk_free' => self::native('disk_free_space', [$root])];
        $sources = [];
        $load = $native['load'] ?? null;
        if (is_array($load) && count($load) >= 3 && count(array_filter(array_slice($load, 0, 3), static function ($v) { return is_numeric($v) && (float) $v >= 0 && is_finite((float) $v); })) === 3) {
            $load = array_map(static function ($v) { return round((float) $v, 2); }, array_slice($load, 0, 3));
            $sources['load'] = 'sys_getloadavg';
        } else {
            $load = self::parseLoad($read('/proc/loadavg'));
            $sources['load'] = $load !== null ? '/proc/loadavg' : '';
            if ($load === null) {
                $load = self::parseLoad($run('uptime'));
                $sources['load'] = $load !== null ? 'uptime' : '';
            }
        }
        $cores = max(0, $cachedCores);
        $sources['cores'] = $cores ? 'cache' : '';
        if (!$cores) {
            $raw = $read('/proc/cpuinfo');
            // A capped/truncated cpuinfo must not undercount large machines.
            $cores = strlen($raw) >= 131072 ? 0 : (int) preg_match_all('/^processor\s*:/m', $raw);
            $sources['cores'] = $cores ? '/proc/cpuinfo' : '';
        }
        if (!$cores) {
            $cores = self::parseCpuList($read('/sys/devices/system/cpu/online'));
            $sources['cores'] = $cores ? '/sys/devices/system/cpu/online' : '';
        }
        if (!$cores) {
            foreach (['getconf _NPROCESSORS_ONLN', 'nproc', 'lscpu -p=CPU,ONLINE', 'lscpu'] as $command) {
                $raw = trim($run($command));
                $cores = strpos($command, 'lscpu') === 0 ? self::parseLscpu($raw) : (preg_match('/^\d+$/D', $raw) ? (int) $raw : 0);
                if ($cores > 0 && $cores <= 1048576) {
                    $sources['cores'] = $command;
                    break;
                }
                $cores = 0;
            }
        }
        $mem = self::memoryInfo($read, $run);
        $sources['ram'] = $mem['source'];
        $total = self::number($native['disk_total'] ?? null);
        $free = self::number($native['disk_free'] ?? null);
        $sources['disk'] = $total && $free !== null ? 'PHP disk space' : '';
        if ((!$total || $free === null) && isset($root[0]) && $root[0] === '/' && strpos($root, "\0") === false) {
            $disk = self::parseDf($run('df -Pk ' . self::quote($root)));
            if ($disk['total'] && $disk['free'] !== null) {
                $total = $disk['total'];
                $free = $disk['free'];
                $sources['disk'] = 'df -Pk';
            }
        }
        $missing = [];
        if ($load === null) { $missing[] = 'load'; }
        if (!$cores) { $missing[] = 'cores'; }
        if (!$mem['total'] || $mem['available'] === null) { $missing[] = 'ram'; }
        if (!$total || $free === null) { $missing[] = 'disk'; }
        return ['load' => $load, 'cores' => $cores ?: null, 'disk_total' => $total ?: null, 'disk_free' => $free,
                'mem_total' => $mem['total'], 'mem_available' => $mem['available'], 'swap_total' => $mem['swap_total'], 'swap_free' => $mem['swap_free'],
                'metrics_sources' => $sources, 'metrics_missing' => $missing];
    }
}

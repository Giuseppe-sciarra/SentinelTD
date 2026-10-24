<?php
namespace TastiereDigitali\Plugin\System\Tdpanopticon\Support;

/**
 * Modalita' firewall (1.43.0): quando un firewall o un antibot davanti al sito (Cloudflare,
 * SiteGround, Sucuri, un IP bannato...) blocca Sentinel TD, e' il connettore a chiamarlo.
 * Il firewall filtra chi ENTRA nel sito, non quello che il sito manda fuori.
 *
 * - Fuori dalla modalita': se Sentinel non cerca il sito da mezz'ora, un ping leggero al
 *   pannello; la risposta dice in che modalita' stare.
 * - In modalita' firewall (la decide il pannello): ogni 5 minuti lo stato del sito, poi i
 *   lavori in coda eseguiti con le stesse funzioni di com_ajax, e gli esiti.
 *
 * Logica pura, senza Joomla: il plugin passa le funzioni che servono (chiamata al pannello,
 * stato, esecuzione di un lavoro). Stato in un file JSON nella cartella tmp di Joomla: se
 * sparisce si riparte dalla modalita' diretta e il primo ping rimette le cose a posto.
 */
final class Relay
{
    /** @var string */
    private $file;
    /** @var resource|null */
    private $lock;

    public function __construct(string $stateFile)
    {
        $this->file = $stateFile;
    }

    public static function cleanHub(string $url): string
    {
        $url = rtrim(trim($url), '/');
        if ($url === '' || strlen($url) > 255) {
            return '';
        }
        $p = parse_url($url);
        // solo https: a questo indirizzo il connettore manda il token del sito
        if (!is_array($p) || empty($p['host']) || strtolower((string) ($p['scheme'] ?? '')) !== 'https'
            || isset($p['user']) || isset($p['pass']) || isset($p['query']) || isset($p['fragment'])) {
            return '';
        }
        return $url;
    }

    public function state(): array
    {
        $raw = is_file($this->file) ? (string) @file_get_contents($this->file) : '';
        $st = $raw !== '' ? json_decode($raw, true) : [];
        return array_merge(['mode' => 'direct', 'tick' => 300, 'every' => 1800, 'ping_at' => 0, 'tick_at' => 0, 'contact' => 0,
            'status_every' => 0, 'status_at' => 0], is_array($st) ? $st : []);
    }

    public function save(array $st): void
    {
        @file_put_contents($this->file, json_encode($st), LOCK_EX);
    }

    /** Indirizzo del pannello gia' salvato da questa copia (evita di riscriverlo a ogni chiamata
     *  finche' la cache dei plugin di Joomla mostra ancora il valore vecchio). */
    public function hubSaved(string $hub): bool
    {
        return (string) ($this->state()['hub_saved'] ?? '') === $hub;
    }

    public function markHubSaved(string $hub): void
    {
        $st = $this->state();
        $st['hub_saved'] = $hub;
        $this->save($st);
    }

    /** Sentinel ha chiamato il connettore: un appunto al minuto, non di piu'. */
    public function noteContact(int $now): void
    {
        $st = $this->state();
        if ($now - (int) $st['contact'] >= 60) {
            $st['contact'] = $now;
            $this->save($st);
        }
    }

    public function apply(array $st, array $d): array
    {
        $was = (string) ($st['mode'] ?? 'direct');
        $st['mode']  = (($d['mode'] ?? '') === 'relay') ? 'relay' : 'direct';
        if ($was !== 'relay' && $st['mode'] === 'relay') {
            $st['status_at'] = 0;      // appena in modalita' firewall: lo stato completo subito
        }
        $st['tick']  = max(60, min(3600, (int) ($d['tick'] ?? 300)));
        $st['every'] = max(300, min(86400, (int) ($d['ping'] ?? 1800)));
        $st['status_every'] = max(0, min(86400, (int) ($d['status_every'] ?? 0)));
        $this->save($st);
        return $st;
    }

    /* Il pannello non vuole piu' sentire questa copia: si torna al solo ping della mezz'ora. */
    private function refused(array $st, $r, int $now): void
    {
        if (is_array($r) && in_array((int) ($r['_code'] ?? 0), [401, 403, 409], true)) {
            $st['mode'] = 'direct';
            $st['ping_at'] = $now;
            $this->save($st);
        }
    }

    public function due(int $now, string $hub): bool
    {
        if ($hub === '') {
            return false;
        }
        $st = $this->state();
        if ($st['mode'] === 'relay') {
            return $now - (int) $st['tick_at'] >= (int) $st['tick'] - 30;
        }
        return $now - (int) $st['contact'] >= (int) $st['every'] && $now - (int) $st['ping_at'] >= (int) $st['every'] - 60;
    }

    private function acquire(): bool
    {
        $h = @fopen($this->file . '.lock', 'c');
        if (!$h) {
            return false;
        }
        if (!@flock($h, LOCK_EX | LOCK_NB)) {
            fclose($h);
            return false;
        }
        $this->lock = $h;
        return true;
    }

    private function release(): void
    {
        if ($this->lock) {
            @flock($this->lock, LOCK_UN);
            @fclose($this->lock);
            $this->lock = null;
        }
    }

    /**
     * Il giro. $d: hub, url, version, fp, post(hub, body, timeout), status(bool refresh),
     * run(array job, hub), now, budget, jobs (false = solo stato, nessun lavoro, tempi brevi:
     * PHP non sa chiudere prima la risposta al visitatore).
     * Torna cosa ha fatto.
     */
    public function tick(array $d): string
    {
        $hub = (string) ($d['hub'] ?? '');
        $now = (int) ($d['now'] ?? time());
        $budget = (float) ($d['budget'] ?? 150);
        $allowJobs = (bool) ($d['jobs'] ?? true);
        $short = $allowJobs ? 1.0 : 0.34;      // senza lavori: risposte veloci, il visitatore aspetta
        if (!$this->due($now, $hub)) {
            return 'wait';
        }
        if (!$this->acquire()) {
            return 'busy';
        }
        $post = $d['post'];
        $status = $d['status'];
        $run = $d['run'];
        $base = ['url' => (string) ($d['url'] ?? ''), 'connector' => (string) ($d['version'] ?? ''), 'cms' => 'joomla',
                 'fp' => (string) ($d['fp'] ?? '')];
        try {
            $st = $this->state();
            if ($st['mode'] !== 'relay') {
                $st['ping_at'] = $now;
                $this->save($st);
                $r = $post($hub, $base + ['kind' => 'ping'], (int) max(5, 15 * $short));
                if (!is_array($r) || isset($r['_error'])) {
                    return 'ping-failed';
                }
                $st = $this->apply($st, $r);
                if ($st['mode'] !== 'relay') {
                    return 'ping';
                }
            }
            $st['tick_at'] = $now;
            $this->save($st);

            $start = microtime(true);
            $results = [];
            $refresh = false;
            $wait = false;
            $done = 0;
            // stato completo solo ogni status_every secondi, su richiesta e dopo un lavoro
            $needStatus = ($now - (int) $st['status_at']) >= (int) $st['status_every'] - 30;
            $sentStatus = false;
            $searched = null;      // una sola ricerca degli aggiornamenti per giro
            for ($round = 0; $round < 50; $round++) {
                $over = !$allowJobs || (microtime(true) - $start) > $budget;
                $body = $base + [
                    'kind'    => 'report',
                    'jobs_ok' => $allowJobs,
                    'results' => $results,
                    'wait'    => $wait && !$over,
                    'nojobs'  => $over,
                ];
                if ($needStatus || $refresh) {
                    $body['status'] = $status($refresh);
                    $st['status_at'] = $now;
                    $needStatus = false;
                    $sentStatus = true;
                }
                $r = $post($hub, $body, (int) max(10, ($wait ? 60 : 30) * $short));
                if (!is_array($r) || isset($r['_error'])) {
                    $this->refused($st, $r, $now);
                    return 'report-failed';
                }
                $results = [];
                $refresh = false;
                $st = $this->apply($st, $r);
                $jobs = (isset($r['jobs']) && is_array($r['jobs'])) ? $r['jobs'] : [];
                if (!$jobs) {
                    if (!empty($r['need_status']) && !$sentStatus) {
                        $needStatus = true;     // il pannello non ha uno stato recente
                        continue;
                    }
                    break;
                }
                foreach ($jobs as $job) {
                    if (!is_array($job) || empty($job['id'])) {
                        continue;
                    }
                    if (!$allowJobs || (microtime(true) - $start) > $budget) {
                        // tempo finito prima di cominciarlo: torna in coda per il prossimo giro
                        $results[] = ['id' => (string) $job['id'], 'result' => ['_requeue' => true]];
                        continue;
                    }
                    if (($job['action'] ?? '') === 'refresh') {
                        // ricerca degli aggiornamenti adesso, una per giro: lo stato fresco va con l'esito
                        if ($searched === null) {
                            $searched = $status(true);
                        }
                        $results[] = ['id' => (string) $job['id'], 'result' => ['ok' => true, 'status' => $searched]];
                        $st['status_at'] = $now;
                        $sentStatus = true;
                        continue;
                    }
                    if (!$allowJobs || (microtime(true) - $start) > $budget) {
                        // tempo finito prima di cominciarlo: torna in coda per il prossimo giro
                        $results[] = ['id' => (string) $job['id'], 'result' => ['_requeue' => true]];
                        continue;
                    }
                    if (function_exists('set_time_limit')) {
                        @set_time_limit(300);   // ogni lavoro ha i suoi 5 minuti
                    }
                    try {
                        $res = $run($job, $hub);
                    } catch (\Throwable $e) {
                        $res = ['ok' => false, 'error' => $e->getMessage()];
                    }
                    $results[] = ['id' => (string) $job['id'], 'result' => is_array($res) ? $res : ['ok' => false, 'error' => 'esito vuoto']];
                    $done++;
                    $needStatus = true;
                }
                $wait = true;
            }
            return 'report' . ($done ? ':' . $done : '');
        } finally {
            $this->release();
        }
    }
}

<?php

/**
 * Plugin Name: Sentinel TD Agent
 * Description: Connettore di Sentinel TD: espone stato versioni/update via REST e consente aggiornamenti da remoto. Token e collegamento in Impostazioni → Sentinel TD.
 * Version: 2.26.1
 * Author: Tastiere Digitali
 *
 * INSTALLAZIONE: carica lo zip da Plugin → Aggiungi nuovo → Carica plugin, poi attiva.
 *
 * NOTA SULLA CARTELLA: il connettore funziona identico sia in "td-panopticon"
 * (parco storico) sia in "sentinel-td" (nuove installazioni). Tutti gli
 * identificatori interni - option del token, namespace REST, costanti - restano
 * gli stessi, quindi il pannello parla con entrambe allo stesso modo.
 */

if (!defined('ABSPATH')) {
    exit;
}

if (!defined('TDPANOP_VERSION')) {
    // la versione letta dall'intestazione del plugin stesso: vive in un posto solo
    define('TDPANOP_VERSION', (static function () {
        $head = (string) @file_get_contents(__FILE__, false, null, 0, 2048);
        return preg_match('/^\s*\*\s*Version:\s*(\S+)/m', $head, $m) ? $m[1] : '0';
    })());
}

if (defined('TDPANOP_LOADED')) {
    add_action('admin_notices', function () {
        $msg = 'un\'altra copia del connettore è già attiva su questo sito. Tieni attiva una sola copia (disattiva e rimuovi quella non usata).';
        echo '<div class="notice notice-warning"><p><strong>Sentinel TD Agent</strong>: '
           . esc_html(function_exists('tdpanop__') ? tdpanop__($msg) : $msg) . '</p></div>';
    });
    return;
}
define('TDPANOP_LOADED', true);

const TDPANOP_OPT = 'td_panopticon_token';

/* ---------------------------------------------------------------------------
 * Collegamento a Sentinel TD PRE-CONFIGURATO.
 * Compila la chiave qui sotto UNA volta (dal pannello: Connettori → Registrazione
 * automatica → Copia) e ri-zippa: ogni sito su cui installi questo plugin mostrera'
 * direttamente "scegli cartella → Collega", senza chiedere URL ne' chiave.
 * Se lasci la chiave vuota, la pagina chiede URL+chiave come prima (fallback).
 * ------------------------------------------------------------------------- */
const TDPANOP_HUB_URL = '';   // vuoto: si imposta dal backend del sito, oppure lo compila Sentinel nel pacchetto che genera
const TDPANOP_HUB_KEY = '';   // idem: mai nel repository

/*
 * Tutto il resto del connettore sta in un blocco condizionale. Motivo: PHP registra
 * le funzioni di primo livello mentre COMPILA il file, prima di eseguire la guardia
 * qui sopra; con due copie del connettore attive (vecchia cartella "td-panopticon" e
 * nuova "sentinel-td") la seconda andava in errore fatale per funzioni ridichiarate e
 * il sito diventava bianco. Dentro un blocco le funzioni vengono registrate solo
 * quando il blocco viene eseguito, e la seconda copia esce prima di arrivarci.
 */
if (!function_exists('tdpanop_token')) {

/* ---------------------------------------------------------------------------
 * Lingue: italiano, inglese, francese, tedesco.
 * I testi nel codice sono in italiano; per le altre lingue si usa la tabella qui
 * sotto, scelta in base alla lingua dell'utente in amministrazione. Le lingue non
 * previste usano l'inglese. Tabella dentro il file: il connettore resta un file
 * unico, come lo genera il pannello.
 * ------------------------------------------------------------------------- */
function tdpanop__($text)
{
    static $map = null;
    if ($map === null) {
        $locale = function_exists('determine_locale') ? determine_locale() : get_locale();
        $lang   = strtolower(substr((string) $locale, 0, 2));
        $all    = tdpanop_translations();
        $map    = ($lang === 'it') ? [] : ($all[$lang] ?? $all['en']);
    }
    return isset($map[$text]) ? $map[$text] : $text;
}

function tdpanop_translations()
{
    return [
        'en' => [
            'Connettore di Sentinel TD: espone stato versioni/update via REST e consente aggiornamenti da remoto. Token e collegamento in Impostazioni → Sentinel TD.' => 'Sentinel TD connector: exposes versions and available updates over REST and allows remote updates. Token and connection in Settings → Sentinel TD.',
            'un\'altra copia del connettore è già attiva su questo sito. Tieni attiva una sola copia (disattiva e rimuovi quella non usata).' => 'another copy of the connector is already active on this site. Keep only one copy active (deactivate and remove the unused one).',
            'Token rigenerato. Aggiornalo anche in Sentinel TD.' => 'Token regenerated. Update it in Sentinel TD as well.',
            'Collegamento fallito: %s' => 'Connection failed: %s',
            'Questo sito è GIÀ presente in Sentinel TD: nessuna modifica fatta.' => 'This site is ALREADY in Sentinel TD: nothing was changed.',
            'Sito già presente in Sentinel TD: collegamento aggiornato, il pannello ora usa il token attuale di questo sito.' => 'Site already in Sentinel TD: connection updated, the panel now uses this site\'s current token.',
            'Sito collegato a Sentinel TD.' => 'Site connected to Sentinel TD.',
            'Sito collegato a Sentinel TD nella cartella "%s".' => 'Site connected to Sentinel TD in the folder "%s".',
            'Sito collegato a Sentinel TD con auto-update attivo.' => 'Site connected to Sentinel TD with automatic updates on.',
            'Sito collegato a Sentinel TD nella cartella "%s" con auto-update attivo.' => 'Site connected to Sentinel TD in the folder "%s" with automatic updates on.',
            'Collegamento rifiutato (HTTP %d): %s' => 'Connection refused (HTTP %d): %s',
            'verifica URL e chiave' => 'check the URL and the key',
            'chiave non valida o pannello non raggiungibile (HTTP %d)' => 'invalid key or panel not reachable (HTTP %d)',
            'Copia questo token e incollalo quando aggiungi il sito in %s.' => 'Copy this token and paste it when you add the site in %s.',
            'Token del sito' => 'Site token',
            'Copia' => 'Copy',
            'Rigenerare il token? Dovrai aggiornarlo anche in Sentinel TD, altrimenti i check falliranno.' => 'Regenerate the token? You will have to update it in Sentinel TD too, otherwise the checks will fail.',
            'Rigenera token' => 'Regenerate token',
            'Collega a Sentinel TD' => 'Connect to Sentinel TD',
            'Pannello: %s (pre-configurato). Scegli la cartella e collega.' => 'Panel: %s (pre-configured). Choose the folder and connect.',
            'Registra questo sito nel pannello da solo: niente copia-incolla del token. Se il sito è già presente in Sentinel TD, non viene modificato nulla.' => 'Register this site in the panel by itself: no token copy and paste. If the site is already in Sentinel TD, nothing is changed.',
            'URL pannello' => 'Panel URL',
            'Chiave di registrazione' => 'Registration key',
            'dal pannello: Impostazioni → Connettori → Registrazione automatica' => 'from the panel: Settings → Connectors → Automatic registration',
            'https://sentinel.tuodominio.it' => 'https://sentinel.example.com',
            'Salva e carica cartelle' => 'Save and load folders',
            'Impossibile leggere le cartelle: %s' => 'Unable to read the folders: %s',
            'Cartella' => 'Folder',
            '— Nessuna cartella —' => '— No folder —',
            'oppure nuova:' => 'or a new one:',
            'es. ClienteX' => 'e.g. ClientX',
            'Auto-update' => 'Auto-update',
            'Attiva gli aggiornamenti automatici gestiti da Sentinel TD per questo sito' => 'Turn on automatic updates managed by Sentinel TD for this site',
            'Collega questo sito a Sentinel TD' => 'Connect this site to Sentinel TD',
        ],
        'fr' => [
            'Connettore di Sentinel TD: espone stato versioni/update via REST e consente aggiornamenti da remoto. Token e collegamento in Impostazioni → Sentinel TD.' => 'Connecteur Sentinel TD : expose les versions et les mises à jour disponibles via REST et permet les mises à jour à distance. Jeton et connexion dans Réglages → Sentinel TD.',
            'un\'altra copia del connettore è già attiva su questo sito. Tieni attiva una sola copia (disattiva e rimuovi quella non usata).' => 'une autre copie du connecteur est déjà active sur ce site. Ne gardez qu\'une seule copie active (désactivez et supprimez celle qui ne sert pas).',
            'Token rigenerato. Aggiornalo anche in Sentinel TD.' => 'Jeton régénéré. Mettez-le aussi à jour dans Sentinel TD.',
            'Collegamento fallito: %s' => 'Échec de la connexion : %s',
            'Questo sito è GIÀ presente in Sentinel TD: nessuna modifica fatta.' => 'Ce site est DÉJÀ présent dans Sentinel TD : aucune modification effectuée.',
            'Sito già presente in Sentinel TD: collegamento aggiornato, il pannello ora usa il token attuale di questo sito.' => 'Site déjà présent dans Sentinel TD : connexion mise à jour, le panneau utilise maintenant le jeton actuel de ce site.',
            'Sito collegato a Sentinel TD.' => 'Site connecté à Sentinel TD.',
            'Sito collegato a Sentinel TD nella cartella "%s".' => 'Site connecté à Sentinel TD dans le dossier « %s ».',
            'Sito collegato a Sentinel TD con auto-update attivo.' => 'Site connecté à Sentinel TD avec les mises à jour automatiques activées.',
            'Sito collegato a Sentinel TD nella cartella "%s" con auto-update attivo.' => 'Site connecté à Sentinel TD dans le dossier « %s » avec les mises à jour automatiques activées.',
            'Collegamento rifiutato (HTTP %d): %s' => 'Connexion refusée (HTTP %d) : %s',
            'verifica URL e chiave' => 'vérifiez l\'URL et la clé',
            'chiave non valida o pannello non raggiungibile (HTTP %d)' => 'clé non valide ou panneau injoignable (HTTP %d)',
            'Copia questo token e incollalo quando aggiungi il sito in %s.' => 'Copiez ce jeton et collez-le lorsque vous ajoutez le site dans %s.',
            'Token del sito' => 'Jeton du site',
            'Copia' => 'Copier',
            'Rigenerare il token? Dovrai aggiornarlo anche in Sentinel TD, altrimenti i check falliranno.' => 'Régénérer le jeton ? Vous devrez aussi le mettre à jour dans Sentinel TD, sinon les vérifications échoueront.',
            'Rigenera token' => 'Régénérer le jeton',
            'Collega a Sentinel TD' => 'Connecter à Sentinel TD',
            'Pannello: %s (pre-configurato). Scegli la cartella e collega.' => 'Panneau : %s (préconfiguré). Choisissez le dossier et connectez.',
            'Registra questo sito nel pannello da solo: niente copia-incolla del token. Se il sito è già presente in Sentinel TD, non viene modificato nulla.' => 'Enregistrez ce site dans le panneau directement : pas de copier-coller du jeton. Si le site est déjà présent dans Sentinel TD, rien n\'est modifié.',
            'URL pannello' => 'URL du panneau',
            'Chiave di registrazione' => 'Clé d\'enregistrement',
            'dal pannello: Impostazioni → Connettori → Registrazione automatica' => 'depuis le panneau : Paramètres → Connecteurs → Enregistrement automatique',
            'https://sentinel.tuodominio.it' => 'https://sentinel.exemple.fr',
            'Salva e carica cartelle' => 'Enregistrer et charger les dossiers',
            'Impossibile leggere le cartelle: %s' => 'Impossible de lire les dossiers : %s',
            'Cartella' => 'Dossier',
            '— Nessuna cartella —' => '— Aucun dossier —',
            'oppure nuova:' => 'ou un nouveau :',
            'es. ClienteX' => 'ex. ClientX',
            'Auto-update' => 'Mise à jour auto',
            'Attiva gli aggiornamenti automatici gestiti da Sentinel TD per questo sito' => 'Activer les mises à jour automatiques gérées par Sentinel TD pour ce site',
            'Collega questo sito a Sentinel TD' => 'Connecter ce site à Sentinel TD',
        ],
        'de' => [
            'Connettore di Sentinel TD: espone stato versioni/update via REST e consente aggiornamenti da remoto. Token e collegamento in Impostazioni → Sentinel TD.' => 'Sentinel-TD-Connector: stellt Versionen und verfügbare Updates per REST bereit und ermöglicht Updates aus der Ferne. Token und Verbindung unter Einstellungen → Sentinel TD.',
            'un\'altra copia del connettore è già attiva su questo sito. Tieni attiva una sola copia (disattiva e rimuovi quella non usata).' => 'eine weitere Kopie des Connectors ist auf dieser Website bereits aktiv. Lassen Sie nur eine Kopie aktiv (deaktivieren und entfernen Sie die nicht genutzte).',
            'Token rigenerato. Aggiornalo anche in Sentinel TD.' => 'Token neu erzeugt. Aktualisieren Sie ihn auch in Sentinel TD.',
            'Collegamento fallito: %s' => 'Verbindung fehlgeschlagen: %s',
            'Questo sito è GIÀ presente in Sentinel TD: nessuna modifica fatta.' => 'Diese Website ist BEREITS in Sentinel TD vorhanden: es wurde nichts geändert.',
            'Sito già presente in Sentinel TD: collegamento aggiornato, il pannello ora usa il token attuale di questo sito.' => 'Website bereits in Sentinel TD: Verbindung aktualisiert, das Panel verwendet jetzt den aktuellen Token dieser Website.',
            'Sito collegato a Sentinel TD.' => 'Website mit Sentinel TD verbunden.',
            'Sito collegato a Sentinel TD nella cartella "%s".' => 'Website mit Sentinel TD im Ordner „%s“ verbunden.',
            'Sito collegato a Sentinel TD con auto-update attivo.' => 'Website mit Sentinel TD verbunden, automatische Updates aktiv.',
            'Sito collegato a Sentinel TD nella cartella "%s" con auto-update attivo.' => 'Website mit Sentinel TD im Ordner „%s“ verbunden, automatische Updates aktiv.',
            'Collegamento rifiutato (HTTP %d): %s' => 'Verbindung abgelehnt (HTTP %d): %s',
            'verifica URL e chiave' => 'URL und Schlüssel prüfen',
            'chiave non valida o pannello non raggiungibile (HTTP %d)' => 'ungültiger Schlüssel oder Panel nicht erreichbar (HTTP %d)',
            'Copia questo token e incollalo quando aggiungi il sito in %s.' => 'Kopieren Sie diesen Token und fügen Sie ihn ein, wenn Sie die Website in %s hinzufügen.',
            'Token del sito' => 'Website-Token',
            'Copia' => 'Kopieren',
            'Rigenerare il token? Dovrai aggiornarlo anche in Sentinel TD, altrimenti i check falliranno.' => 'Token neu erzeugen? Sie müssen ihn auch in Sentinel TD aktualisieren, sonst schlagen die Prüfungen fehl.',
            'Rigenera token' => 'Token neu erzeugen',
            'Collega a Sentinel TD' => 'Mit Sentinel TD verbinden',
            'Pannello: %s (pre-configurato). Scegli la cartella e collega.' => 'Panel: %s (vorkonfiguriert). Ordner wählen und verbinden.',
            'Registra questo sito nel pannello da solo: niente copia-incolla del token. Se il sito è già presente in Sentinel TD, non viene modificato nulla.' => 'Diese Website direkt im Panel registrieren: kein Kopieren des Tokens nötig. Ist die Website bereits in Sentinel TD vorhanden, wird nichts geändert.',
            'URL pannello' => 'Panel-URL',
            'Chiave di registrazione' => 'Registrierungsschlüssel',
            'dal pannello: Impostazioni → Connettori → Registrazione automatica' => 'im Panel: Einstellungen → Connectoren → Automatische Registrierung',
            'https://sentinel.tuodominio.it' => 'https://sentinel.beispiel.de',
            'Salva e carica cartelle' => 'Speichern und Ordner laden',
            'Impossibile leggere le cartelle: %s' => 'Ordner können nicht gelesen werden: %s',
            'Cartella' => 'Ordner',
            '— Nessuna cartella —' => '— Kein Ordner —',
            'oppure nuova:' => 'oder neu:',
            'es. ClienteX' => 'z. B. KundeX',
            'Auto-update' => 'Auto-Update',
            'Attiva gli aggiornamenti automatici gestiti da Sentinel TD per questo sito' => 'Von Sentinel TD verwaltete automatische Updates für diese Website aktivieren',
            'Collega questo sito a Sentinel TD' => 'Diese Website mit Sentinel TD verbinden',
        ],
    ];
}

// descrizione nella lista plugin, nella lingua dell'utente
add_filter('all_plugins', function ($plugins) {
    foreach ($plugins as $file => $data) {
        if (isset($data['Name']) && $data['Name'] === 'Sentinel TD Agent') {
            $plugins[$file]['Description'] = tdpanop__($data['Description']);
        }
    }
    return $plugins;
});

/*
 * Una sola copia attiva per sito. Se il connettore e' gia' caricato (caso tipico:
 * cartella vecchia "td-panopticon" e nuova "sentinel-td" installate entrambe),
 * questa copia esce SUBITO: senza questa guardia PHP andrebbe in fatal error per
 * ridichiarazione di costanti e funzioni, e il sito resterebbe bianco.
 */


/* ---------------------------------------------------------------------------
 * Token: generazione all'attivazione
 * ------------------------------------------------------------------------- */
register_activation_hook(__FILE__, function () {
    if (!get_option(TDPANOP_OPT)) {
        add_option(TDPANOP_OPT, wp_generate_password(48, false, false));
    }
});

function tdpanop_token(): string
{
    $t = get_option(TDPANOP_OPT);
    if (!$t) {
        // Generazione ATOMICA, a prova di object cache (Redis).
        // Il vecchio get+update aveva una race: due richieste quasi simultanee (es. il
        // POST "Collega" e il render della pagina) su worker FPM diversi, con la cache
        // alloptions non coerente, generavano DUE token diversi - uno finiva al pannello,
        // l'altro restava sul sito -> mismatch dalla nascita (caso reale: Russ Traslochi).
        // 1) butta la cache e rileggi il valore VERO dal DB
        wp_cache_delete(TDPANOP_OPT, 'options');
        wp_cache_delete('alloptions', 'options');
        $t = get_option(TDPANOP_OPT);
        if (!$t) {
            $t = wp_generate_password(48, false, false);
            // 2) add_option e' un INSERT: se un'altra richiesta ha gia' creato il token,
            //    fallisce senza sovrascrivere -> vince SEMPRE il primo, mai due token.
            //    autoload 'no': fuori da alloptions, fuori dalle sue race per sempre.
            if (!add_option(TDPANOP_OPT, $t, '', 'no')) {
                wp_cache_delete(TDPANOP_OPT, 'options');
                wp_cache_delete('alloptions', 'options');
                $t = (string) get_option(TDPANOP_OPT);
            }
        }
    }
    return $t;
}

/* ---------------------------------------------------------------------------
 * Pagina backend: Impostazioni → Sentinel TD
 * ------------------------------------------------------------------------- */
add_action('admin_menu', function () {
    add_options_page('Sentinel TD', 'Sentinel TD', 'manage_options', 'td-panopticon', 'tdpanop_admin_page');
});

function tdpanop_admin_page()
{
    if (!current_user_can('manage_options')) {
        return;
    }

    // rigenera
    if (isset($_POST['tdpanop_rotate']) && check_admin_referer('tdpanop_rotate')) {
        update_option(TDPANOP_OPT, wp_generate_password(48, false, false));
        echo '<div class="notice notice-success is-dismissible"><p>' . esc_html(tdpanop__('Token rigenerato. Aggiornalo anche in Sentinel TD.')) . '</p></div>';
    }

    // ---- Collega a Sentinel TD (auto-registrazione) -------------------------------
    // Le costanti hardcodate hanno la precedenza; le option restano come fallback
    // per zip non pre-configurati.
    $hub_hardcoded = (TDPANOP_HUB_URL !== '' && TDPANOP_HUB_KEY !== '');
    $hub_url = $hub_hardcoded ? TDPANOP_HUB_URL : get_option('td_panopticon_hub_url', '');
    $hub_key = $hub_hardcoded ? TDPANOP_HUB_KEY : get_option('td_panopticon_hub_key', '');
    $hub_tags = [];
    $hub_tags_err = '';

    // salva hub url/chiave e carica le cartelle (solo se NON hardcodato)
    if (!$hub_hardcoded && isset($_POST['tdpanop_hub_save']) && check_admin_referer('tdpanop_hub')) {
        $hub_url = esc_url_raw(trim((string) ($_POST['tdpanop_hub_url'] ?? '')));
        $hub_key = sanitize_text_field((string) ($_POST['tdpanop_hub_key'] ?? ''));
        update_option('td_panopticon_hub_url', $hub_url);
        update_option('td_panopticon_hub_key', $hub_key);
    }

    // registra il sito
    if (isset($_POST['tdpanop_hub_register']) && check_admin_referer('tdpanop_hub')) {
        $hub_url = $hub_hardcoded ? TDPANOP_HUB_URL : get_option('td_panopticon_hub_url', '');
        $hub_key = $hub_hardcoded ? TDPANOP_HUB_KEY : get_option('td_panopticon_hub_key', '');
        $tag_sel = sanitize_text_field((string) ($_POST['tdpanop_hub_tag'] ?? ''));
        $tag_new = sanitize_text_field((string) ($_POST['tdpanop_hub_tag_new'] ?? ''));
        $tag     = $tag_new !== '' ? $tag_new : ($tag_sel === '__none' ? '' : $tag_sel);
        $auto    = !empty($_POST['tdpanop_hub_auto']);
        $resp = wp_remote_post(rtrim($hub_url, '/') . '/api/agent/register', [
            'timeout' => 30,
            'headers' => ['Content-Type' => 'application/json'],
            'body'    => wp_json_encode([
                'key'         => $hub_key,
                'name'        => get_bloginfo('name'),
                'url'         => home_url(),
                'token'       => tdpanop_token(),
                'tags'        => $tag,
                'auto_update' => $auto,
                // URL di login REALE (con WPS Hide Login & simili restituisce quello
                // custom, es. /login): il pannello lo usa come "URL admin" del sito.
                'admin_url'   => wp_login_url(),
            ]),
        ]);
        if (is_wp_error($resp)) {
            echo '<div class="notice notice-error"><p>' . esc_html(sprintf(tdpanop__('Collegamento fallito: %s'), $resp->get_error_message())) . '</p></div>';
        } else {
            $code = (int) wp_remote_retrieve_response_code($resp);
            $body = json_decode((string) wp_remote_retrieve_body($resp), true);
            if ($code === 200 && !empty($body['ok'])) {
                if (!empty($body['existed']) && !empty($body['realigned'])) {
                    // il pannello ha riallineato il collegamento (tipico dopo una reinstallazione)
                    $msg = esc_html(tdpanop__('Sito già presente in Sentinel TD: collegamento aggiornato, il pannello ora usa il token attuale di questo sito.'));
                } elseif (!empty($body['existed'])) {
                    $msg = esc_html(tdpanop__('Questo sito è GIÀ presente in Sentinel TD: nessuna modifica fatta.'));
                } elseif ($tag !== '') {
                    $msg = esc_html(sprintf(tdpanop__($auto ? 'Sito collegato a Sentinel TD nella cartella "%s" con auto-update attivo.' : 'Sito collegato a Sentinel TD nella cartella "%s".'), $tag));
                } else {
                    $msg = esc_html(tdpanop__($auto ? 'Sito collegato a Sentinel TD con auto-update attivo.' : 'Sito collegato a Sentinel TD.'));
                }
                echo '<div class="notice notice-success is-dismissible"><p>' . $msg . '</p></div>';
            } else {
                $detail = is_array($body) ? ($body['detail'] ?? '') : '';
                echo '<div class="notice notice-error"><p>' . esc_html(sprintf(tdpanop__('Collegamento rifiutato (HTTP %d): %s'), $code, $detail ?: tdpanop__('verifica URL e chiave'))) . '</p></div>';
            }
        }
    }

    // carica le cartelle dal pannello (se URL+chiave presenti)
    if ($hub_url !== '' && $hub_key !== '') {
        $r = wp_remote_get(rtrim($hub_url, '/') . '/api/agent/tags?key=' . rawurlencode($hub_key), ['timeout' => 20]);
        if (is_wp_error($r)) {
            $hub_tags_err = $r->get_error_message();
        } elseif ((int) wp_remote_retrieve_response_code($r) === 200) {
            $b = json_decode((string) wp_remote_retrieve_body($r), true);
            $hub_tags = (is_array($b) && !empty($b['tags']) && is_array($b['tags'])) ? $b['tags'] : [];
        } else {
            $hub_tags_err = sprintf(tdpanop__('chiave non valida o pannello non raggiungibile (HTTP %d)'), (int) wp_remote_retrieve_response_code($r));
        }
    }

    $token = tdpanop_token();
?>
    <div class="wrap">
        <h1>Sentinel TD</h1>
        <p><?php echo sprintf(esc_html(tdpanop__('Copia questo token e incollalo quando aggiungi il sito in %s.')), '<strong>Sentinel TD</strong>'); ?></p>
        <table class="form-table">
            <tr>
                <th scope="row"><label for="tdpanop_tok"><?php echo esc_html(tdpanop__('Token del sito')); ?></label></th>
                <td>
                    <input type="text" id="tdpanop_tok" readonly value="<?php echo esc_attr($token); ?>"
                        style="width:480px;max-width:100%;font-family:monospace" onclick="this.select()">
                    <button type="button" class="button" onclick="navigator.clipboard.writeText(document.getElementById('tdpanop_tok').value)"><?php echo esc_html(tdpanop__('Copia')); ?></button>
                </td>
            </tr>
            <tr>
                <th scope="row">Endpoint</th>
                <td><code><?php echo esc_html(rest_url('tdpanopticon/v1/status')); ?></code></td>
            </tr>
        </table>
        <form method="post" onsubmit="return confirm('<?php echo esc_js(tdpanop__('Rigenerare il token? Dovrai aggiornarlo anche in Sentinel TD, altrimenti i check falliranno.')); ?>');">
            <?php wp_nonce_field('tdpanop_rotate'); ?>
            <input type="submit" name="tdpanop_rotate" class="button button-secondary" value="<?php echo esc_attr(tdpanop__('Rigenera token')); ?>">
        </form>

        <hr style="margin:24px 0">
        <h2><?php echo esc_html(tdpanop__('Collega a Sentinel TD')); ?></h2>
        <?php if ($hub_hardcoded) : ?>
            <p><?php echo sprintf(esc_html(tdpanop__('Pannello: %s (pre-configurato). Scegli la cartella e collega.')), '<code>' . esc_html(TDPANOP_HUB_URL) . '</code>'); ?></p>
        <?php else : ?>
            <p><?php echo esc_html(tdpanop__('Registra questo sito nel pannello da solo: niente copia-incolla del token. Se il sito è già presente in Sentinel TD, non viene modificato nulla.')); ?></p>

            <form method="post" style="margin-bottom:14px">
                <?php wp_nonce_field('tdpanop_hub'); ?>
                <table class="form-table">
                    <tr>
                        <th scope="row"><label for="tdpanop_hub_url"><?php echo esc_html(tdpanop__('URL pannello')); ?></label></th>
                        <td><input type="url" id="tdpanop_hub_url" name="tdpanop_hub_url" value="<?php echo esc_attr($hub_url); ?>"
                                placeholder="<?php echo esc_attr(tdpanop__('https://sentinel.tuodominio.it')); ?>" style="width:420px;max-width:100%"></td>
                    </tr>
                    <tr>
                        <th scope="row"><label for="tdpanop_hub_key"><?php echo esc_html(tdpanop__('Chiave di registrazione')); ?></label></th>
                        <td><input type="text" id="tdpanop_hub_key" name="tdpanop_hub_key" value="<?php echo esc_attr($hub_key); ?>"
                                style="width:420px;max-width:100%;font-family:monospace"
                                placeholder="<?php echo esc_attr(tdpanop__('dal pannello: Impostazioni → Connettori → Registrazione automatica')); ?>"></td>
                    </tr>
                </table>
                <input type="submit" name="tdpanop_hub_save" class="button" value="<?php echo esc_attr(tdpanop__('Salva e carica cartelle')); ?>">
            </form>
        <?php endif; ?>

        <?php if ($hub_url !== '' && $hub_key !== '') : ?>
            <?php if ($hub_tags_err !== '') : ?>
                <div class="notice notice-warning inline">
                    <p><?php echo esc_html(sprintf(tdpanop__('Impossibile leggere le cartelle: %s'), $hub_tags_err)); ?></p>
                </div>
            <?php else : ?>
                <form method="post">
                    <?php wp_nonce_field('tdpanop_hub'); ?>
                    <table class="form-table">
                        <tr>
                            <th scope="row"><label for="tdpanop_hub_tag"><?php echo esc_html(tdpanop__('Cartella')); ?></label></th>
                            <td>
                                <select id="tdpanop_hub_tag" name="tdpanop_hub_tag">
                                    <option value="__none"><?php echo esc_html(tdpanop__('— Nessuna cartella —')); ?></option>
                                    <?php foreach ($hub_tags as $t) : ?>
                                        <option value="<?php echo esc_attr($t); ?>"><?php echo esc_html($t); ?></option>
                                    <?php endforeach; ?>
                                </select>
                                <span style="margin:0 8px"><?php echo esc_html(tdpanop__('oppure nuova:')); ?></span>
                                <input type="text" name="tdpanop_hub_tag_new" placeholder="<?php echo esc_attr(tdpanop__('es. ClienteX')); ?>" style="width:180px">
                            </td>
                        </tr>
                        <tr>
                            <th scope="row"><?php echo esc_html(tdpanop__('Auto-update')); ?></th>
                            <td><label><input type="checkbox" name="tdpanop_hub_auto" value="1" checked>
                                    <?php echo esc_html(tdpanop__('Attiva gli aggiornamenti automatici gestiti da Sentinel TD per questo sito')); ?></label></td>
                        </tr>
                    </table>
                    <input type="submit" name="tdpanop_hub_register" class="button button-primary" value="<?php echo esc_attr(tdpanop__('Collega questo sito a Sentinel TD')); ?>">
                </form>
            <?php endif; ?>
        <?php endif; ?>
    </div>
<?php
}

/* ---------------------------------------------------------------------------
 * REST API
 * ------------------------------------------------------------------------- */
add_action('rest_api_init', function () {
    register_rest_route('tdpanopticon/v1', '/status', [
        'methods'  => 'GET',
        'callback' => 'tdpanop_status',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/autologin', [
        'methods'  => 'POST',
        'callback' => 'tdpanop_autologin',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/update', [
        'methods'  => 'POST',
        'callback' => 'tdpanop_update',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/install', [
        'methods'  => 'POST',
        'callback' => 'tdpanop_install',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/uninstall', [
        'methods'  => 'POST',
        'callback' => 'tdpanop_uninstall',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/diagnostics', [
        'methods'  => 'GET',
        'callback' => 'tdpanop_diagnostics',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/package', [
        'methods'  => 'GET',
        'callback' => 'tdpanop_package_export',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/backups', [
        'methods'  => 'GET',
        'callback' => 'tdpanop_backups_list',
        'permission_callback' => 'tdpanop_auth',
    ]);
    register_rest_route('tdpanopticon/v1', '/rollback', [
        'methods'  => 'POST',
        'callback' => 'tdpanop_rollback',
        'permission_callback' => 'tdpanop_auth',
    ]);
});

/* ---------------------------------------------------------------------------
 * Copia prima dell'aggiornamento e ripristino.
 * Prima di aggiornare un plugin o un tema, la sua cartella viene zippata in
 * wp-content/uploads/sentinel-backups/ ({tipo}-{slug}-{versione}-{data}.zip). Il pannello
 * puo' poi RIPRISTINARE quella versione con un clic, o da solo se la home si rompe dopo
 * l'aggiornamento. Limiti: niente copia oltre i 300 MB (temi e plugin con dentro media) e
 * se lo spazio non basta; si tengono le ultime 2 copie per elemento e niente oltre 14 giorni.
 * Senza copia l'aggiornamento parte comunque: la risposta dice perche' manca.
 * ------------------------------------------------------------------------- */
if (!defined('TDPANOP_BACKUP_MAX_BYTES')) {
    define('TDPANOP_BACKUP_MAX_BYTES', 300 * 1048576);
    define('TDPANOP_BACKUP_KEEP', 2);
    define('TDPANOP_BACKUP_DAYS', 14);
}

function tdpanop_backup_dir(): string
{
    $up  = wp_upload_dir(null, false);
    $dir = rtrim((string) ($up['basedir'] ?? WP_CONTENT_DIR . '/uploads'), '/') . '/sentinel-backups';
    if (!is_dir($dir)) {
        @mkdir($dir, 0755, true);
    }
    if (is_dir($dir)) {
        if (!is_file($dir . '/index.php')) {
            @file_put_contents($dir . '/index.php', "<?php\n// Silence is golden.\n");
        }
        if (!is_file($dir . '/.htaccess')) {
            @file_put_contents($dir . '/.htaccess', "<IfModule mod_authz_core.c>\nRequire all denied\n</IfModule>\n<IfModule !mod_authz_core.c>\nDeny from all\n</IfModule>\n");
        }
    }
    return $dir;
}

function tdpanop_item_dir(string $type, string $slug): string
{
    $slug = basename($slug);
    if ($type === 'plugin') {
        return rtrim(WP_PLUGIN_DIR, '/') . '/' . $slug;
    }
    if ($type === 'theme') {
        return rtrim(get_theme_root(), '/') . '/' . $slug;
    }
    return '';
}

function tdpanop_item_version(string $type, string $slug): string
{
    $slug = basename($slug);
    if ($type === 'plugin') {
        require_once ABSPATH . 'wp-admin/includes/plugin.php';
        $file = tdpanop_plugin_file_by_slug($slug);
        if ($file && is_file(WP_PLUGIN_DIR . '/' . $file)) {
            $d = get_plugin_data(WP_PLUGIN_DIR . '/' . $file, false, false);
            return (string) ($d['Version'] ?? '');
        }
        return '';
    }
    $t = wp_get_theme($slug);
    return $t->exists() ? (string) $t->get('Version') : '';
}

/** Ultima copia fatta in questa richiesta: finisce nella risposta dell'aggiornamento. */
function tdpanop_backup_fields(?array $set = null): array
{
    static $last = null;
    if ($set !== null) {
        $last = $set;
    }
    return ['backup' => $last['file'] ?? null, 'backup_error' => $last['error'] ?? ''];
}

function tdpanop_backup_item(string $type, string $slug): array
{
    $src = tdpanop_item_dir($type, $slug);
    $out = ['file' => null, 'error' => ''];
    if ($src === '' || !is_dir($src)) {
        $out['error'] = 'cartella non trovata';
        tdpanop_backup_fields($out);
        return $out;
    }
    $dir = tdpanop_backup_dir();
    if (!is_dir($dir) || !wp_is_writable($dir)) {
        $out['error'] = 'cartella delle copie non scrivibile';
        tdpanop_backup_fields($out);
        return $out;
    }
    // peso della cartella: oltre il limite niente copia (temi e plugin con dentro media)
    $bytes = 0;
    try {
        $it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($src, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::LEAVES_ONLY, RecursiveIteratorIterator::CATCH_GET_CHILD);
        foreach ($it as $f) {
            if ($f->isFile()) {
                $bytes += (int) $f->getSize();
                if ($bytes > TDPANOP_BACKUP_MAX_BYTES) {
                    $out['error'] = 'cartella oltre ' . (TDPANOP_BACKUP_MAX_BYTES / 1048576) . ' MB';
                    tdpanop_backup_fields($out);
                    return $out;
                }
            }
        }
    } catch (\Throwable $e) {
        $out['error'] = 'cartella non leggibile';
        tdpanop_backup_fields($out);
        return $out;
    }
    // Spazio VERO dell'account (prova di scrittura, con cache di 15 minuti), non il disco del
    // server: su un hosting con quota la copia non deve mangiarsi lo spazio che serve
    // all'aggiornamento. Serve posto per la copia e per scaricare ed estrarre la versione nuova.
    $need = (int) min(300, ceil($bytes * 2.5 / 1048576) + 20);
    $sp = tdpanop_space_check($need);
    if (empty($sp['ok'])) {
        $out['error'] = 'spazio dell\'account insufficiente: niente copia, lo spazio resta all\'aggiornamento';
        tdpanop_backup_fields($out);
        return $out;
    }
    $ver  = preg_replace('/[^0-9A-Za-z._-]/', '_', tdpanop_item_version($type, $slug) ?: 'x');
    $name = sprintf('%s-%s-%s-%s.zip', $type, basename($slug), $ver, gmdate('Ymd-His'));
    $path = $dir . '/' . $name;
    $okZip = false;
    try {
        if (class_exists('ZipArchive')) {
            $z = new ZipArchive();
            if ($z->open($path, ZipArchive::CREATE | ZipArchive::OVERWRITE) === true) {
                $base = basename($src);
                $it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($src, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::SELF_FIRST);
                foreach ($it as $f) {
                    $rel = $base . '/' . ltrim(substr($f->getPathname(), strlen($src)), '/\\');
                    $f->isDir() ? $z->addEmptyDir($rel) : $z->addFile($f->getPathname(), $rel);
                }
                $okZip = $z->close();
            }
        } else {
            require_once ABSPATH . 'wp-admin/includes/class-pclzip.php';
            $z = new PclZip($path);
            $okZip = (bool) $z->create($src, PCLZIP_OPT_REMOVE_PATH, dirname($src));
        }
    } catch (\Throwable $e) {
        $okZip = false;
    }
    if (!$okZip || !is_file($path) || filesize($path) < 100) {
        @unlink($path);
        $out['error'] = 'zip non riuscito';
        tdpanop_backup_fields($out);
        return $out;
    }
    $out['file'] = $name;
    $out['bytes'] = (int) filesize($path);
    tdpanop_backup_fields($out);
    tdpanop_backups_prune($type, $slug);
    return $out;
}

/** Tiene le ultime N copie per elemento e niente oltre TDPANOP_BACKUP_DAYS giorni. */
function tdpanop_backups_prune(string $type, string $slug): void
{
    $dir = tdpanop_backup_dir();
    $all = tdpanop_backups_for($type, $slug);
    foreach (array_slice($all, TDPANOP_BACKUP_KEEP) as $b) {
        @unlink($dir . '/' . $b['file']);
    }
    foreach ((array) glob($dir . '/*.zip') as $f) {
        if (filemtime($f) < time() - TDPANOP_BACKUP_DAYS * 86400) {
            @unlink($f);
        }
    }
}

/** Copie di un elemento, dalla piu' recente. */
function tdpanop_backups_for(string $type, string $slug): array
{
    $dir = tdpanop_backup_dir();
    $prefix = $type . '-' . basename($slug) . '-';
    $out = [];
    foreach ((array) glob($dir . '/' . str_replace(['[', ']'], ['\\[', '\\]'], $prefix) . '*.zip') as $f) {
        $name = basename($f);
        if (!preg_match('/^' . preg_quote($prefix, '/') . '(.+)-(\d{8}-\d{6})\.zip$/', $name, $m)) {
            continue;
        }
        $out[] = ['file' => $name, 'version' => $m[1], 'at' => gmdate('c', filemtime($f)), 'bytes' => (int) filesize($f)];
    }
    usort($out, static function ($a, $b) { return strcmp($b['at'], $a['at']); });
    return $out;
}

function tdpanop_backups_list(WP_REST_Request $req)
{
    $type = (string) $req->get_param('type');
    $slug = (string) $req->get_param('slug');
    if (!in_array($type, ['plugin', 'theme'], true) || $slug === '') {
        return new WP_REST_Response(['ok' => false, 'error' => 'type e slug obbligatori'], 400);
    }
    return new WP_REST_Response(['ok' => true, 'backups' => tdpanop_backups_for($type, $slug),
                                 'keep' => TDPANOP_BACKUP_KEEP, 'days' => TDPANOP_BACKUP_DAYS], 200);
}

/**
 * Ripristina una copia: la cartella attuale viene messa da parte, lo zip estratto al suo posto,
 * e solo se tutto e' andato bene la vecchia cartella viene cancellata. In caso di errore si
 * rimette com'era.
 */
function tdpanop_rollback(WP_REST_Request $req)
{
    $type = (string) $req->get_param('type');
    $slug = basename((string) $req->get_param('slug'));
    $file = basename((string) $req->get_param('file'));
    if (!in_array($type, ['plugin', 'theme'], true) || $slug === '' || $file === '') {
        return new WP_REST_Response(['ok' => false, 'error' => 'type, slug e file obbligatori'], 400);
    }
    $known = array_column(tdpanop_backups_for($type, $slug), 'file');
    if (!in_array($file, $known, true)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'copia non trovata sul sito'], 404);
    }
    $dir  = tdpanop_backup_dir();
    $zip  = $dir . '/' . $file;
    $dest = tdpanop_item_dir($type, $slug);
    $tmp  = $dir . '/.restore-' . $slug . '-' . gmdate('His');
    $old  = $dest . '.sentinel-old';
    $before = tdpanop_item_version($type, $slug);
    @mkdir($tmp, 0755, true);
    $okZip = false;
    try {
        if (class_exists('ZipArchive')) {
            $z = new ZipArchive();
            if ($z->open($zip) === true) {
                $okZip = $z->extractTo($tmp);
                $z->close();
            }
        } else {
            require_once ABSPATH . 'wp-admin/includes/class-pclzip.php';
            $z = new PclZip($zip);
            $okZip = (bool) $z->extract(PCLZIP_OPT_PATH, $tmp);
        }
    } catch (\Throwable $e) {
        $okZip = false;
    }
    $extracted = $tmp . '/' . $slug;
    if (!$okZip || !is_dir($extracted)) {
        tdpanop_rmdir($tmp);
        return new WP_REST_Response(['ok' => false, 'error' => 'estrazione della copia non riuscita'], 200);
    }
    // scambio: attuale -> .sentinel-old, copia -> al suo posto
    if (is_dir($old)) {
        tdpanop_rmdir($old);
    }
    if (is_dir($dest) && !@rename($dest, $old)) {
        tdpanop_rmdir($tmp);
        return new WP_REST_Response(['ok' => false, 'error' => 'impossibile mettere da parte la cartella attuale'], 200);
    }
    if (!@rename($extracted, $dest)) {
        if (is_dir($old)) {
            @rename($old, $dest);   // si rimette com'era
        }
        tdpanop_rmdir($tmp);
        return new WP_REST_Response(['ok' => false, 'error' => 'impossibile rimettere la copia al suo posto'], 200);
    }
    tdpanop_rmdir($old);
    tdpanop_rmdir($tmp);
    if ($type === 'plugin') {
        wp_clean_plugins_cache(true);
    } else {
        wp_clean_themes_cache(false);
    }
    update_option('tdpanop_flush_caches', time(), true);   // cache da svuotare alla richiesta dopo
    $after = tdpanop_item_version($type, $slug);
    return new WP_REST_Response(['ok' => true, 'error' => '', 'from' => $before, 'to' => $after, 'file' => $file], 200);
}

function tdpanop_rmdir(string $dir): void
{
    if (!is_dir($dir)) {
        return;
    }
    try {
        $it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($dir, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::CHILD_FIRST);
        foreach ($it as $f) {
            $f->isDir() && !$f->isLink() ? @rmdir($f->getPathname()) : @unlink($f->getPathname());
        }
    } catch (\Throwable $e) {
        // best effort
    }
    @rmdir($dir);
}

function tdpanop_auth(WP_REST_Request $req)
{
    $bearer = '';
    $hdr = $req->get_header('authorization');
    if ($hdr && stripos($hdr, 'Bearer ') === 0) {
        $bearer = trim(substr($hdr, 7));
    }
    // Alcuni hosting (Apache in CGI/FastCGI configurato male) buttano via l'header
    // Authorization prima che arrivi a PHP: il token non arriverebbe mai e la risposta
    // sarebbe sempre 401. Il pannello lo manda anche in X-Sentinel-Token, che nessuno filtra.
    if ($bearer === '') {
        $alt = $req->get_header('x_sentinel_token');
        if ($alt) {
            $bearer = trim((string) $alt);
        }
    }
    if ($bearer === '') {
        return false;
    }
    if (hash_equals(tdpanop_token(), $bearer)) {
        return true;
    }
    // AUTH SELF-HEALING contro l'object cache avvelenata (Redis).
    // Caso reale: alla riattivazione del plugin, con la cache alloptions stantia,
    // add_option puo' scrivere in CACHE un token nuovo mentre nel DB resta quello
    // vero -> il connettore confronta col token fantasma -> 401 su un token giusto,
    // stabile finche' nessuno flusha Redis. Qui, SOLO in caso di mismatch, buttiamo
    // la cache e riconfrontiamo col valore VERO dal DB: costo zero nel caso normale,
    // autoguarigione in quello avvelenato.
    wp_cache_delete(TDPANOP_OPT, 'options');
    wp_cache_delete('alloptions', 'options');
    $real = get_option(TDPANOP_OPT);
    return is_string($real) && $real !== '' && hash_equals($real, $bearer);
}

function tdpanop_status($req = null)
{
    if (!function_exists('get_plugins')) {
        require_once ABSPATH . 'wp-admin/includes/plugin.php';
    }
    if (!function_exists('get_core_updates')) {
        require_once ABSPATH . 'wp-admin/includes/update.php';
    }

    // CHECK PASSIVO (come Akeeba Panopticon): di default leggiamo i transient di update
    // cosi' come li mantiene WordPress (il wp-cron li aggiorna periodicamente). Nessun
    // refresh forzato a ogni status: impatto minimo sul sito, nessuna richiesta in uscita
    // a wordpress.org, risposta veloce.
    // Il refresh forzato (pesante: contatta wordpress.org) avviene SOLO se richiesto
    // esplicitamente con ?refresh=1, cosi' Sentinel TD puo' rinfrescare quando serve (es.
    // una volta al giorno o sul check manuale) senza gravare su ogni controllo.
    $forceRefresh = false;
    if ($req instanceof WP_REST_Request) {
        $forceRefresh = (string) $req->get_param('refresh') === '1';
    }
    if ($forceRefresh) {
        // Con un object cache persistente il transient di update e' instabile (sfrattato
        // dalla LRU o vuoto) e wp_update_plugins()/wp_update_themes() sono throttlati a
        // 12h: se last_checked e' recente NON ricontattano wordpress.org e restituiscono
        // uno stato vecchio. Cancellando prima i transient il throttle non scatta e il
        // refresh e' reale (identico al pulsante "Controlla di nuovo" del backend).
        tdpanop_wake_premium_updaters();
        delete_site_transient('update_plugins');
        delete_site_transient('update_themes');
        delete_site_transient('update_core');
        // Con i tempi del cron: fuori dal cron WordPress da' a wordpress.org appena 3 secondi,
        // e su un hosting lento la richiesta scade in silenzio lasciando la cache VUOTA ->
        // "zero aggiornamenti" falso. In contesto cron il tempo e' 30 secondi.
        $cron_filter = function () {
            return true;
        };
        add_filter('wp_doing_cron', $cron_filter, 999);
        try {
            wp_version_check();
            wp_update_plugins();
            wp_update_themes();
        } finally {
            remove_filter('wp_doing_cron', $cron_filter, 999);
        }
    }

    // La cache degli aggiornamenti c'e' davvero? Se un ricalcolo e' fallito (wordpress.org
    // non raggiunto, timeout, memoria) o un object cache l'ha sfrattata, i transient
    // MANCANO: non e' "zero aggiornamenti", e' "non lo so". Lo si dichiara al pannello, che
    // tiene i dati dell'ultimo controllo riuscito invece di azzerare tutto.
    $t_up = get_site_transient('update_plugins');
    $t_ut = get_site_transient('update_themes');
    $t_uc = get_site_transient('update_core');
    $plugins_known = is_object($t_up) && isset($t_up->last_checked);
    $themes_known  = is_object($t_ut) && isset($t_ut->last_checked);
    $core_known    = is_object($t_uc) && isset($t_uc->last_checked);

    $core_cur = get_bloginfo('version');
    $core_latest = $core_cur;
    $core_update = false;
    $uc = get_site_transient('update_core');
    if ($uc && !empty($uc->updates)) {
        foreach ($uc->updates as $u) {
            if (
                isset($u->response) && $u->response === 'upgrade' && !empty($u->current)
                && version_compare((string) $u->current, (string) $core_cur, '>')
            ) {
                $core_latest = $u->current;
                $core_update = true;
                break;
            }
        }
    }

    $extensions = [];

    // plugin/temi di default inclusi da WP ma non installati da te -> esclusi
    $skip_plugins = ['akismet', 'hello'];   // cartelle: akismet/, hello.php
    // i temi di default WP iniziano con "twenty" (twentytwentyfour, ...)

    // PLUGIN: solo quelli di terze parti (tuoi), con flag update
    $all_plugins = get_plugins();
    $up = get_site_transient('update_plugins');
    $up_resp = ($up && !empty($up->response)) ? $up->response : [];
    foreach ($all_plugins as $file => $p) {
        $dir = dirname($file);                       // es. "advanced-custom-fields" oppure "." per file in root
        $base = strtolower($dir !== '.' ? $dir : basename($file, '.php'));
        if (in_array($base, $skip_plugins, true)) {
            continue;
        }
        $has = isset($up_resp[$file]);
        // guardia anti-falso-positivo (come il connettore Joomla): l'update vale SOLO se la
        // versione proposta e' davvero piu' alta di quella installata. Dopo un update il
        // transient puo' restare stantio con una voce a pari versione: senza questa guardia
        // lo ri-segnaleremmo come "da aggiornare" -> update riproposto ad ogni ciclo.
        if ($has) {
            $curV = isset($p['Version']) ? (string) $p['Version'] : '';
            $newV = isset($up_resp[$file]->new_version) ? (string) $up_resp[$file]->new_version : '';
            if ($curV !== '' && $newV !== '' && version_compare($newV, $curV, '<=')) {
                $has = false;
            }
        }
        $extensions[] = [
            'type'    => 'plugin',
            'name'    => isset($p['Name']) ? $p['Name'] : $file,
            'slug'    => $dir,
            'current' => isset($p['Version']) ? $p['Version'] : '',
            'new'     => $has && isset($up_resp[$file]->new_version) ? $up_resp[$file]->new_version : '',
            'update'  => $has,
        ];
    }

    // TEMI: TUTTI quelli installati, default "twenty*" COMPRESI. Prima erano esclusi
    // ("tanto uso YOOtheme"), ma un tema installato e non aggiornato e' superficie
    // d'attacco anche da disattivo: se sta sul sito, va tenuto aggiornato come il resto.
    $themes = wp_get_themes();
    $ut = get_site_transient('update_themes');
    $ut_resp = ($ut && !empty($ut->response)) ? $ut->response : [];
    foreach ($themes as $slug => $t) {
        $has = isset($ut_resp[$slug]);
        // stessa guardia versione dei plugin
        if ($has) {
            $curV = (string) $t->get('Version');
            $newV = isset($ut_resp[$slug]['new_version']) ? (string) $ut_resp[$slug]['new_version'] : '';
            if ($curV !== '' && $newV !== '' && version_compare($newV, $curV, '<=')) {
                $has = false;
            }
        }
        $extensions[] = [
            'type'    => 'theme',
            'name'    => $t->get('Name'),
            'slug'    => $slug,
            'current' => $t->get('Version'),
            'new'     => $has && isset($ut_resp[$slug]['new_version']) ? $ut_resp[$slug]['new_version'] : '',
            'update'  => $has,
        ];
    }

    // TRADUZIONI (language pack): su WP gli update delle lingue arrivano come
    // "translations" dentro i transient update_core / update_plugins / update_themes.
    // Li aggrego in UNA voce sintetica (WP li aggiorna tutti insieme), cosi' compaiono
    // nel monitoraggio e sono aggiornabili. Tipo 'language' per coerenza con Joomla.
    // Solo le traduzioni REALMENTE piu' recenti di quelle gia' installate.
    // WP a volte continua a elencare la stessa traduzione anche DOPO averla aggiornata
    // (le revisioni PO non vengono riconciliate): senza questo confronto la voce
    // "Traduzioni" resterebbe update=true per sempre -> riproposta ad ogni ciclo con
    // relativa email/Telegram, anche se non c'e' nulla di nuovo da fare.
    $installedTrans = [];
    if (function_exists('wp_get_installed_translations')) {
        foreach (['plugin' => 'plugins', 'theme' => 'themes', 'core' => 'default'] as $trType => $dom) {
            foreach ((array) wp_get_installed_translations($dom) as $islug => $langs) {
                foreach ((array) $langs as $lang => $meta) {
                    $rev = isset($meta['PO-Revision-Date']) ? strtotime((string) $meta['PO-Revision-Date']) : 0;
                    $installedTrans[$trType . '|' . $islug . '|' . $lang] = $rev ?: 0;
                }
            }
        }
    }
    $trans = [];
    foreach (['update_core', 'update_plugins', 'update_themes'] as $tname) {
        $tr = get_site_transient($tname);
        if ($tr && !empty($tr->translations) && is_array($tr->translations)) {
            foreach ($tr->translations as $t) {
                $type = isset($t['type']) ? (string) $t['type'] : '';
                $slug = isset($t['slug']) ? (string) $t['slug'] : '';
                $lang = isset($t['language']) ? (string) $t['language'] : '';
                // 'default' e' lo slug del core in wp_get_installed_translations
                $islug   = ($type === 'core') ? 'default' : $slug;
                $availTs = isset($t['updated']) ? strtotime((string) $t['updated']) : 0;
                $instTs  = $installedTrans[$type . '|' . $islug . '|' . $lang] ?? 0;
                // tieni SOLO se la disponibile e' davvero piu' nuova (o se non risulta
                // installata): le voci "fantasma" a pari data (o piu' vecchie) spariscono.
                if ($availTs > 0 && $instTs > 0 && $availTs <= $instTs) {
                    continue;
                }
                $k = $type . '|' . $slug . '|' . $lang;
                $trans[$k] = $t;
            }
        }
    }
    if (!empty($trans)) {
        $n = count($trans);
        $extensions[] = [
            'type'    => 'language',
            'name'    => 'Traduzioni (' . $n . ')',
            'slug'    => 'wp-translations',
            'current' => '',
            'new'     => (string) $n,
            'update'  => true,
        ];
    }

    return new WP_REST_Response([
        'cms'  => 'wp',
        'connector' => TDPANOP_VERSION,   // il pannello sa quale versione gira su ogni sito
        'core' => ['current' => $core_cur, 'latest' => $core_latest, 'update' => $core_update, 'known' => $core_known],
        'php'  => PHP_VERSION,
        'extensions' => $extensions,
        // false = la cache degli aggiornamenti mancava: i flag "update" qui sopra sono zeri
        // non verificati, il pannello tiene quelli dell'ultimo controllo riuscito
        'updates_known' => ['plugin' => $plugins_known, 'theme' => $themes_known, 'core' => $core_known],
        'refreshed' => $forceRefresh,
    ], 200);
}

/* ---------------------------------------------------------------------------
 * Update on-demand: aggiorna UNA estensione (o il core)
 * body JSON: {"type":"plugin|theme|core", "slug":"..."}
 * ------------------------------------------------------------------------- */
function tdpanop_plugin_file_by_slug(string $slug): string
{
    if (!function_exists('get_plugins')) {
        require_once ABSPATH . 'wp-admin/includes/plugin.php';
    }
    foreach (array_keys(get_plugins()) as $file) {
        if (dirname($file) === $slug || $file === $slug) {
            return $file;
        }
    }
    return '';
}

function tdpanop_update(WP_REST_Request $req)
{
    $type = (string) $req->get_param('type');
    $slug = (string) $req->get_param('slug');

    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/misc.php';
    require_once ABSPATH . 'wp-admin/includes/plugin.php';
    require_once ABSPATH . 'wp-admin/includes/update.php';
    require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';

    if (defined('DISALLOW_FILE_MODS') && DISALLOW_FILE_MODS) {
        return new WP_REST_Response(['ok' => false, 'error' => 'DISALLOW_FILE_MODS attivo: update bloccati'], 200);
    }

    // Versione che il pannello si aspetta (opzionale): serve a distinguere "gia' aggiornato"
    // da "non aggiornabile da qui".
    $panelExpected = trim((string) $req->get_param('expected'));

    // Dati di aggiornamento pronti SENZA distruggere quelli buoni (vedi la funzione).
    tdpanop_prepare_update_data($type, $slug, false);

    // Spazio: prima di un aggiornamento grosso si prova a scrivere quanto serve davvero.
    // Senza spazio WordPress scarica uno zip troncato e fallisce dopo, con un errore che
    // non dice niente (PCLZIP_ERR_BAD_FORMAT). Il core si controlla nel suo ramo, sotto.
    if ($type === 'plugin' || $type === 'theme') {
        $needMb = tdpanop_required_space_mb($type, $slug);
        if ($needMb > 0) {
            $chk = tdpanop_space_check($needMb);
            if (!$chk['ok']) {
                return new WP_REST_Response(['ok' => false, 'reason' => 'no_space', 'need_mb' => $needMb,
                    'writable_mb' => $chk['written_mb'],
                    'error' => sprintf('spazio insufficiente sul sito: per questo aggiornamento servono circa %d MB, se ne riescono a scrivere solo %s MB. Aggiornamento non tentato',
                                       $needMb, tdpanop_mb($chk['written_mb']))], 200);
            }
        }
    }

    $skin = new Automatic_Upgrader_Skin();
    $res  = null;
    $verBefore = '';
    $verAfter  = '';
    $expected  = '';   // versione attesa dall'update, se nota
    $reactivated = null;   // null = non applicabile; true/false = esito riattivazione plugin

    try {
        if ($type === 'plugin') {
            $file = tdpanop_plugin_file_by_slug($slug);
            if ($file === '') {
                return new WP_REST_Response(['ok' => false, 'error' => 'Plugin non trovato: ' . $slug], 200);
            }
            $plugins   = get_plugins();
            $verBefore = isset($plugins[$file]['Version']) ? (string) $plugins[$file]['Version'] : '';

            // stato di attivazione PRIMA dell'update (per riattivarlo dopo se serve)
            $wasActive        = is_plugin_active($file);
            $wasNetworkActive = is_multisite() && is_plugin_active_for_network($file);

            // versione attesa dal transient di update
            $upd = get_site_transient('update_plugins');
            if ($upd && !empty($upd->response[$file]->new_version)) {
                $expected = (string) $upd->response[$file]->new_version;
            }

            tdpanop_backup_item('plugin', $slug);   // copia della versione attuale, per il ripristino
            $up  = new Plugin_Upgrader($skin);

            // IMPORTANTE: Plugin_Upgrader disattiva il plugin PRIMA dell'upgrade quando NON
            // siamo in cron (deactivate_plugin_before_upgrade), perche' assume che un browser
            // lo riattivera'. Noi giriamo via REST: nessun browser -> il plugin resterebbe spento.
            // Soluzione (stessa logica del core WP per i background update): segnaliamo a WP di
            // essere in contesto cron SOLO per la durata dell'upgrade, cosi' il plugin NON viene
            // disattivato. Usiamo un filtro mirato invece della costante DOING_CRON (piu' pulito).
            $force_cron = function () {
                return true;
            };
            add_filter('wp_doing_cron', $force_cron, 999);
            try {
                $res = $up->upgrade($file);
            } finally {
                remove_filter('wp_doing_cron', $force_cron, 999);
            }

            // svuota le cache delle opzioni: dopo l'upgrade lo stato 'active_plugins'
            // in memoria puo' essere stale, e get_plugins() ha la sua cache.
            wp_cache_delete('alloptions', 'options');
            wp_clean_plugins_cache(false);

            $plugins  = get_plugins();
            $verAfter = isset($plugins[$file]['Version']) ? (string) $plugins[$file]['Version'] : '';

            // SECONDO TENTATIVO: la versione non e' salita. Dati ricostruiti da zero (voce
            // persa, file del produttore scaduto, blocco di Elementor) e si riprova una volta.
            if (!($verAfter !== '' && $verBefore !== '' && version_compare($verAfter, $verBefore, '>'))
                && tdpanop_prepare_update_data('plugin', $slug, true)) {
                $skin = new Automatic_Upgrader_Skin();
                $up   = new Plugin_Upgrader($skin);
                add_filter('wp_doing_cron', $force_cron, 999);
                try {
                    $res = $up->upgrade($file);
                } finally {
                    remove_filter('wp_doing_cron', $force_cron, 999);
                }
                wp_cache_delete('alloptions', 'options');
                wp_clean_plugins_cache(false);
                $plugins  = get_plugins();
                $verAfter = isset($plugins[$file]['Version']) ? (string) $plugins[$file]['Version'] : '';
            }

            // RETE DI SICUREZZA (cintura + bretelle): anche se il contesto cron dovrebbe aver
            // evitato la disattivazione, se per qualunque motivo il plugin risulta spento e prima
            // era attivo, lo riattiviamo. activate_plugin e' idempotente.
            if (($wasActive || $wasNetworkActive) && !is_wp_error($res) && file_exists(WP_PLUGIN_DIR . '/' . $file)) {
                $network = $wasNetworkActive;
                if ($network ? !is_plugin_active_for_network($file) : !is_plugin_active($file)) {
                    $act = activate_plugin($file, '', $network, true);   // silent
                    if (is_wp_error($act)) {
                        activate_plugin($file, '', $network, false);     // ritenta con hook
                    }
                }
                // verifica finale: ora risulta attivo?
                wp_cache_delete('alloptions', 'options');
                $reactivated = $network ? is_plugin_active_for_network($file) : is_plugin_active($file);
            }
        } elseif ($type === 'theme') {
            $t = wp_get_theme($slug);
            $verBefore = $t->exists() ? (string) $t->get('Version') : '';

            $upd = get_site_transient('update_themes');
            if ($upd && !empty($upd->response[$slug]['new_version'])) {
                $expected = (string) $upd->response[$slug]['new_version'];
            }

            tdpanop_backup_item('theme', $slug);   // copia della versione attuale, per il ripristino
            $up  = new Theme_Upgrader($skin);
            $res = $up->upgrade($slug);

            wp_clean_themes_cache(false);
            $t = wp_get_theme($slug);
            $verAfter = $t->exists() ? (string) $t->get('Version') : '';
        } elseif ($type === 'language') {
            // aggiorna TUTTI i language pack disponibili (core + plugin + temi).
            // WP li gestisce insieme con Language_Pack_Upgrader.
            require_once ABSPATH . 'wp-admin/includes/class-language-pack-upgrader.php';

            // conta le traduzioni disponibili PRIMA
            $countBefore = 0;
            foreach (['update_core', 'update_plugins', 'update_themes'] as $tname) {
                $tr = get_site_transient($tname);
                if ($tr && !empty($tr->translations)) {
                    $countBefore += count($tr->translations);
                }
            }
            if ($countBefore === 0) {
                return new WP_REST_Response(['ok' => true, 'error' => '', 'new' => 'nessuna traduzione da aggiornare'], 200);
            }

            $up  = new Language_Pack_Upgrader($skin);
            // upgrade() senza argomenti aggiorna tutte le lingue disponibili dai transient
            $res = $up->bulk_upgrade();

            // ricalcola dopo: rinfresca i transient e riconta
            wp_clean_update_cache();
            wp_update_plugins();
            wp_update_themes();
            wp_version_check();
            $countAfter = 0;
            foreach (['update_core', 'update_plugins', 'update_themes'] as $tname) {
                $tr = get_site_transient($tname);
                if ($tr && !empty($tr->translations)) {
                    $countAfter += count($tr->translations);
                }
            }

            // successo se sono state applicate (countAfter < countBefore) o nessun errore
            $okLang = !is_wp_error($res);
            if (is_array($res)) {
                // bulk_upgrade torna array di risultati; ok se almeno uno non e' WP_Error/false
                $okLang = false;
                foreach ($res as $r) {
                    if ($r && !is_wp_error($r)) {
                        $okLang = true;
                        break;
                    }
                }
            }
            if ($okLang) {
                return new WP_REST_Response(['ok' => true, 'error' => '', 'new' => 'traduzioni aggiornate (' . max(0, $countBefore - $countAfter) . '/' . $countBefore . ')'], 200);
            }
            $emsg = is_wp_error($res) ? $res->get_error_message() : 'aggiornamento traduzioni non riuscito';
            return new WP_REST_Response(['ok' => false, 'error' => $emsg, 'new' => ''], 200);
        } elseif ($type === 'core') {
            require_once ABSPATH . 'wp-admin/includes/class-core-upgrader.php';
            $updates = get_core_updates();
            if (empty($updates) || !isset($updates[0]) || $updates[0]->response !== 'upgrade') {
                return new WP_REST_Response(['ok' => true, 'error' => '', 'new' => get_bloginfo('version')], 200);
            }
            $verBefore = get_bloginfo('version');
            $expected  = !empty($updates[0]->current) ? (string) $updates[0]->current : '';

            // core: zip da circa 30 MB + copia estratta da circa 90 MB, tutto insieme
            $chk = tdpanop_space_check(150);
            if (!$chk['ok']) {
                return new WP_REST_Response(['ok' => false, 'reason' => 'no_space', 'need_mb' => 150,
                    'writable_mb' => $chk['written_mb'], 'current' => $verBefore, 'new' => $expected,
                    'error' => sprintf('spazio insufficiente sul sito: per aggiornare WordPress servono circa 150 MB, se ne riescono a scrivere solo %s MB. Aggiornamento non tentato',
                                       tdpanop_mb($chk['written_mb']))], 200);
            }

            $up  = new Core_Upgrader($skin);
            $res = $up->upgrade($updates[0]);

            $verAfter = $expected;   // per il core ci fidiamo dell'esito dell'upgrader (verificato sotto via is_wp_error)
        } else {
            return new WP_REST_Response(['ok' => false, 'error' => 'type non valido'], 200);
        }
    } catch (\Throwable $e) {
        return new WP_REST_Response(['ok' => false, 'error' => $e->getMessage()], 200);
    }

    if ($expected === '' && $panelExpected !== '') {
        $expected = $panelExpected;
    }

    // La versione e' SALITA? Allora e' un aggiornamento riuscito, qualunque cosa abbia
    // risposto l'upgrader: capita con i plugin a licenza che si aggiornano da soli o che
    // arrivano a una versione diversa da quella annunciata (prima risultavano "falliti").
    if ($type === 'plugin' || $type === 'theme') {
        if ($verAfter !== '' && $verBefore !== '' && version_compare($verAfter, $verBefore, '>')) {
            $out = ['ok' => true, 'error' => '', 'new' => $verAfter] + tdpanop_backup_fields();
            if ($reactivated !== null) {
                $out['reactivated'] = (bool) $reactivated;
            }
            return new WP_REST_Response($out, 200);
        }
        // Non e' salita. Era gia' alla versione attesa? Allora non c'era niente da fare.
        if ($verBefore !== '' && $expected !== '' && version_compare($verBefore, $expected, '>=')) {
            return new WP_REST_Response(['ok' => true, 'noop' => true, 'error' => '', 'new' => $verBefore,
                'message' => 'già alla versione ' . $verBefore], 200);
        }
        // NB: NON rileggere qui la cache degli aggiornamenti. Dopo un tentativo WordPress la
        // svuota da solo: rileggerla adesso dava sempre "dati mancanti" e nascondeva l'errore
        // vero (download rifiutato, file non copiabile…). Si usa l'esito dell'installazione.
        $err = is_wp_error($res) ? $res : ((isset($skin->result) && is_wp_error($skin->result)) ? $skin->result : null);
        $lic = tdpanop_license_hint($slug);
        if ($err !== null && $err->get_error_code() === 'no_package') {
            // il produttore non ha consegnato il file: va fatto a mano (o con un pacchetto)
            return new WP_REST_Response(['ok' => false, 'manual' => true, 'current' => $verBefore, 'new' => $expected,
                'reason' => 'no_package',
                'error' => 'il server del produttore non ha consegnato il file di aggiornamento a questo sito'
                         . ($lic !== '' ? ' (' . $lic . ')' : '')], 200);
        }
        if ($err === null && $res === false) {
            // WordPress non aveva l'aggiornamento in elenco al momento di installarlo
            return new WP_REST_Response(['ok' => false, 'manual' => true, 'current' => $verBefore, 'new' => $expected,
                'reason' => 'no_entry',
                'error' => 'WordPress non aveva i dati di aggiornamento di questo prodotto al momento di installarlo'
                         . ($lic !== '' ? ' (' . $lic . ')' : '')], 200);
        }
        if ($err !== null) {
            // errore reale: riportato com'e', con il suo codice per capire cosa e' successo
            $emsg = trim(wp_strip_all_tags($err->get_error_message())) . ' [' . $err->get_error_code() . ']';
            $why  = tdpanop_space_explanation($emsg . ' ' . tdpanop_skin_reason($skin));
            if ($why !== '') {
                return new WP_REST_Response(['ok' => false, 'reason' => 'no_space', 'current' => $verBefore,
                    'new' => $expected, 'error' => $why], 200);
            }
            return new WP_REST_Response(['ok' => false, 'current' => $verBefore, 'new' => $expected,
                'error' => $emsg . ($lic !== '' ? ' (' . $lic . ')' : '')], 200);
        }
    }

    // errore esplicito dall'upgrader
    if (is_wp_error($res)) {
        $emsg = trim(wp_strip_all_tags($res->get_error_message())) . ' [' . $res->get_error_code() . ']';
        $why  = tdpanop_space_explanation($emsg . ' ' . tdpanop_skin_reason($skin), $type === 'core' ? 150 : 100);
        return new WP_REST_Response(['ok' => false, 'error' => $why !== '' ? $why : $emsg, 'current' => $verBefore]
            + ($why !== '' ? ['reason' => 'no_space'] : []), 200);
    }

    // SUCCESSO solo se la versione è davvero cambiata (ed è salita a quella attesa, se nota)
    $changed = ($verAfter !== '' && $verAfter !== $verBefore);
    $reached = ($expected === '' || $verAfter === $expected || version_compare($verAfter, $expected, '>='));

    if ($changed && $reached) {
        $out = ['ok' => true, 'error' => '', 'new' => $verAfter] + tdpanop_backup_fields();
        if ($reactivated !== null) {
            $out['reactivated'] = (bool) $reactivated;
        }
        return new WP_REST_Response($out, 200);
    }

    // fallito: prova a recuperare il motivo dai messaggi dello skin (es. credenziali FS, pacchetto non scaricabile)
    $reason = '';
    if (!empty($skin->result) && is_wp_error($skin->result)) {
        $reason = $skin->result->get_error_message();
    }
    if ($reason === '' && method_exists($skin, 'get_upgrade_messages')) {
        $msgs = $skin->get_upgrade_messages();
        if (!empty($msgs)) {
            $reason = trim(strip_tags(implode(' | ', (array) $msgs)));
        }
    }
    if ($reason === '') {
        $reason = 'versione non cambiata (' . ($verBefore ?: '?') . ' → attesa ' . ($expected ?: '?') . '): update non applicato (licenza/credenziali?)';
    }
    $why = tdpanop_space_explanation($reason, $type === 'core' ? 150 : 100);
    if ($why !== '') {
        return new WP_REST_Response(['ok' => false, 'reason' => 'no_space', 'error' => $why, 'new' => $expected,
            'current' => $verBefore], 200);
    }

    return new WP_REST_Response(['ok' => false, 'error' => $reason, 'new' => $expected, 'current' => $verBefore], 200);
}

/* ---------------------------------------------------------------------------
 * Aggiornamento nel contesto del BACKEND (admin-ajax.php).
 * Via REST WordPress gira "da fuori": i plugin a licenza (Elementor Pro, ACF Pro,
 * Gravity Forms, WP Rocket…) spesso caricano il proprio sistema di aggiornamento solo
 * nel backend, e il pacchetto non arriva. admin-ajax.php e' backend a tutti gli effetti
 * (is_admin() vero, admin_init eseguito): li' i loro sistemi di aggiornamento sono attivi.
 * Stessa autenticazione del REST (token Bearer), stessa logica di tdpanop_update().
 * ------------------------------------------------------------------------- */
function tdpanop_ajax_update()
{
    $hdr = '';
    foreach (['HTTP_AUTHORIZATION', 'REDIRECT_HTTP_AUTHORIZATION'] as $k) {
        if (!empty($_SERVER[$k])) {
            $hdr = (string) $_SERVER[$k];
            break;
        }
    }
    if ($hdr === '' && function_exists('getallheaders')) {
        foreach ((array) getallheaders() as $hname => $hvalue) {
            if (strtolower((string) $hname) === 'authorization') {
                $hdr = (string) $hvalue;
                break;
            }
        }
    }
    $req = new WP_REST_Request('POST');
    $req->set_header('authorization', $hdr);
    if (!empty($_SERVER['HTTP_X_SENTINEL_TOKEN'])) {
        $req->set_header('x_sentinel_token', (string) $_SERVER['HTTP_X_SENTINEL_TOKEN']);
    }
    if (!tdpanop_auth($req)) {
        wp_send_json(['ok' => false, 'error' => 'non autorizzato'], 401);
    }
    $req->set_param('type', sanitize_key(wp_unslash((string) ($_POST['type'] ?? ''))));
    $req->set_param('slug', sanitize_text_field(wp_unslash((string) ($_POST['slug'] ?? ''))));
    $req->set_param('expected', sanitize_text_field(wp_unslash((string) ($_POST['expected'] ?? ''))));
    $resp = tdpanop_update($req);
    $data = ($resp instanceof WP_REST_Response) ? (array) $resp->get_data() : (array) $resp;
    $data['context'] = 'admin';
    wp_send_json($data, 200);
}
add_action('wp_ajax_nopriv_tdpanop_update', 'tdpanop_ajax_update');
add_action('wp_ajax_tdpanop_update', 'tdpanop_ajax_update');

/**
 * Prepara i dati di aggiornamento per UN plugin o tema, senza distruggere quelli buoni.
 *
 * Prima il connettore buttava via e ricostruiva tutto prima di OGNI aggiornamento. Ma
 * Elementor Pro (e altri a licenza) interroga il proprio server al massimo una volta al
 * minuto: su un sito con molti aggiornamenti in fila, al turno di Pro il server era gia'
 * stato chiamato da poco, Elementor rifiutava di richiamarlo e Pro spariva dall'elenco
 * ("WordPress non aveva i dati…"). Ora:
 *  - se l'elemento e' gia' in elenco con il suo file, lo si usa cosi' com'e';
 *  - altrimenti si ricostruisce, e se la voce buona e' andata persa la si rimette;
 *  - per Elementor Pro, se manca ancora, si toglie il blocco del minuto e si chiede la
 *    versione direttamente al suo modulo di licenza.
 * $force = true ricostruisce comunque (secondo tentativo dopo un fallimento).
 * Core e traduzioni: ricostruzione completa come sempre.
 */
function tdpanop_prepare_update_data($type, $slug, $force = false)
{
    tdpanop_wake_premium_updaters();
    if ($type !== 'plugin' && $type !== 'theme') {
        delete_site_transient('update_plugins');
        delete_site_transient('update_themes');
        delete_site_transient('update_core');
        wp_update_plugins();
        wp_update_themes();
        wp_version_check();
        return true;
    }
    $tname = ($type === 'plugin') ? 'update_plugins' : 'update_themes';
    $key   = ($type === 'plugin') ? tdpanop_plugin_file_by_slug($slug) : $slug;
    if ($key === '') {
        return false;
    }
    $entry = tdpanop_update_entry($tname, $key);
    if (!$force && tdpanop_entry_has_package($entry)) {
        return true;
    }
    $saved = $entry;
    if ($slug === 'elementor-pro') {
        delete_option('_elementor_pro_api_requests_lock');
    }
    delete_site_transient($tname);
    if ($type === 'plugin') {
        wp_update_plugins();
    } else {
        wp_update_themes();
    }
    $entry = tdpanop_update_entry($tname, $key);
    if (!tdpanop_entry_has_package($entry) && tdpanop_entry_has_package($saved)) {
        tdpanop_set_update_entry($tname, $key, $saved);
        $entry = $saved;
    }
    if (!tdpanop_entry_has_package($entry) && $type === 'plugin' && $slug === 'elementor-pro') {
        $e = tdpanop_elementor_pro_entry($key);
        if ($e !== null) {
            tdpanop_set_update_entry($tname, $key, $e);
            $entry = $e;
        }
    }
    return tdpanop_entry_has_package($entry);
}

function tdpanop_update_entry($tname, $key)
{
    $t = get_site_transient($tname);
    return ($t && !empty($t->response[$key])) ? $t->response[$key] : null;
}

function tdpanop_entry_has_package($e)
{
    if ($e === null) {
        return false;
    }
    $pkg = is_object($e) ? ($e->package ?? '') : (is_array($e) ? ($e['package'] ?? '') : '');
    return (string) $pkg !== '';
}

function tdpanop_set_update_entry($tname, $key, $entry)
{
    $cur = get_site_transient($tname);
    if (!is_object($cur)) {
        $cur = new stdClass();
    }
    if (!isset($cur->response) || !is_array($cur->response)) {
        $cur->response = [];
    }
    $cur->response[$key] = $entry;
    set_site_transient($tname, $cur);
}

/**
 * Versione e file di Elementor Pro chiesti direttamente al suo modulo di licenza, dopo
 * aver tolto il suo blocco di una richiesta al minuto. null se non disponibili (licenza
 * non valida, server non raggiungibile, struttura del plugin diversa).
 */
function tdpanop_elementor_pro_entry($file)
{
    try {
        delete_option('_elementor_pro_api_requests_lock');
        $cls = 'ElementorPro\\License\\API';
        if (!is_callable([$cls, 'get_version'])) {
            return null;
        }
        $info = call_user_func([$cls, 'get_version'], true);
        if (is_wp_error($info) || (!is_array($info) && !is_object($info))) {
            return null;
        }
        $info = (array) $info;
        if (empty($info['new_version']) || empty($info['package'])) {
            return null;
        }
        return (object) [
            'slug'        => 'elementor-pro',
            'plugin'      => $file,
            'new_version' => (string) $info['new_version'],
            'package'     => (string) $info['package'],
            'url'         => (string) ($info['url'] ?? ''),
            'tested'      => (string) ($info['tested'] ?? ''),
        ];
    } catch (\Throwable $e) {
        return null;
    }
}

/**
 * Alcuni plugin a licenza creano il proprio sistema di aggiornamento solo quando qualcosa
 * lo chiede, di solito le loro pagine nel backend. Elementor Pro lo crea con
 * Admin::get_updater_instance(): fuori da li' puo' non esistere, e allora WordPress non sa
 * ne' che c'e' una versione nuova ne' da dove scaricarla. Chiamarlo prima di ricostruire la
 * cache degli aggiornamenti lo rende sempre presente. Se il prodotto manca o cambia
 * struttura, la chiamata viene semplicemente saltata.
 */
function tdpanop_wake_premium_updaters()
{
    static $done = false;
    if ($done) {
        return;
    }
    $done = true;
    try {
        if (is_callable(['\\ElementorPro\\License\\Admin', 'get_updater_instance'])) {
            call_user_func(['\\ElementorPro\\License\\Admin', 'get_updater_instance']);
        }
    } catch (\Throwable $e) {
        // mai bloccare un aggiornamento per questo
    }
}

/**
 * Stato della licenza per i prodotti che lo espongono (oggi: Elementor Pro), cosi' il
 * report dice PERCHE' il file non arriva invece di un generico "a mano".
 */
function tdpanop_license_hint($slug)
{
    if ($slug === 'elementor-pro') {
        $key = (string) get_option('elementor_pro_license_key', '');
        if ($key === '') {
            return 'nessuna licenza Elementor Pro inserita su questo sito';
        }
        $raw = get_option('_elementor_pro_license_v2_data');
        $val = is_array($raw) && isset($raw['value']) ? json_decode((string) $raw['value'], true) : null;
        if (is_array($val) && !empty($val['license']) && $val['license'] !== 'valid') {
            return 'stato licenza Elementor Pro: ' . sanitize_text_field((string) $val['license']);
        }
    }
    return '';
}

/* ---------------------------------------------------------------------------
 * Install on-demand: installa (e opzionalmente attiva) un plugin/tema da zip.
 * multipart/form-data:
 *   - package : file .zip
 *   - kind    : 'plugin' | 'theme'
 *   - activate: '1' | '0' (default 1)
 * Usa gli Upgrader nativi di WP (= Plugin/Tema → Aggiungi nuovo → Carica).
 * Con overwrite_package=true reinstalla anche se gia' presente (utile per
 * ridistribuire una nuova build di un plugin custom su piu' siti).
 * ------------------------------------------------------------------------- */
function tdpanop_install(WP_REST_Request $req)
{
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/misc.php';
    require_once ABSPATH . 'wp-admin/includes/plugin.php';
    require_once ABSPATH . 'wp-admin/includes/theme.php';
    require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';

    if (defined('DISALLOW_FILE_MODS') && DISALLOW_FILE_MODS) {
        return new WP_REST_Response(['ok' => false, 'error' => 'DISALLOW_FILE_MODS attivo: install bloccati'], 200);
    }

    $kind     = strtolower((string) $req->get_param('kind'));
    $activate = ((string) $req->get_param('activate')) !== '0';   // default: attiva
    if (!in_array($kind, ['plugin', 'theme'], true)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'kind non valido (plugin|theme)'], 200);
    }

    // --- file caricato ---
    $files = $req->get_file_params();
    $f = isset($files['package']) ? $files['package'] : null;
    if (!$f || empty($f['tmp_name'])) {
        return new WP_REST_Response(['ok' => false, 'error' => 'Nessun file ricevuto (campo "package")'], 200);
    }
    if (!empty($f['error'])) {
        return new WP_REST_Response(['ok' => false, 'error' => 'Upload fallito (codice PHP ' . (int) $f['error'] . ')'], 200);
    }
    $origName = (string) (isset($f['name']) ? $f['name'] : 'package.zip');
    if (strtolower(pathinfo($origName, PATHINFO_EXTENSION)) !== 'zip') {
        return new WP_REST_Response(['ok' => false, 'error' => 'Il pacchetto deve essere un file .zip'], 200);
    }

    // --- sposta lo zip in un percorso temporaneo scrivibile ---
    $up_dir = wp_upload_dir();
    $base   = (!empty($up_dir['basedir']) && wp_is_writable($up_dir['basedir']))
        ? $up_dir['basedir']
        : get_temp_dir();
    $tmp = trailingslashit($base) . 'tdpanop-' . wp_generate_password(10, false, false) . '.zip';
    if (!@move_uploaded_file($f['tmp_name'], $tmp) && !@copy($f['tmp_name'], $tmp)) {
        return new WP_REST_Response(['ok' => false, 'error' => 'Impossibile scrivere il pacchetto in ' . $base], 200);
    }

    $skin = new Automatic_Upgrader_Skin();

    try {
        if ($kind === 'plugin') {
            $up  = new Plugin_Upgrader($skin);
            $res = $up->install($tmp, ['overwrite_package' => true]);

            if (is_wp_error($res)) {
                return new WP_REST_Response(['ok' => false, 'error' => $res->get_error_message()], 200);
            }
            if ($res === false) {
                $reason = tdpanop_skin_reason($skin) ?: 'Installazione plugin fallita';
                return new WP_REST_Response(['ok' => false, 'error' => $reason], 200);
            }

            wp_clean_plugins_cache(false);
            $file = method_exists($up, 'plugin_info') ? (string) $up->plugin_info() : '';
            if ($file === '') {
                return new WP_REST_Response(['ok' => false, 'error' => 'Plugin installato ma file principale non rilevato'], 200);
            }
            $data    = get_plugin_data(WP_PLUGIN_DIR . '/' . $file, false, false);
            $name    = !empty($data['Name']) ? $data['Name'] : $file;
            $version = !empty($data['Version']) ? $data['Version'] : '';
            $slug    = dirname($file) !== '.' ? dirname($file) : $file;

            $activated = false;
            if ($activate) {
                $act = activate_plugin($file, '', false, false);
                if (is_wp_error($act)) {
                    return new WP_REST_Response([
                        'ok' => true,
                        'error' => 'Installato ma attivazione fallita: ' . $act->get_error_message(),
                        'new' => $version,
                        'type' => 'plugin',
                        'name' => $name,
                        'slug' => $slug,
                        'activated' => false,
                    ], 200);
                }
                wp_cache_delete('alloptions', 'options');
                $activated = is_plugin_active($file);
            }

            return new WP_REST_Response([
                'ok' => true,
                'error' => '',
                'new' => $version,
                'type' => 'plugin',
                'name' => $name,
                'slug' => $slug,
                'activated' => $activated,
            ], 200);
        }

        // --- theme ---
        $up  = new Theme_Upgrader($skin);
        $res = $up->install($tmp, ['overwrite_package' => true]);

        if (is_wp_error($res)) {
            return new WP_REST_Response(['ok' => false, 'error' => $res->get_error_message()], 200);
        }
        if ($res === false) {
            $reason = tdpanop_skin_reason($skin) ?: 'Installazione tema fallita';
            return new WP_REST_Response(['ok' => false, 'error' => $reason], 200);
        }

        wp_clean_themes_cache(false);
        $stylesheet = method_exists($up, 'theme_info') && $up->theme_info() ? (string) $up->theme_info()->get_stylesheet() : '';
        if ($stylesheet === '') {
            // fallback: lo skin espone il risultato con destination_name
            $stylesheet = isset($up->result['destination_name']) ? (string) $up->result['destination_name'] : '';
        }
        if ($stylesheet === '') {
            return new WP_REST_Response(['ok' => false, 'error' => 'Tema installato ma stylesheet non rilevato'], 200);
        }
        $theme   = wp_get_theme($stylesheet);
        $name    = $theme->exists() ? (string) $theme->get('Name') : $stylesheet;
        $version = $theme->exists() ? (string) $theme->get('Version') : '';

        $activated = false;
        if ($activate && $theme->exists()) {
            switch_theme($stylesheet);
            $activated = (get_stylesheet() === $stylesheet);
        }

        return new WP_REST_Response([
            'ok' => true,
            'error' => '',
            'new' => $version,
            'type' => 'theme',
            'name' => $name,
            'slug' => $stylesheet,
            'activated' => $activated,
        ], 200);
    } catch (\Throwable $e) {
        return new WP_REST_Response(['ok' => false, 'error' => $e->getMessage()], 200);
    } finally {
        if (is_file($tmp)) {
            @unlink($tmp);
        }
    }
}

/* Estrae un motivo leggibile dai messaggi dello skin dell'upgrader. */
function tdpanop_skin_reason($skin): string
{
    if (!empty($skin->result) && is_wp_error($skin->result)) {
        return $skin->result->get_error_message();
    }
    if (method_exists($skin, 'get_upgrade_messages')) {
        $msgs = $skin->get_upgrade_messages();
        if (!empty($msgs)) {
            return trim(strip_tags(implode(' | ', (array) $msgs)));
        }
    }
    return '';
}

/* ---------------------------------------------------------------------------
 * Uninstall on-demand: rimuove un plugin o un tema.
 * body JSON: {"type":"plugin|theme", "slug":"..."}
 * Sicurezze: non rimuove se stesso, non rimuove il tema attivo.
 * ------------------------------------------------------------------------- */
function tdpanop_uninstall(WP_REST_Request $req)
{
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/misc.php';
    require_once ABSPATH . 'wp-admin/includes/plugin.php';
    require_once ABSPATH . 'wp-admin/includes/theme.php';
    require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';

    if (defined('DISALLOW_FILE_MODS') && DISALLOW_FILE_MODS) {
        return new WP_REST_Response(['ok' => false, 'error' => 'DISALLOW_FILE_MODS attivo: rimozioni bloccate'], 200);
    }

    $type = strtolower((string) $req->get_param('type'));
    $slug = (string) $req->get_param('slug');
    if ($slug === '') {
        return new WP_REST_Response(['ok' => false, 'error' => 'slug mancante'], 200);
    }

    try {
        if ($type === 'plugin') {
            $file = tdpanop_plugin_file_by_slug($slug);
            if ($file === '') {
                return new WP_REST_Response(['ok' => false, 'error' => 'Plugin non trovato: ' . $slug], 200);
            }
            // non rimuovere il connettore stesso
            if (dirname($file) === 'td-panopticon' || strpos($file, 'td-panopticon') === 0) {
                return new WP_REST_Response(['ok' => false, 'error' => 'Non posso rimuovere il connettore Sentinel TD stesso'], 200);
            }
            $name = '';
            $pdata = get_plugin_data(WP_PLUGIN_DIR . '/' . $file, false, false);
            $name = !empty($pdata['Name']) ? $pdata['Name'] : $file;

            if (is_plugin_active($file) || (is_multisite() && is_plugin_active_for_network($file))) {
                deactivate_plugins([$file], true);
            }
            $res = delete_plugins([$file]);
            if (is_wp_error($res)) {
                return new WP_REST_Response(['ok' => false, 'error' => $res->get_error_message()], 200);
            }
            wp_clean_plugins_cache(false);
            return new WP_REST_Response(['ok' => true, 'error' => '', 'type' => 'plugin', 'name' => $name, 'slug' => $slug, 'removed' => true], 200);
        } elseif ($type === 'theme') {
            $t = wp_get_theme($slug);
            if (!$t->exists()) {
                return new WP_REST_Response(['ok' => false, 'error' => 'Tema non trovato: ' . $slug], 200);
            }
            // non rimuovere il tema attivo (ne' il suo parent se attivo come child)
            if (get_stylesheet() === $slug || get_template() === $slug) {
                return new WP_REST_Response(['ok' => false, 'error' => 'Tema attivo: non rimuovibile (attivane un altro prima)'], 200);
            }
            $name = (string) $t->get('Name');
            $res = delete_theme($slug);
            if (is_wp_error($res)) {
                return new WP_REST_Response(['ok' => false, 'error' => $res->get_error_message()], 200);
            }
            wp_clean_themes_cache(false);
            return new WP_REST_Response(['ok' => true, 'error' => '', 'type' => 'theme', 'name' => $name, 'slug' => $slug, 'removed' => true], 200);
        }

        return new WP_REST_Response(['ok' => false, 'error' => 'type non valido (plugin|theme)'], 200);
    } catch (\Throwable $e) {
        return new WP_REST_Response(['ok' => false, 'error' => $e->getMessage()], 200);
    }
}

/* ---------------------------------------------------------------------------
 * DIAGNOSTICA (2.19.0)
 * Spazio davvero scrivibile, cartelle, peso del sito, verifica dei file del core.
 * GET /diagnostics?space=MB&sizes=0|1&core=0|1
 * ------------------------------------------------------------------------- */

/** MB con la virgola, per i messaggi */
function tdpanop_mb($v): string
{
    return number_format((float) $v, 1, ',', '.');
}

/**
 * Prova di scrittura vera: scrive fino a $mb MB in un file nella cartella indicata e lo
 * cancella. Il test "spazio libero" di WordPress misura il disco dell'intero server, non la
 * quota del sito. Caso reale: 72 GB liberi sul disco, 1,6 MB scrivibili sulla quota, e tutti
 * gli aggiornamenti grossi rotti con zip troncati (PCLZIP_ERR_BAD_FORMAT).
 */
function tdpanop_space_probe(string $dir, int $mb): array
{
    $mb  = max(1, min(400, $mb));
    $out = ['dir' => $dir, 'tested_mb' => $mb, 'written_mb' => 0.0, 'ok' => false, 'error' => '', 'seconds' => 0.0];
    if ($dir === '' || !is_dir($dir) || !wp_is_writable($dir)) {
        $out['error'] = 'cartella non scrivibile';
        return $out;
    }
    $t0   = microtime(true);
    $file = trailingslashit($dir) . 'tdpanop-space-' . wp_generate_password(10, false) . '.tmp';
    $fh   = @fopen($file, 'wb');
    if (!$fh) {
        $out['error'] = 'impossibile creare un file di prova';
        return $out;
    }
    // dati casuali: un file di zeri su un disco compresso (ZFS, btrfs) quasi non occupa
    // spazio e la prova direbbe "c'e' posto" anche a quota piena
    try {
        $chunk = random_bytes(1048576);
    } catch (\Throwable $e) {
        $chunk = '';
        while (strlen($chunk) < 1048576) {
            $chunk .= md5(uniqid((string) mt_rand(), true), true);
        }
        $chunk = substr($chunk, 0, 1048576);
    }
    $len     = strlen($chunk);
    $written = 0;
    $failed  = false;
    for ($i = 0; $i < $mb; $i++) {
        $w = @fwrite($fh, $chunk);
        if ($w === false || $w < $len) {
            $written += max(0, (int) $w);
            $failed = true;
            break;
        }
        $written += $w;
        // ogni 16 MB si forza la scrittura: certe quote rifiutano solo al momento del flush
        if ($i % 16 === 15 && !@fflush($fh)) {
            $failed = true;
            break;
        }
    }
    if (!@fflush($fh)) {
        $failed = true;
    }
    if (!@fclose($fh)) {
        $failed = true;
    }
    clearstatcache(true, $file);
    $size = (int) @filesize($file);
    @unlink($file);
    $real = min($written, $size);
    $out['written_mb'] = round($real / 1048576, 1);
    $out['ok']         = !$failed && $real >= $mb * $len;
    $out['seconds']    = round(microtime(true) - $t0, 2);
    if (!$out['ok'] && $out['error'] === '') {
        $out['error'] = 'scrittura interrotta';
    }
    return $out;
}

/**
 * Spazio per gli aggiornamenti: si prova la cartella temporanea (dove WordPress scarica)
 * e, se sta su un altro disco, anche wp-content (dove estrae). Un esito positivo resta
 * valido 15 minuti, cosi' una fila di aggiornamenti non riscrive centinaia di MB ogni volta.
 */
function tdpanop_space_check(int $mb): array
{
    $cached = (float) get_transient('tdpanop_space_ok');
    if ($cached >= $mb) {
        return ['ok' => true, 'tested_mb' => $mb, 'written_mb' => $cached, 'cached' => true, 'probes' => []];
    }
    $tmp     = untrailingslashit(get_temp_dir());
    $content = untrailingslashit(WP_CONTENT_DIR);
    $dirs    = [$tmp];
    $st1     = @stat($tmp);
    $st2     = @stat($content);
    if (!$st1 || !$st2 || $st1['dev'] !== $st2['dev']) {
        $dirs[] = $content;
    }
    $probes = [];
    $ok     = true;
    $min    = null;
    foreach ($dirs as $d) {
        $p        = tdpanop_space_probe($d, $mb);
        $probes[] = $p;
        if (!$p['ok']) {
            $ok = false;
        }
        $min = ($min === null) ? $p['written_mb'] : min($min, $p['written_mb']);
    }
    if ($ok) {
        set_transient('tdpanop_space_ok', $mb, 15 * MINUTE_IN_SECONDS);
    } else {
        delete_transient('tdpanop_space_ok');
    }
    return ['ok' => $ok, 'tested_mb' => $mb, 'written_mb' => (float) $min, 'cached' => false, 'probes' => $probes];
}

/**
 * Spazio necessario per aggiornare un plugin o un tema, in MB (0 = non controllare).
 * La dimensione si chiede solo ai file pubblici di wordpress.org: ai server dei prodotti a
 * licenza non si fanno richieste in piu', i loro link di download sono a scadenza.
 */
function tdpanop_required_space_mb(string $type, string $slug): int
{
    $pkg = '';
    if ($type === 'plugin') {
        $file = tdpanop_plugin_file_by_slug($slug);
        $upd  = get_site_transient('update_plugins');
        if ($file !== '' && $upd && !empty($upd->response[$file]->package)) {
            $pkg = (string) $upd->response[$file]->package;
        }
    } elseif ($type === 'theme') {
        $upd = get_site_transient('update_themes');
        if ($upd && !empty($upd->response[$slug]['package'])) {
            $pkg = (string) $upd->response[$slug]['package'];
        }
    }
    if ($pkg === '' || stripos((string) wp_parse_url($pkg, PHP_URL_HOST), 'downloads.wordpress.org') === false) {
        return 0;
    }
    $h = wp_remote_head($pkg, ['timeout' => 10, 'redirection' => 3]);
    if (is_wp_error($h)) {
        return 0;
    }
    $len = (int) wp_remote_retrieve_header($h, 'content-length');
    if ($len < 5 * 1048576) {
        return 0;   // pacchetti piccoli: nessun controllo
    }
    // zip + contenuto estratto (circa tre volte lo zip) + margine
    return (int) min(400, ceil($len * 4 / 1048576) + 10);
}

/** L'errore di WordPress puo' dipendere dallo spazio? (zip troncato, copia fallita…) */
function tdpanop_is_space_error(string $text): bool
{
    return (bool) preg_match('/PCLZIP_ERR_BAD_FORMAT|incompatible_archive|copy_failed|disk_full|download_failed|Could not copy file|Impossibile copiare|non pu(o|ò) essere installato|could not be installed|not enough space|spazio su disco/iu', $text);
}

/**
 * Dopo un aggiornamento fallito: se l'errore e' di quelli che lo spazio esaurito produce,
 * si verifica davvero quanto si riesce a scrivere. Stringa vuota se lo spazio c'e'.
 */
function tdpanop_space_explanation(string $original, int $need = 100): string
{
    if (!tdpanop_is_space_error($original)) {
        return '';
    }
    delete_transient('tdpanop_space_ok');
    $c = tdpanop_space_check($need);
    if ($c['ok']) {
        return '';
    }
    return sprintf(
        'spazio del sito esaurito: si riescono a scrivere solo %s MB, per aggiornare ne servono circa %d. Errore di WordPress: %s',
        tdpanop_mb($c['written_mb']), $need, $original
    );
}

/** Dimensione del database del sito (solo le tabelle col suo prefisso). */
function tdpanop_db_size(): int
{
    global $wpdb;
    $like = $wpdb->esc_like($wpdb->base_prefix) . '%';
    $v = $wpdb->get_var($wpdb->prepare(
        'SELECT SUM(data_length + index_length) FROM information_schema.TABLES WHERE table_schema = %s AND table_name LIKE %s',
        DB_NAME, $like
    ));
    if ($v === null) {
        $v    = 0;
        $rows = $wpdb->get_results($wpdb->prepare('SHOW TABLE STATUS LIKE %s', $like), ARRAY_A);
        foreach ((array) $rows as $r) {
            $v += (int) ($r['Data_length'] ?? 0) + (int) ($r['Index_length'] ?? 0);
        }
    }
    return (int) $v;
}

/**
 * Peso del sito in un solo passaggio sui file, diviso per parti. Si ferma dopo $budget
 * secondi (siti con centinaia di migliaia di file): in quel caso complete = false.
 */

/**
 * Un file e' un log "grande"? Nomi dei log di PHP e WordPress, oltre i 10 MB.
 */
function tdpanop_is_big_log(string $name, int $size): bool
{
    if ($size < 10 * 1048576) {
        return false;
    }
    $n = strtolower($name);
    return $n === 'error_log' || $n === 'php_errorlog' || $n === 'php_error_log' || $n === 'debug.log'
        || substr($n, -4) === '.log' || substr($n, -10) === '.error.log';
}

/**
 * Log grandi FUORI dal sito ma dello stesso account: la home dell'utente e la sua cartella
 * "logs" (cPanel): e' li' che finiscono i log degli errori PHP, invisibili dal sito. Solo il
 * primo livello, solo se leggibile.
 */
function tdpanop_outside_logs(string $abs): array
{
    $out = [];
    $home = dirname($abs);
    foreach ([$home, $home . '/logs'] as $dir) {
        if ($dir === '' || $dir === '/' || !is_dir($dir) || !is_readable($dir)) {
            continue;
        }
        foreach ((array) @scandir($dir) as $name) {
            if ($name === '.' || $name === '..') {
                continue;
            }
            $p = $dir . '/' . $name;
            if (!is_file($p) || is_link($p)) {
                continue;
            }
            $sz = (int) @filesize($p);
            if (tdpanop_is_big_log($name, $sz)) {
                $out[] = ['path' => $p, 'bytes' => $sz, 'outside' => true];
            }
        }
    }
    return $out;
}

/**
 * Numeri del server per la pagina "Stato server" del pannello: carico medio (1, 5, 15 minuti),
 * disco del server (totale e libero: su un hosting condiviso e' il disco di tutti, non la
 * quota dell'account, che si misura con la prova di scrittura), software e PHP.
 */
function tdpanop_server_info(): array
{
    $load = function_exists('sys_getloadavg') ? @sys_getloadavg() : false;
    $cores = 0;
    if (is_readable('/proc/cpuinfo')) {
        $cores = (int) preg_match_all('/^processor\s*:/m', (string) @file_get_contents('/proc/cpuinfo'));
    }
    $total = @disk_total_space(ABSPATH);
    $free  = @disk_free_space(ABSPATH);
    return [
        'load'       => is_array($load) ? array_map(static fn($x) => round((float) $x, 2), array_slice($load, 0, 3)) : null,
        'cores'      => $cores ?: null,
        'disk_total' => $total ? (int) $total : null,
        'disk_free'  => $free ? (int) $free : null,
        'software'   => isset($_SERVER['SERVER_SOFTWARE']) ? substr((string) $_SERVER['SERVER_SOFTWARE'], 0, 80) : '',
        'sapi'       => PHP_SAPI,
        'os'         => PHP_OS_FAMILY,
        'hostname'   => (string) @gethostname(),
    ];
}

function tdpanop_sizes(float $budget = 25.0): array
{
    $t0      = microtime(true);
    $norm    = function ($p) {
        return untrailingslashit(wp_normalize_path((string) $p));
    };
    $abs     = $norm(ABSPATH);
    $content = $norm(WP_CONTENT_DIR);
    $up      = wp_upload_dir(null, false);
    $uploads = $norm($up['basedir'] ?? '');
    $plugins = $norm(WP_PLUGIN_DIR);
    $mu      = $norm(defined('WPMU_PLUGIN_DIR') ? WPMU_PLUGIN_DIR : $content . '/mu-plugins');
    $themes  = $norm(get_theme_root());
    $b       = ['uploads' => 0, 'plugins' => 0, 'themes' => 0, 'content_other' => 0, 'core' => 0];
    $roots   = [$abs];
    foreach ([$content, $uploads] as $d) {
        if ($d !== '' && strpos($d . '/', $abs . '/') !== 0) {
            $roots[] = $d;
        }
    }
    $bucket = function ($path) use ($uploads, $plugins, $mu, $themes, $content) {
        if ($uploads !== '' && strpos($path, $uploads . '/') === 0) {
            return 'uploads';
        }
        if (strpos($path, $plugins . '/') === 0 || strpos($path, $mu . '/') === 0) {
            return 'plugins';
        }
        if (strpos($path, $themes . '/') === 0) {
            return 'themes';
        }
        if (strpos($path, $content . '/') === 0) {
            return 'content_other';
        }
        return 'core';
    };
    $complete = true;
    $files    = 0;
    $big_logs = [];   // log grandi dentro il sito: riempiono lo spazio in silenzio
    foreach (array_unique($roots) as $root) {
        try {
            $it = new RecursiveIteratorIterator(
                new RecursiveDirectoryIterator($root, FilesystemIterator::SKIP_DOTS),
                RecursiveIteratorIterator::LEAVES_ONLY,
                RecursiveIteratorIterator::CATCH_GET_CHILD
            );
            foreach ($it as $f) {
                if (++$files % 500 === 0 && (microtime(true) - $t0) > $budget) {
                    $complete = false;
                    break 2;
                }
                try {
                    if (!$f->isFile()) {
                        continue;
                    }
                    $sz = (int) $f->getSize();
                } catch (\Throwable $e) {
                    continue;
                }
                $b[$bucket(wp_normalize_path($f->getPathname()))] += $sz;
                if (count($big_logs) < 20 && tdpanop_is_big_log($f->getFilename(), $sz)) {
                    $big_logs[] = ['path' => wp_normalize_path($f->getPathname()), 'bytes' => $sz, 'outside' => false];
                }
            }
        } catch (\Throwable $e) {
            $complete = false;
        }
    }
    foreach (tdpanop_outside_logs($abs) as $l) {
        if (count($big_logs) < 30) {
            $big_logs[] = $l;
        }
    }
    usort($big_logs, static function ($x, $y) { return $y['bytes'] <=> $x['bytes']; });
    $b['big_logs']    = $big_logs;
    $b['db']          = tdpanop_db_size();
    $b['files_total'] = $b['uploads'] + $b['plugins'] + $b['themes'] + $b['content_other'] + $b['core'];
    $b['total']       = $b['files_total'] + $b['db'];
    $b['complete']    = $complete;
    $b['files']       = $files;
    $b['seconds']     = round(microtime(true) - $t0, 2);
    return $b;
}

/**
 * Verifica dei file del core con le impronte ufficiali di wordpress.org (come
 * "wp core verify-checksums"): file modificati, mancanti, e file in piu' dentro wp-admin e
 * wp-includes (resti di vecchie versioni dopo un aggiornamento via FTP, o file estranei).
 */
function tdpanop_core_integrity(): array
{
    require_once ABSPATH . 'wp-admin/includes/update.php';
    global $wp_local_package;
    $version = get_bloginfo('version');
    $locale  = (isset($wp_local_package) && $wp_local_package) ? (string) $wp_local_package : 'en_US';
    $sums    = get_core_checksums($version, $locale);
    if ((!is_array($sums) || !$sums) && $locale !== 'en_US') {
        $sums = get_core_checksums($version, 'en_US');
    }
    if (!is_array($sums) || !$sums) {
        return ['status' => 'unavailable', 'version' => $version,
                'error' => 'impronte ufficiali non disponibili per questa versione'];
    }
    // File che hosting, traduzioni e strumenti toccano di continuo e che WordPress non esegue
    // mai: segnalarli e' solo rumore (es. wp-config-sample.php, readme, licenze).
    $harmless = ['wp-config-sample.php', 'readme.html', 'license.txt', 'licenza.html', 'liesmich.html', 'licence.txt'];
    // File in piu' che sono log o configurazioni del server, non file estranei.
    $noise = static function (string $rel): bool {
        return (bool) preg_match('#(^|/)(error_log|php_errorlog|php_error_log|\.user\.ini|php\.ini|\.htaccess|web\.config|\.DS_Store|Thumbs\.db|desktop\.ini)$#i', $rel)
            || (bool) preg_match('#\.log$#i', $rel);
    };
    $ignored  = 0;
    $modified = [];
    $missing  = [];
    $checked  = 0;
    foreach ($sums as $file => $md5) {
        if (strpos($file, 'wp-content/') === 0) {
            continue;   // temi e plugin predefiniti: non sono core
        }
        if (in_array($file, $harmless, true)) {
            $ignored++;
            continue;
        }
        $path = ABSPATH . $file;
        if (!is_file($path)) {
            $missing[] = $file;
            continue;
        }
        $checked++;
        if (md5_file($path) !== $md5) {
            $modified[] = $file;
        }
    }
    $extra = [];
    foreach (['wp-admin', 'wp-includes'] as $d) {
        $base = ABSPATH . $d;
        if (!is_dir($base)) {
            continue;
        }
        try {
            $it = new RecursiveIteratorIterator(
                new RecursiveDirectoryIterator($base, FilesystemIterator::SKIP_DOTS),
                RecursiveIteratorIterator::LEAVES_ONLY,
                RecursiveIteratorIterator::CATCH_GET_CHILD
            );
            foreach ($it as $f) {
                if (!$f->isFile()) {
                    continue;
                }
                $rel = ltrim(str_replace('\\', '/', substr($f->getPathname(), strlen(ABSPATH))), '/');
                if (!isset($sums[$rel])) {
                    if ($noise($rel)) {
                        $ignored++;
                        continue;
                    }
                    $extra[] = $rel;
                }
            }
        } catch (\Throwable $e) {
            // cartella non leggibile: si va avanti con quello che si e' visto
        }
    }
    sort($modified);
    sort($missing);
    sort($extra);
    return [
        'status'         => ($modified || $missing || $extra) ? 'issues' : 'ok',
        'version'        => $version,
        'locale'         => $locale,
        'checked'        => $checked,
        'modified_count' => count($modified),
        'missing_count'  => count($missing),
        'extra_count'    => count($extra),
        'ignored_count'  => $ignored,   // file innocui non segnalati (esempi, readme, log, ini)
        'modified'       => array_slice($modified, 0, 100),
        'missing'        => array_slice($missing, 0, 100),
        'extra'          => array_slice($extra, 0, 100),
    ];
}

function tdpanop_diagnostics(WP_REST_Request $req)
{
    // il tempo massimo del sito si legge PRIMA di alzarlo per la diagnostica stessa
    // (2.19.0 lo leggeva dopo e mostrava il proprio 180 invece del valore vero)
    $maxExec = (int) ini_get('max_execution_time');
    @set_time_limit(180);
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/plugin.php';

    $space     = (int) $req->get_param('space');
    $wantSizes = $req->get_param('sizes') === null ? true : (bool) (int) $req->get_param('sizes');
    $wantCore  = $req->get_param('core') === null ? true : (bool) (int) $req->get_param('core');
    $tmp       = get_temp_dir();
    $upg       = WP_CONTENT_DIR . '/upgrade';

    $out = [
        'diagnostics'        => 1,
        'cms'                => 'wp',
        'php'                => PHP_VERSION,
        'memory_limit'       => (string) ini_get('memory_limit'),
        'wp_memory_limit'    => defined('WP_MEMORY_LIMIT') ? (string) WP_MEMORY_LIMIT : '',
        'max_execution_time' => $maxExec,
        'zip'                => class_exists('ZipArchive'),
        'fs_method'          => function_exists('get_filesystem_method') ? (string) get_filesystem_method() : '',
        'temp_dir'           => $tmp,
        'temp_custom'        => defined('WP_TEMP_DIR'),
        'temp_writable'      => wp_is_writable($tmp),
        'content_writable'   => wp_is_writable(WP_CONTENT_DIR),
        'server'             => tdpanop_server_info(),   // carico, disco, software: per la pagina Stato server
        'upgrade_writable'   => is_dir($upg) ? wp_is_writable($upg) : wp_is_writable(WP_CONTENT_DIR),
        'file_mods_allowed'  => !(defined('DISALLOW_FILE_MODS') && DISALLOW_FILE_MODS),
        'disk_free'          => function_exists('disk_free_space') ? (float) @disk_free_space(WP_CONTENT_DIR) : null,
    ];
    if ($space > 0) {
        delete_transient('tdpanop_space_ok');
        $out['space'] = tdpanop_space_check(min(400, $space));
    }
    if ($wantSizes) {
        $out['sizes'] = tdpanop_sizes();
    }
    if ($wantCore) {
        $out['core'] = tdpanop_core_integrity();
    }
    return new WP_REST_Response($out, 200);
}

/* ---------------------------------------------------------------------------
 * PACCHETTO DA UN SITO (2.19.0)
 * GET /package?type=plugin|theme&slug=...  ->  zip del plugin o tema installato.
 * Serve ai prodotti a licenza (Elementor Pro…): dove l'aggiornamento e' riuscito, Sentinel
 * prende da qui lo zip e lo installa sui siti a cui il produttore non lo consegna.
 * ------------------------------------------------------------------------- */
function tdpanop_package_export(WP_REST_Request $req)
{
    @set_time_limit(300);
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/plugin.php';

    $type = sanitize_key((string) $req->get_param('type'));
    $slug = sanitize_text_field((string) $req->get_param('slug'));
    if (!class_exists('ZipArchive')) {
        return new WP_REST_Response(['ok' => false, 'error' => 'modulo zip di PHP assente su questo sito: impossibile creare il pacchetto'], 200);
    }

    $src    = '';
    $root   = '';
    $single = false;
    $ver    = '';
    if ($type === 'plugin') {
        $file = tdpanop_plugin_file_by_slug($slug);
        if ($file === '') {
            return new WP_REST_Response(['ok' => false, 'error' => 'plugin non trovato: ' . $slug], 200);
        }
        $plugins = get_plugins();
        $ver     = isset($plugins[$file]['Version']) ? (string) $plugins[$file]['Version'] : '';
        $dir     = dirname($file);
        if ($dir === '.' || $dir === '') {
            $src    = WP_PLUGIN_DIR . '/' . $file;
            $root   = basename($file);
            $single = true;
        } else {
            $src  = WP_PLUGIN_DIR . '/' . $dir;
            $root = $dir;
        }
    } elseif ($type === 'theme') {
        $th = wp_get_theme($slug);
        if (!$th->exists()) {
            return new WP_REST_Response(['ok' => false, 'error' => 'tema non trovato: ' . $slug], 200);
        }
        $src  = $th->get_stylesheet_directory();
        $root = $th->get_stylesheet();
        $ver  = (string) $th->get('Version');
    } else {
        return new WP_REST_Response(['ok' => false, 'error' => 'type non valido'], 200);
    }

    $tmp = wp_tempnam($root . '.zip');
    $zip = new ZipArchive();
    if ($zip->open($tmp, ZipArchive::CREATE | ZipArchive::OVERWRITE) !== true) {
        @unlink($tmp);
        return new WP_REST_Response(['ok' => false, 'error' => 'impossibile creare lo zip nella cartella temporanea'], 200);
    }
    if ($single) {
        $zip->addFile($src, $root);
    } else {
        $base = untrailingslashit(wp_normalize_path($src));
        $it   = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($base, FilesystemIterator::SKIP_DOTS),
            RecursiveIteratorIterator::LEAVES_ONLY,
            RecursiveIteratorIterator::CATCH_GET_CHILD
        );
        foreach ($it as $f) {
            if (!$f->isFile()) {
                continue;
            }
            $path = wp_normalize_path($f->getPathname());
            $zip->addFile($path, $root . '/' . ltrim(substr($path, strlen($base)), '/'));
        }
    }
    if ($zip->close() !== true) {
        @unlink($tmp);
        return new WP_REST_Response(['ok' => false, 'error' => 'zip non completato (spazio esaurito nella cartella temporanea?)'], 200);
    }
    clearstatcache(true, $tmp);
    $size = (int) @filesize($tmp);
    if ($size <= 0) {
        @unlink($tmp);
        return new WP_REST_Response(['ok' => false, 'error' => 'zip vuoto'], 200);
    }

    while (ob_get_level()) {
        ob_end_clean();
    }
    nocache_headers();
    header('Content-Type: application/zip');
    header('Content-Length: ' . $size);
    header('Content-Disposition: attachment; filename="' . sanitize_file_name($root . '-' . $ver) . '.zip"');
    header('X-Sentinel-Version: ' . $ver);
    readfile($tmp);
    @unlink($tmp);
    exit;
}

/* Autologin one-time (60s). Link verso la home per non urtare login custom. */
function tdpanop_autologin()
{
    $admins = get_users(['role' => 'administrator', 'number' => 1, 'orderby' => 'ID', 'order' => 'ASC']);
    if (empty($admins)) {
        return new WP_Error('no_admin', 'Nessun amministratore', ['status' => 404]);
    }
    $uid = $admins[0]->ID;
    $key = wp_generate_password(40, false);
    set_transient('tdpanop_al_' . $key, $uid, 60);
    return new WP_REST_Response(['url' => add_query_arg(['tdpanop_al' => $key], home_url('/'))], 200);
}

add_action('init', function () {
    if (empty($_GET['tdpanop_al'])) {
        return;
    }
    $key = sanitize_text_field($_GET['tdpanop_al']);
    $uid = get_transient('tdpanop_al_' . $key);
    if (!$uid) {
        return;
    }
    delete_transient('tdpanop_al_' . $key);
    wp_set_current_user($uid);
    wp_set_auth_cookie($uid, false);
    wp_safe_redirect(admin_url());
    exit;
});

/* ---------------------------------------------------------------------------
 * Cache da svuotare dopo gli aggiornamenti.
 * Dopo un aggiornamento di plugin, temi o core le cache del sito possono restare incoerenti
 * con il codice nuovo: CSS generati dai costruttori di pagine (Elementor, Essential Addons,
 * Beaver Builder, Divi, Avada), pagine in cache che puntano ancora a CSS e JS vecchi, dati
 * del plugin vecchio nella cache degli oggetti (Redis). Risultato tipico: pagine senza
 * stili, layout rotti o pagine che vanno in timeout finche' qualcuno svuota a mano.
 * Qui lo si fa da soli, per QUALUNQUE aggiornamento o installazione (connettore,
 * installazione in blocco, wp-admin, aggiornamenti automatici di WordPress).
 * Ogni pulizia parte solo se quel plugin o tema c'e', usando il suo comando ufficiale; un
 * errore in una non ferma le altre e non blocca mai il sito.
 * La pulizia avviene alla richiesta SUCCESSIVA, non in quella dell'aggiornamento, dove in
 * memoria c'e' ancora il codice vecchio: e la richiesta successiva arriva subito, perche'
 * dopo ogni aggiornamento il pannello rilegge lo stato del sito.
 * ------------------------------------------------------------------------- */
add_action('upgrader_process_complete', function ($upgrader, $extra) {
    $type = is_array($extra) ? (string) ($extra['type'] ?? '') : '';
    if (in_array($type, ['plugin', 'theme', 'core'], true)) {
        update_option('tdpanop_flush_caches', time(), true);
    }
}, 10, 2);

/**
 * Svuota le cache del sito. Ritorna l'elenco di quelle svuotate (per il log del connettore).
 */
function tdpanop_flush_caches(): array
{
    $done = [];
    $try = static function (string $name, callable $fn) use (&$done) {
        try {
            if ($fn() !== false) {
                $done[] = $name;
            }
        } catch (\Throwable $e) {
            // una cache che non si svuota non deve fermare le altre ne' il sito
        }
    };

    // --- costruttori di pagine: CSS generati
    if (class_exists('\Elementor\Plugin') && !empty(\Elementor\Plugin::$instance)) {
        $try('Elementor', static function () {
            $fm = \Elementor\Plugin::$instance->files_manager ?? null;
            if (!$fm || !method_exists($fm, 'clear_cache')) {
                return false;
            }
            $fm->clear_cache();   // come "wp elementor flush-css"
            return true;
        });
    }
    $up = wp_upload_dir(null, false);
    $eaDir = !empty($up['basedir']) ? $up['basedir'] . '/essential-addons-elementor' : '';
    if ($eaDir !== '' && is_dir($eaDir) && (defined('EAEL_PLUGIN_VERSION') || class_exists('\Essential_Addons_Elementor\Classes\Bootstrap'))) {
        $try('Essential Addons', static function () use ($eaDir) {
            foreach ((array) glob($eaDir . '/*.{css,js}', GLOB_BRACE) as $f) {
                if (is_file($f)) {
                    @unlink($f);   // file uniti di Essential Addons: si rigenerano alla prima visita
                }
            }
            return true;
        });
    }
    if (class_exists('FLBuilderModel') && method_exists('FLBuilderModel', 'delete_asset_cache_for_all_posts')) {
        $try('Beaver Builder', static function () { \FLBuilderModel::delete_asset_cache_for_all_posts(); return true; });
    }
    if (class_exists('ET_Core_PageResource') && method_exists('ET_Core_PageResource', 'remove_static_resources')) {
        $try('Divi', static function () { \ET_Core_PageResource::remove_static_resources('all', 'all'); return true; });
    }
    if (function_exists('fusion_reset_all_caches')) {
        $try('Avada', static function () { fusion_reset_all_caches(); return true; });
    }

    // --- cache di pagina e di file uniti
    if (function_exists('rocket_clean_domain')) {
        $try('WP Rocket', static function () {
            rocket_clean_domain();
            if (function_exists('rocket_clean_minify')) {
                rocket_clean_minify();
            }
            return true;
        });
    }
    if (function_exists('w3tc_flush_all')) {
        $try('W3 Total Cache', static function () { w3tc_flush_all(); return true; });
    }
    if (defined('LSCWP_V') || class_exists('\LiteSpeed\Purge')) {
        $try('LiteSpeed Cache', static function () { do_action('litespeed_purge_all'); return true; });
    }
    if (function_exists('wp_cache_clear_cache')) {
        $try('WP Super Cache', static function () { wp_cache_clear_cache(); return true; });
    }
    if (isset($GLOBALS['wp_fastest_cache']) && is_object($GLOBALS['wp_fastest_cache']) && method_exists($GLOBALS['wp_fastest_cache'], 'deleteCache')) {
        $try('WP Fastest Cache', static function () { $GLOBALS['wp_fastest_cache']->deleteCache(true); return true; });
    }
    if (function_exists('sg_cachepress_purge_cache')) {
        $try('SiteGround Optimizer', static function () { sg_cachepress_purge_cache(); return true; });
    }
    if (defined('BREEZE_VERSION')) {
        $try('Breeze', static function () { do_action('breeze_clear_all_cache'); return true; });
    }
    if (class_exists('Cache_Enabler')) {
        $try('Cache Enabler', static function () { do_action('cache_enabler_clear_complete_cache'); return true; });
    }
    if (defined('WPHB_VERSION')) {
        $try('Hummingbird', static function () { do_action('wphb_clear_page_cache'); return true; });
    }
    if (defined('NGINX_HELPER_BASENAME') || class_exists('Nginx_Helper')) {
        $try('Nginx Helper', static function () { do_action('rt_nginx_helper_purge_all'); return true; });
    }
    if (class_exists('autoptimizeCache') && method_exists('autoptimizeCache', 'clearall')) {
        $try('Autoptimize', static function () { \autoptimizeCache::clearall(); return true; });
    }

    // --- cache degli oggetti (Redis, Memcached): niente dati del codice vecchio in memoria.
    // Lo svuotamento completo solo se cancella le chiavi di QUESTO sito e basta
    // (WP_REDIS_SELECTIVE_FLUSH del plugin Redis Object Cache): con un Redis condiviso tra
    // piu' siti o applicazioni, wp_cache_flush() li svuoterebbe tutti. Altrimenti si tolgono
    // solo le opzioni in cache, la causa tipica dei dati vecchi dopo un aggiornamento.
    if (function_exists('wp_using_ext_object_cache') && wp_using_ext_object_cache()) {
        if (defined('WP_REDIS_SELECTIVE_FLUSH') && WP_REDIS_SELECTIVE_FLUSH && function_exists('wp_cache_flush')) {
            $try('cache degli oggetti', static function () { return wp_cache_flush(); });
        } else {
            $try('opzioni in cache', static function () {
                wp_cache_delete('alloptions', 'options');
                wp_cache_delete('notoptions', 'options');
                return true;
            });
        }
    }
    return $done;
}

/**
 * YOOtheme Pro: svuota la cache della CONFIGURAZIONE (wp-content/themes/yootheme/cache),
 * la stessa cartella che svuota il suo pulsante "Svuota cache": builder, elementi e sorgenti
 * dinamiche compilati. E' quella che resta vecchia dopo gli aggiornamenti (es. un plugin che
 * aggiunge elementi al builder) e si ricostruisce da sola. NON si tocca la cache delle
 * immagini (uploads/yootheme/cache), che il pulsante svuota ma dopo un aggiornamento non
 * serve: rigenerare tutte le immagini ridimensionate peserebbe sui server deboli. Il CSS del
 * tema non e' in nessuna delle due: lo compila il personalizzatore nel browser.
 */
function tdpanop_flush_yootheme(): bool
{
    $dir = function_exists('get_template_directory') ? get_template_directory() : '';
    // YOOtheme e' il tema (o il padre del tema figlio) attivo e la cartella e' davvero la sua
    if ($dir === '' || !is_dir($dir . '/cache') || !is_file($dir . '/packages/theme-settings/src/CacheController.php')) {
        return false;
    }
    $cache = realpath($dir . '/cache');
    if ($cache === false) {
        return false;
    }
    $it = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($cache, FilesystemIterator::SKIP_DOTS),
        RecursiveIteratorIterator::CHILD_FIRST
    );
    foreach ($it as $f) {
        $path = $f->getPathname();
        if (strpos($path, $cache . DIRECTORY_SEPARATOR) !== 0) {
            continue;   // mai fuori dalla cartella della cache
        }
        $f->isDir() && !$f->isLink() ? @rmdir($path) : @unlink($path);
    }
    return true;
}

// Prima che YOOtheme rilegga la sua configurazione (che avviene col tema): cosi' gia' questa
// richiesta parte pulita. Il segno resta per le altre pulizie, sotto.
add_action('plugins_loaded', function () {
    if (get_option('tdpanop_flush_caches') && !get_option('tdpanop_flush_yoo_done')) {
        try {
            if (tdpanop_flush_yootheme()) {
                update_option('tdpanop_flush_yoo_done', 1, false);
            }
        } catch (\Throwable $e) {
            // mai bloccare il sito per una pulizia di cache
        }
    }
}, 1);

add_action('wp_loaded', function () {
    if (!get_option('tdpanop_flush_caches')) {
        return;
    }
    delete_option('tdpanop_flush_caches');
    $done = tdpanop_flush_caches();
    if (get_option('tdpanop_flush_yoo_done')) {
        array_unshift($done, 'YOOtheme');
        delete_option('tdpanop_flush_yoo_done');
    }
    update_option('tdpanop_last_flush', ['at' => time(), 'caches' => $done], false);
}, 99);

} // fine blocco condizionale (vedi sopra)

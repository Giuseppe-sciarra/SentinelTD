<?php

/**
 * Plugin Name: Sentinel TD Agent
 * Description: Connettore di Sentinel TD: espone stato versioni/update via REST e consente aggiornamenti da remoto. Token e collegamento in Impostazioni → Sentinel TD.
 * Version: 2.41.3
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
    // Un'altra copia e' gia' caricata (mu-plugin, o l'altra cartella): questa esce in silenzio.
    // Nessun avviso in amministrazione: chi gestisce il sito non deve vedere niente.
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
// Token cablato: usato SOLO dalla build mu-plugin headless che Sentinel genera per un sito
// gia' collegato (col token di quel sito gia' dentro). Vuoto nel sorgente e nelle build
// normali: li' il token resta quello generato/salvato nell'option, come sempre.
const TDPANOP_TOKEN = '';

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
            'Ultima chiamata a Sentinel TD: %s' => 'Last call to Sentinel TD: %s',
            'Chiama Sentinel TD ora' => 'Call Sentinel TD now',
            'chiamata riuscita: Sentinel TD raggiunge il sito direttamente' => 'call succeeded: Sentinel TD reaches the site directly',
            'modalità firewall: stato mandato, lavori eseguiti: %s' => 'firewall mode: status sent, jobs run: %s',
            'modalità firewall: stato mandato a Sentinel TD' => 'firewall mode: status sent to Sentinel TD',
            'chiamata non riuscita: %s' => 'call failed: %s',
            'indirizzo di Sentinel TD mancante: collega il sito o installa il connettore scaricato dal pannello' => 'Sentinel TD address missing: connect the site or install the connector downloaded from the panel',
            'una chiamata a Sentinel TD è già in corso, riprova fra un minuto' => 'a call to Sentinel TD is already running, try again in a minute',
            'Modalità firewall' => 'Firewall mode',
            'attiva: un firewall blocca Sentinel TD, è il sito a chiamarlo ogni 5 minuti' => 'on: a firewall blocks Sentinel TD, so the site calls it every 5 minutes',
            'non attiva: Sentinel TD raggiunge il sito direttamente' => 'off: Sentinel TD reaches the site directly',
            'Connettore di Sentinel TD: espone stato versioni/update via REST e consente aggiornamenti da remoto. Token e collegamento in Impostazioni → Sentinel TD.' => 'Sentinel TD connector: exposes versions and available updates over REST and allows remote updates. Token and connection in Settings → Sentinel TD.',
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
            'Ultima chiamata a Sentinel TD: %s' => 'Dernier appel à Sentinel TD : %s',
            'Chiama Sentinel TD ora' => 'Appeler Sentinel TD maintenant',
            'chiamata riuscita: Sentinel TD raggiunge il sito direttamente' => 'appel réussi : Sentinel TD atteint le site directement',
            'modalità firewall: stato mandato, lavori eseguiti: %s' => 'mode pare-feu : état envoyé, tâches exécutées : %s',
            'modalità firewall: stato mandato a Sentinel TD' => 'mode pare-feu : état envoyé à Sentinel TD',
            'chiamata non riuscita: %s' => 'appel échoué : %s',
            'indirizzo di Sentinel TD mancante: collega il sito o installa il connettore scaricato dal pannello' => 'adresse de Sentinel TD manquante : connectez le site ou installez le connecteur téléchargé depuis le panneau',
            'una chiamata a Sentinel TD è già in corso, riprova fra un minuto' => 'un appel à Sentinel TD est déjà en cours, réessayez dans une minute',
            'Modalità firewall' => 'Mode pare-feu',
            'attiva: un firewall blocca Sentinel TD, è il sito a chiamarlo ogni 5 minuti' => 'actif : un pare-feu bloque Sentinel TD, c\'est le site qui l\'appelle toutes les 5 minutes',
            'non attiva: Sentinel TD raggiunge il sito direttamente' => 'inactif : Sentinel TD atteint le site directement',
            'Connettore di Sentinel TD: espone stato versioni/update via REST e consente aggiornamenti da remoto. Token e collegamento in Impostazioni → Sentinel TD.' => 'Connecteur Sentinel TD : expose les versions et les mises à jour disponibles via REST et permet les mises à jour à distance. Jeton et connexion dans Réglages → Sentinel TD.',
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
            'Ultima chiamata a Sentinel TD: %s' => 'Letzter Aufruf von Sentinel TD: %s',
            'Chiama Sentinel TD ora' => 'Sentinel TD jetzt aufrufen',
            'chiamata riuscita: Sentinel TD raggiunge il sito direttamente' => 'Aufruf erfolgreich: Sentinel TD erreicht die Website direkt',
            'modalità firewall: stato mandato, lavori eseguiti: %s' => 'Firewall-Modus: Status gesendet, ausgeführte Aufträge: %s',
            'modalità firewall: stato mandato a Sentinel TD' => 'Firewall-Modus: Status an Sentinel TD gesendet',
            'chiamata non riuscita: %s' => 'Aufruf fehlgeschlagen: %s',
            'indirizzo di Sentinel TD mancante: collega il sito o installa il connettore scaricato dal pannello' => 'Adresse von Sentinel TD fehlt: Website verbinden oder den im Panel heruntergeladenen Connector installieren',
            'una chiamata a Sentinel TD è già in corso, riprova fra un minuto' => 'Ein Aufruf von Sentinel TD läuft bereits, versuche es in einer Minute erneut',
            'Modalità firewall' => 'Firewall-Modus',
            'attiva: un firewall blocca Sentinel TD, è il sito a chiamarlo ogni 5 minuti' => 'aktiv: Eine Firewall blockiert Sentinel TD, deshalb meldet sich die Website alle 5 Minuten selbst',
            'non attiva: Sentinel TD raggiunge il sito direttamente' => 'inaktiv: Sentinel TD erreicht die Website direkt',
            'Connettore di Sentinel TD: espone stato versioni/update via REST e consente aggiornamenti da remoto. Token e collegamento in Impostazioni → Sentinel TD.' => 'Sentinel-TD-Connector: stellt Versionen und verfügbare Updates per REST bereit und ermöglicht Updates aus der Ferne. Token und Verbindung unter Einstellungen → Sentinel TD.',
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

/* ---------------------------------------------------------------------------
 * Aggiornamenti automatici di WordPress spenti (di base): gli aggiornamenti li fa Sentinel (copia
 * prima, controllo della home, storico, notifiche). Stesso effetto di
 * define('AUTOMATIC_UPDATER_DISABLED', true) in wp-config.php e del mu-plugin
 * "TD Site Health Tweaks", che toglie da Salute del sito il test "aggiornamenti in
 * background" (con gli aggiornamenti spenti apposta segnerebbe un problema critico falso).
 * Dove queste due cose ci sono gia', il connettore non fa niente.
 * Si accende e si spegne dal pannello (Impostazioni -> Aggiornamenti): la scelta arriva con
 * ogni controllo del sito e il connettore se la segna (option tdpanop_wp_autoupdates).
 * Finche' il pannello non dice niente (connettore appena installato) gli aggiornamenti
 * automatici di WordPress restano spenti.
 * ------------------------------------------------------------------------- */
define('TDPANOP_AUTOUPD_OPT', 'tdpanop_wp_autoupdates');   // 'off' = spenti (di base), 'on' = lasciati a WordPress

function tdpanop_auto_updates_already_off(): bool
{
    return defined('AUTOMATIC_UPDATER_DISABLED') && AUTOMATIC_UPDATER_DISABLED;
}

function tdpanop_block_auto_updates(): bool
{
    return (string) get_option(TDPANOP_AUTOUPD_OPT, 'off') !== 'on';
}

/** Scelta del pannello arrivata con il controllo: "block" o "allow". Si scrive solo se cambia. */
function tdpanop_apply_auto_updates_choice($choice): void
{
    $choice = (string) $choice;
    if ($choice !== 'block' && $choice !== 'allow') {
        return;     // pannello vecchio o parametro assente: resta com'era
    }
    $want = $choice === 'allow' ? 'on' : 'off';
    if ((string) get_option(TDPANOP_AUTOUPD_OPT, 'off') !== $want) {
        update_option(TDPANOP_AUTOUPD_OPT, $want, true);
    }
}

/** Stato per il pannello: "wp-config" (costante gia' a true), "blocked" o "allowed". */
function tdpanop_auto_updates_state(): string
{
    if (tdpanop_auto_updates_already_off()) {
        return 'wp-config';
    }
    return tdpanop_block_auto_updates() ? 'blocked' : 'allowed';
}

function tdpanop_health_tweaks_present(): bool
{
    return defined('WPMU_PLUGIN_DIR') && is_file(rtrim(WPMU_PLUGIN_DIR, '/\\') . '/td-site-health-tweaks.php');
}

function tdpanop_hide_background_updates_test($tests)
{
    // il test vive sotto "async", non "direct": si toglie da entrambi, come il mu-plugin
    if (is_array($tests)) {
        unset($tests['async']['background_updates'], $tests['direct']['background_updates']);
    }
    return $tests;
}

// I filtri leggono la scelta quando WordPress li chiama (molto dopo il caricamento dei plugin):
// una scelta arrivata con il controllo vale subito, senza aspettare la richiesta dopo.
if (!tdpanop_auto_updates_already_off()) {
    add_filter('automatic_updater_disabled', function ($disabled) {
        return tdpanop_block_auto_updates() ? true : $disabled;
    }, 999);
}
if (!tdpanop_health_tweaks_present()) {
    add_filter('site_status_tests', function ($tests) {
        return tdpanop_block_auto_updates() ? tdpanop_hide_background_updates_test($tests) : $tests;
    });
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
    // Token cablato dalla build headless (ha la precedenza): il mu-plugin autentica senza
    // pagina di impostazioni e senza dipendere dall'option.
    if (TDPANOP_TOKEN !== '') {
        return TDPANOP_TOKEN;
    }
    $t = get_option(TDPANOP_OPT);
    if (!$t) {
        // Generazione ATOMICA, a prova di object cache (Redis).
        // Il vecchio get+update aveva una race: due richieste quasi simultanee (es. il
        // POST "Collega" e il render della pagina) su worker FPM diversi, con la cache
        // alloptions non coerente, generavano DUE token diversi - uno finiva al pannello,
        // l'altro restava sul sito -> mismatch dalla nascita (caso reale).
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

    // modalita' firewall: chiamata a Sentinel adesso, senza aspettare il giro
    if (isset($_POST['tdpanop_relay_now']) && check_admin_referer('tdpanop_relay_now')) {
        // veloce: solo la chiamata e lo stato completo; i lavori li prende il giro normale (dietro
        // Cloudflare una pagina che dura piu' di 100 secondi viene interrotta)
        $tdpanop_out = tdpanop_relay_tick(['force' => true, 'nojobs' => true, 'status_now' => true]);
        $tdpanop_ok = in_array(explode(':', $tdpanop_out)[0], ['ping', 'report'], true);
        echo '<div class="notice ' . ($tdpanop_ok ? 'notice-success' : 'notice-error') . ' is-dismissible"><p>'
            . esc_html(tdpanop_relay_outcome_text($tdpanop_out)) . '</p></div>';
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
            <tr>
                <th scope="row"><?php echo esc_html(tdpanop__('Modalità firewall')); ?></th>
                <td><?php
                    $tdpanop_rs = tdpanop_relay_state();
                    echo esc_html($tdpanop_rs['mode'] === 'relay'
                        ? tdpanop__('attiva: un firewall blocca Sentinel TD, è il sito a chiamarlo ogni 5 minuti')
                        : tdpanop__('non attiva: Sentinel TD raggiunge il sito direttamente'));
                    $tdpanop_hub = tdpanop_relay_hub();
                    if ($tdpanop_hub !== '') {
                        echo '<br><code>' . esc_html($tdpanop_hub) . '</code>';
                    }
                    if (!empty($tdpanop_rs['last_at'])) {
                        echo '<br>' . esc_html(sprintf(tdpanop__('Ultima chiamata a Sentinel TD: %s'),
                            (function_exists('wp_date') ? 'wp_date' : 'date_i18n')(get_option('date_format') . ' ' . get_option('time_format'), (int) $tdpanop_rs['last_at'])))
                            . ' · ' . esc_html(tdpanop_relay_outcome_text((string) ($tdpanop_rs['last'] ?? '')));
                    }
                ?>
                    <form method="post" style="margin-top:8px">
                        <?php wp_nonce_field('tdpanop_relay_now'); ?>
                        <input type="submit" name="tdpanop_relay_now" class="button" value="<?php echo esc_attr(tdpanop__('Chiama Sentinel TD ora')); ?>">
                    </form>
                </td>
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
        tdpanop_relay_note_contact($req);
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
    $ok = is_string($real) && $real !== '' && hash_equals($real, $bearer);
    if ($ok) {
        tdpanop_relay_note_contact($req);
    }
    return $ok;
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
        // aggiornamenti automatici di WordPress: la scelta del pannello (Impostazioni)
        tdpanop_apply_auto_updates_choice($req->get_param('wp_auto_updates'));
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
    $trans = tdpanop_pending_translations();
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

    $tdpanop_where = tdpanop_running_mode();
    return new WP_REST_Response([
        'cms'  => 'wp',
        'connector' => TDPANOP_VERSION,   // il pannello sa quale versione gira su ogni sito
        // dove gira QUESTA copia: "mu" (mu-plugin) o "plugin" con la sua cartella. Il giro notturno
        // del pannello aggiorna solo la copia in uso, nella sua cartella, e mai i mu-plugin.
        'mode'      => $tdpanop_where['mode'],
        'folder'    => $tdpanop_where['folder'],
        // nome della macchina (una stringa, costo zero): serve a distinguere due macchine dietro
        // lo stesso IP in Gestione server. Nessuna misura di carico, RAM o disco.
        'hostname'  => (string) @gethostname(),
        'core' => ['current' => $core_cur, 'latest' => $core_latest, 'update' => $core_update, 'known' => $core_known],
        'php'  => PHP_VERSION,
        'extensions' => $extensions,
        // false = la cache degli aggiornamenti mancava: i flag "update" qui sopra sono zeri
        // non verificati, il pannello tiene quelli dell'ultimo controllo riuscito
        'updates_known' => ['plugin' => $plugins_known, 'theme' => $themes_known, 'core' => $core_known],
        'refreshed' => $forceRefresh,
        // aggiornamenti automatici di WordPress: "wp-config" (spenti in wp-config.php),
        // "blocked" (spenti dal connettore) o "allowed" (lasciati a WordPress)
        'wp_auto_updates' => tdpanop_auto_updates_state(),
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

/**
 * Traduzioni davvero da installare, chiave "tipo|slug|lingua" -> voce del transient.
 *
 * Solo quelle REALMENTE piu' recenti di quelle gia' installate. WP a volte continua a
 * elencare la stessa traduzione anche DOPO averla aggiornata (le revisioni PO non vengono
 * riconciliate): senza questo confronto la voce "Traduzioni" resterebbe da aggiornare per
 * sempre -> riproposta ad ogni ciclo con relativa email/Telegram.
 */
function tdpanop_pending_translations(): array
{
    // Il connettore 2.39 si ricordava le traduzioni che WordPress riproponeva subito dopo
    // l'installazione e non le segnalava piu'. Cosi' pero' Sentinel nascondeva una traduzione
    // che il backend continuava a proporre (e diceva "aggiornate 1/1"). Tolto: Sentinel mostra
    // quello che mostra WordPress.
    if (get_option('tdpanop_trans_ghosts', null) !== null) {
        delete_option('tdpanop_trans_ghosts');
    }
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
                $trans[$type . '|' . $slug . '|' . $lang] = $t;
            }
        }
    }
    return $trans;
}

/** Chiave di una traduzione: "tipo|slug|lingua" (come nello stato). */
function tdpanop_translation_key($u): string
{
    $u = (object) $u;
    return (string) ($u->type ?? '') . '|' . (string) ($u->slug ?? '') . '|' . (string) ($u->language ?? '');
}

/** File .mo che il language pack scrive: serve a capire se l'installazione c'e' stata davvero. */
function tdpanop_translation_file($u): string
{
    $u = (object) $u;
    $lang = (string) ($u->language ?? '');
    $slug = (string) ($u->slug ?? '');
    if ($lang === '' || !defined('WP_LANG_DIR')) {
        return '';
    }
    switch ((string) ($u->type ?? '')) {
        case 'core':
            return WP_LANG_DIR . '/' . $lang . '.mo';
        case 'plugin':
            return WP_LANG_DIR . '/plugins/' . $slug . '-' . $lang . '.mo';
        case 'theme':
            return WP_LANG_DIR . '/themes/' . $slug . '-' . $lang . '.mo';
    }
    return '';
}

/** Nome leggibile di una traduzione per lo Storico: "Yoast SEO (de_DE)", "WordPress (it_IT)". */
function tdpanop_translation_label($u): string
{
    $u = (object) $u;
    $type = (string) ($u->type ?? '');
    $slug = (string) ($u->slug ?? '');
    $name = $slug !== '' ? $slug : '?';
    if ($type === 'core') {
        $name = 'WordPress';
    } elseif ($type === 'plugin' && function_exists('get_plugins')) {
        foreach ((array) get_plugins() as $file => $p) {
            if (dirname($file) === $slug || basename($file, '.php') === $slug) {
                $name = (string) ($p['Name'] ?? $slug);
                break;
            }
        }
    } elseif ($type === 'theme' && function_exists('wp_get_theme')) {
        $th = wp_get_theme($slug);
        if ($th && $th->exists()) {
            $name = (string) $th->get('Name');
        }
    }
    $name = html_entity_decode(wp_strip_all_tags($name), ENT_QUOTES, 'UTF-8');
    return $name . ' (' . (string) ($u->language ?? '') . ')';
}

/** Elenco di nomi per lo Storico, al massimo 5 ("… e altre 3"). */
function tdpanop_translation_names(array $labels): string
{
    $labels = array_values($labels);
    $txt = implode(', ', array_slice($labels, 0, 5));
    return count($labels) > 5 ? $txt . ' e altre ' . (count($labels) - 5) : $txt;
}

/** Data della traduzione installata (PO-Revision-Date del .po sul disco), 0 se non c'e'. */
function tdpanop_translation_installed_ts($u): int
{
    $mo = tdpanop_translation_file($u);
    if ($mo === '' || !function_exists('wp_get_pomo_file_data')) {
        return 0;
    }
    $po = substr($mo, 0, -3) . '.po';
    if (!file_exists($po)) {
        return 0;
    }
    $h = (array) wp_get_pomo_file_data($po);
    $ts = isset($h['PO-Revision-Date']) ? strtotime((string) $h['PO-Revision-Date']) : 0;
    return $ts ?: 0;
}

/**
 * Ricalcolo completo degli aggiornamenti, come "Controlla di nuovo" nel backend.
 * - Con i tempi del cron: fuori dal cron WordPress da' a wordpress.org 3 secondi, e su un
 *   hosting lento la richiesta scade lasciando la lista vuota (= "nessuna traduzione", falso).
 * - Con l'elenco dei file di traduzione riletto dal disco: WordPress lo tiene in cache fino a
 *   un'ora, e con una cache vecchia direbbe a wordpress.org che manca una traduzione appena
 *   installata.
 * Torna false se la lista non e' stata ricalcolata (wordpress.org non ha risposto).
 */
function tdpanop_refresh_update_data(): bool
{
    tdpanop_wake_premium_updaters();
    if (defined('WP_LANG_DIR') && function_exists('wp_cache_delete')) {
        foreach (['/', '/plugins/', '/themes/'] as $sub) {
            wp_cache_delete(md5(WP_LANG_DIR . $sub), 'translation_files');
        }
    }
    delete_site_transient('update_plugins');
    delete_site_transient('update_themes');
    delete_site_transient('update_core');
    $cron = function () {
        return true;
    };
    add_filter('wp_doing_cron', $cron, 999);
    try {
        wp_version_check();
        wp_update_plugins();
        wp_update_themes();
    } finally {
        remove_filter('wp_doing_cron', $cron, 999);
    }
    $p = get_site_transient('update_plugins');
    $t = get_site_transient('update_themes');
    $c = get_site_transient('update_core');
    return is_object($p) && isset($p->checked) && is_object($t) && isset($t->checked)
        && is_object($c) && !empty($c->updates);
}

function tdpanop_update(WP_REST_Request $req)
{
    $type = (string) $req->get_param('type');
    $slug = (string) $req->get_param('slug');
    // copia zip prima dell'aggiornamento: il pannello manda backup=0 quando e' spenta in Impostazioni
    // (e' l'operazione piu' pesante sull'hosting). Di base resta attiva.
    $doBackup = (string) $req->get_param('backup') !== '0';

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

            if ($doBackup) {
                tdpanop_backup_item('plugin', $slug);   // copia della versione attuale, per il ripristino
            }
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

            if ($doBackup) {
                tdpanop_backup_item('theme', $slug);   // copia della versione attuale, per il ripristino
            }
            $up  = new Theme_Upgrader($skin);
            $res = $up->upgrade($slug);

            wp_clean_themes_cache(false);
            $t = wp_get_theme($slug);
            $verAfter = $t->exists() ? (string) $t->get('Version') : '';
        } elseif ($type === 'language') {
            // Aggiorna TUTTE le traduzioni proposte (core + plugin + temi), come il pulsante
            // "Aggiorna le traduzioni" del backend. Riuscito SOLO se, ricontrollato con
            // wordpress.org, WordPress non le propone piu'. Il file scritto da solo non basta:
            // su alcuni siti WordPress ripropone la stessa traduzione subito dopo, e risultava
            // "aggiornate (1/1)" mentre il backend la mostrava ancora.
            require_once ABSPATH . 'wp-admin/includes/class-language-pack-upgrader.php';

            $due = tdpanop_pending_translations();   // quelle che Sentinel vede da aggiornare
            if (!$due) {
                return new WP_REST_Response(['ok' => true, 'error' => '', 'new' => 'nessuna traduzione da aggiornare'], 200);
            }
            $labels = [];
            foreach ($due as $k => $t) {
                $labels[$k] = tdpanop_translation_label($t);
            }
            $total = count($due);

            $msgs = [];
            $left = $due;
            $verified = false;
            $retried = false;
            clearstatcache();
            $start = time();
            // al massimo due giri: il secondo installa quello che WordPress propone dopo il
            // ricontrollo (pacchetto piu' nuovo arrivato nel frattempo)
            for ($round = 1; $round <= 2 && $left; $round++) {
                $updates = function_exists('wp_get_translation_updates') ? (array) wp_get_translation_updates() : [];
                if (!$updates) {
                    break;
                }
                $retried = $round > 1;
                $skin = new Automatic_Upgrader_Skin();
                $res  = (new Language_Pack_Upgrader($skin))->bulk_upgrade($updates);
                if (is_wp_error($res)) {
                    $msgs[] = $res->get_error_message();
                }
                if (method_exists($skin, 'get_upgrade_messages')) {
                    foreach ((array) $skin->get_upgrade_messages() as $m) {
                        $m = trim(wp_strip_all_tags((string) $m));
                        if ($m !== '' && !in_array($m, $msgs, true)) {
                            $msgs[] = $m;
                        }
                    }
                }
                $verified = tdpanop_refresh_update_data();
                if (!$verified) {
                    break;
                }
                $left = array_intersect_key(tdpanop_pending_translations(), $due);
            }

            // file scritti in questo aggiornamento
            clearstatcache();
            $written = [];
            foreach ($due as $k => $t) {
                $f = tdpanop_translation_file($t);
                $written[$k] = $f !== '' && file_exists($f) && @filemtime($f) >= $start - 2;
            }
            $why = $msgs ? implode(' · ', array_slice($msgs, -3)) : 'cartella wp-content/languages non scrivibile?';

            if (!$verified) {
                // wordpress.org non ha risposto al ricontrollo: si giudica dai file scritti
                $missing = array_keys(array_filter($written, function ($w) {
                    return !$w;
                }));
                if (!$missing) {
                    return new WP_REST_Response(['ok' => true, 'error' => '', 'new' => 'traduzioni aggiornate (' . $total . '/' . $total . '): '
                        . tdpanop_translation_names($labels) . ' — non ricontrollate, wordpress.org non ha risposto'], 200);
                }
                $names = array_intersect_key($labels, array_flip($missing));
                return new WP_REST_Response(['ok' => false, 'new' => '',
                    'error' => 'traduzioni non installate: ' . tdpanop_translation_names($names) . ' — ' . $why], 200);
            }

            $done = array_diff_key($labels, $left);
            if (!$left) {
                return new WP_REST_Response(['ok' => true, 'error' => '', 'new' => 'traduzioni aggiornate (' . $total . '/' . $total . '): '
                    . tdpanop_translation_names($labels) . ($retried ? ' (al secondo tentativo)' : '')], 200);
            }

            // ancora proposte dopo l'installazione: si dice quali e perche'
            $parts = [];
            foreach ($left as $k => $t) {
                $label = $labels[$k] ?? tdpanop_translation_label($t);
                if (empty($written[$k])) {
                    $parts[] = $label . ': file non scritto — ' . $why;
                    continue;
                }
                $inst  = tdpanop_translation_installed_ts($t);
                $offer = isset($t['updated']) ? strtotime((string) $t['updated']) : 0;
                $parts[] = $label . ': installata, ma WordPress la ripropone subito'
                    . ($inst ? ' (sul sito è del ' . gmdate('d/m/Y H:i', $inst) : ' (WordPress non la vede installata')
                    . ($offer ? ', wordpress.org la dà del ' . gmdate('d/m/Y H:i', $offer) . ')' : ')');
            }
            $head = $done ? 'traduzioni aggiornate ' . count($done) . ' di ' . $total . ' (' . tdpanop_translation_names($done) . '); ' : '';
            return new WP_REST_Response(['ok' => false, 'new' => '',
                'error' => $head . 'traduzioni non applicate: ' . implode('; ', array_slice($parts, 0, 5))
                    . (count($parts) > 5 ? '; e altre ' . (count($parts) - 5) : '')], 200);
        } elseif ($type === 'core') {
            require_once ABSPATH . 'wp-admin/includes/class-core-upgrader.php';
            $updates = get_core_updates();
            if (empty($updates) || !isset($updates[0]) || $updates[0]->response !== 'upgrade') {
                return new WP_REST_Response(['ok' => true, 'error' => '', 'new' => get_bloginfo('version')], 200);
            }
            $verBefore = get_bloginfo('version');
            $expected  = !empty($updates[0]->current) ? (string) $updates[0]->current : '';

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
            return new WP_REST_Response(['ok' => false, 'current' => $verBefore, 'new' => $expected,
                'error' => $emsg . ($lic !== '' ? ' (' . $lic . ')' : '')], 200);
        }
    }

    // errore esplicito dall'upgrader
    if (is_wp_error($res)) {
        $emsg = trim(wp_strip_all_tags($res->get_error_message())) . ' [' . $res->get_error_code() . ']';
        return new WP_REST_Response(['ok' => false, 'error' => $emsg, 'current' => $verBefore], 200);
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
    $req->set_param('backup', sanitize_text_field(wp_unslash((string) ($_POST['backup'] ?? '1'))));
    $resp = tdpanop_update($req);
    $data = ($resp instanceof WP_REST_Response) ? (array) $resp->get_data() : (array) $resp;
    $data['context'] = 'admin';
    wp_send_json($data, 200);
}
add_action('wp_ajax_nopriv_tdpanop_update', 'tdpanop_ajax_update');
add_action('wp_ajax_tdpanop_update', 'tdpanop_ajax_update');

/* Ricerca degli aggiornamenti nel contesto del BACKEND (2.41.3), per la modalita' firewall: i
 * plugin e i temi a licenza dicono se hanno una versione nuova solo quando il loro aggiornatore e'
 * caricato, cioe' nel backend. Stessa autenticazione di tdpanop_ajax_update. */
function tdpanop_ajax_status()
{
    $req = new WP_REST_Request('GET');
    foreach (['HTTP_AUTHORIZATION', 'REDIRECT_HTTP_AUTHORIZATION'] as $k) {
        if (!empty($_SERVER[$k])) {
            $req->set_header('authorization', (string) $_SERVER[$k]);
            break;
        }
    }
    if (!empty($_SERVER['HTTP_X_SENTINEL_TOKEN'])) {
        $req->set_header('x_sentinel_token', (string) $_SERVER['HTTP_X_SENTINEL_TOKEN']);
    }
    if (!tdpanop_auth($req)) {
        wp_send_json(['ok' => false, 'error' => 'non autorizzato'], 401);
    }
    $req->set_param('refresh', sanitize_text_field(wp_unslash((string) ($_POST['refresh'] ?? '1'))));
    $auto = sanitize_text_field(wp_unslash((string) ($_POST['wp_auto_updates'] ?? '')));
    if ($auto !== '') {
        $req->set_param('wp_auto_updates', $auto);
    }
    $resp = tdpanop_status($req);
    $data = ($resp instanceof WP_REST_Response) ? (array) $resp->get_data() : (array) $resp;
    $data['context'] = 'admin';
    wp_send_json($data, 200);
}
add_action('wp_ajax_nopriv_tdpanop_status', 'tdpanop_ajax_status');
add_action('wp_ajax_tdpanop_status', 'tdpanop_ajax_status');

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
 * Core e traduzioni: ricostruzione completa (tdpanop_refresh_update_data).
 */
function tdpanop_prepare_update_data($type, $slug, $force = false)
{
    tdpanop_wake_premium_updaters();
    if ($type !== 'plugin' && $type !== 'theme') {
        tdpanop_refresh_update_data();
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
            // il pacchetto e' il connettore stesso? Mai una seconda copia accanto a quella in uso
            $conflict = tdpanop_self_package_conflict($tmp);
            if ($conflict !== '') {
                return new WP_REST_Response(['ok' => false, 'error' => $conflict], 200);
            }
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

/* ---------------------------------------------------------------------------
 * Dove gira questa copia del connettore, e guardia contro la seconda copia.
 * ------------------------------------------------------------------------- */
function tdpanop_running_mode(): array
{
    $norm = function ($p) {
        return rtrim(str_replace('\\', '/', (string) $p), '/') . '/';
    };
    // __FILE__ ha i collegamenti simbolici risolti: si confronta con la cartella com'e' scritta
    // e con quella risolta, cosi' una wp-content/plugins collegata non sembra "altrove"
    $under = function ($dir) use ($norm) {
        $file = str_replace('\\', '/', __FILE__);
        foreach (array_unique([$norm($dir), $norm(@realpath($dir) ?: $dir)]) as $base) {
            if (strpos($file, $base) === 0) {
                return substr($file, strlen($base));
            }
        }
        return null;
    };
    if (defined('WPMU_PLUGIN_DIR') && $under(WPMU_PLUGIN_DIR) !== null) {
        return ['mode' => 'mu', 'folder' => ''];
    }
    $rel = null;
    if (function_exists('plugin_basename')) {
        // come la vede WordPress (gestisce anche le cartelle dei plugin collegate)
        // (fuori dalle cartelle dei plugin plugin_basename ridà il percorso intero: si accetta
        // solo se il file c'e' davvero sotto WP_PLUGIN_DIR)
        $base = str_replace('\\', '/', plugin_basename(__FILE__));
        if ($base !== '' && defined('WP_PLUGIN_DIR') && is_file(rtrim(WP_PLUGIN_DIR, '/\\') . '/' . $base)) {
            $rel = $base;
        }
    }
    if ($rel === null && defined('WP_PLUGIN_DIR')) {
        $rel = $under(WP_PLUGIN_DIR);
    }
    if ($rel !== null) {
        $cut = strpos($rel, '/');
        return ['mode' => 'plugin', 'folder' => $cut === false ? '' : substr($rel, 0, $cut)];
    }
    return ['mode' => 'other', 'folder' => ''];
}

/*
 * Se lo zip da installare e' il connettore stesso, puo' solo SOSTITUIRE una copia che c'e' gia'
 * (stessa cartella): mai crearne una nuova. Una copia in piu' (accanto a un mu-plugin o in
 * un'altra cartella) non farebbe niente e il pannello vedrebbe sempre la versione vecchia.
 * Sostituire una copia esistente invece va sempre bene, anche se non e' quella in uso: diventa
 * la versione nuova, che quando trova un'altra copia gia' caricata esce in silenzio.
 * Torna il motivo del rifiuto, o '' se si puo' installare.
 */
function tdpanop_self_package_conflict(string $zip): string
{
    if (!class_exists('ZipArchive')) {
        return '';
    }
    $za = new ZipArchive();
    if ($za->open($zip) !== true) {
        return '';
    }
    $root = null;
    for ($i = 0; $i < $za->numFiles; $i++) {
        $name = (string) $za->getNameIndex($i);
        if (preg_match('#^(?:([^/]+)/)?td-panopticon\.php$#', $name, $m)) {
            $head = (string) $za->getFromIndex($i, 8192);
            if (preg_match('/^[\s*]*Plugin Name:\s*Sentinel TD Agent\s*$/mi', $head) || strpos($head, 'TDPANOP_LOADED') !== false) {
                $root = isset($m[1]) ? (string) $m[1] : '';
                break;
            }
        }
    }
    $za->close();
    if ($root === null) {
        return '';
    }
    if ($root === '') {
        // senza cartella WordPress lo metterebbe in una cartella nuova: sarebbe una copia in piu'
        return 'Pacchetto del connettore senza cartella: non si installa una copia nuova';
    }
    if (defined('WP_PLUGIN_DIR') && is_file(rtrim(WP_PLUGIN_DIR, '/\\') . '/' . $root . '/td-panopticon.php')) {
        return '';     // sostituisce una copia che c'e' gia'
    }
    $me = tdpanop_running_mode();
    if ($me['mode'] === 'mu') {
        return 'Il connettore su questo sito gira come mu-plugin: non si installa una copia nuova nei plugin';
    }
    return 'Il connettore su questo sito non sta in "' . $root . '": non si installa una copia nuova';
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
            // non rimuovere il connettore stesso: ne' la cartella in cui gira, ne' le altre sue copie
            $me = tdpanop_running_mode();
            if (dirname($file) === 'td-panopticon' || dirname($file) === 'sentinel-td' || strpos($file, 'td-panopticon') === 0
                || ($me['folder'] !== '' && dirname($file) === $me['folder'])) {
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


/* ---------------------------------------------------------------------------
 * Modalita' firewall (2.41.0): quando un firewall o un antibot davanti al sito (Cloudflare,
 * SiteGround, Sucuri, un IP bannato...) blocca Sentinel TD, e' il connettore a chiamarlo.
 * Il firewall filtra chi ENTRA nel sito, non quello che il sito manda fuori.
 *
 * - Fuori dalla modalita': se Sentinel non cerca il sito da mezz'ora, un ping leggero al
 *   pannello; la risposta dice in che modalita' stare. Niente altro.
 * - In modalita' firewall (la decide il pannello quando trova il firewall): ogni 5 minuti lo
 *   stato del sito al pannello, poi i lavori in coda (aggiornamenti, ripristini, installazioni,
 *   rimozioni) eseguiti con le stesse funzioni delle chiamate REST, e gli esiti.
 * - L'indirizzo del pannello lo impara dalle chiamate autenticate di Sentinel (X-Sentinel-Hub);
 *   in mancanza usa quello del pacchetto o della pagina Impostazioni → Sentinel TD.
 * Il giro parte dal cron di WordPress e, se il cron non riesce a partire (la richiesta che
 * WordPress fa a se stesso puo' fermarsi proprio sul firewall), dopo una visita al sito, a
 * pagina gia' consegnata.
 * ------------------------------------------------------------------------- */
define('TDPANOP_RELAY_STATE', 'tdpanop_relay_state');   // modalita' e tempi
define('TDPANOP_RELAY_HUB', 'tdpanop_relay_hub');       // indirizzo del pannello imparato da X-Sentinel-Hub
define('TDPANOP_RELAY_SEEN', 'tdpanop_relay_contact');  // ultima chiamata autenticata di Sentinel
define('TDPANOP_RELAY_LOCK', 'tdpanop_relay_lock');     // un giro alla volta

function tdpanop_relay_clean_hub(string $url): string
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

/* Sentinel ha chiamato il connettore (token valido): se ne prende nota, e dell'indirizzo del pannello. */
function tdpanop_relay_note_contact($req): void
{
    try {
        $now = time();
        if ($now - (int) get_option(TDPANOP_RELAY_SEEN, 0) >= 60) {
            update_option(TDPANOP_RELAY_SEEN, $now, true);
        }
        $hub = tdpanop_relay_clean_hub((string) $req->get_header('x_sentinel_hub'));
        if ($hub !== '' && $hub !== (string) get_option(TDPANOP_RELAY_HUB, '')) {
            update_option(TDPANOP_RELAY_HUB, $hub, true);
        }
    } catch (\Throwable $e) {
        // mai bloccare una chiamata del pannello per questo
    }
}

function tdpanop_relay_hub(): string
{
    $learned = tdpanop_relay_clean_hub((string) get_option(TDPANOP_RELAY_HUB, ''));
    if ($learned !== '') {
        return $learned;
    }
    if (TDPANOP_HUB_URL !== '') {
        return tdpanop_relay_clean_hub(TDPANOP_HUB_URL);
    }
    return tdpanop_relay_clean_hub((string) get_option('td_panopticon_hub_url', ''));
}

function tdpanop_relay_state(): array
{
    $st = get_option(TDPANOP_RELAY_STATE, []);
    // status_every 0 = stato completo a ogni giro (pannello che non dice ogni quanto)
    return array_merge(['mode' => 'direct', 'tick' => 300, 'every' => 1800, 'ping_at' => 0, 'tick_at' => 0,
        'status_every' => 0, 'status_at' => 0], is_array($st) ? $st : []);
}

function tdpanop_relay_save(array $st): void
{
    // opzioni piccole e caricate con le altre: il controllo a fine pagina non fa query
    update_option(TDPANOP_RELAY_STATE, $st, true);
}

/* Cron di WordPress: ogni 5 minuti in modalita' firewall, ogni 15 fuori (basta per il ping
 * della mezz'ora, senza una richiesta di WordPress a se stesso ogni 5 minuti su tutti i siti). */
function tdpanop_relay_schedule(string $mode): void
{
    if (!function_exists('wp_next_scheduled') || !function_exists('wp_schedule_event')) {
        return;
    }
    $want = $mode === 'relay' ? 'tdpanop_5min' : 'tdpanop_15min';
    $ev = function_exists('wp_get_scheduled_event') ? wp_get_scheduled_event('tdpanop_relay_tick') : null;
    if ($ev && isset($ev->schedule) && $ev->schedule === $want) {
        return;
    }
    if (function_exists('wp_clear_scheduled_hook')) {
        wp_clear_scheduled_hook('tdpanop_relay_tick');
    }
    wp_schedule_event(time() + 60, $want, 'tdpanop_relay_tick');
}

/* La risposta del pannello: modalita' e tempi (con limiti, qualunque cosa arrivi). */
function tdpanop_relay_apply(array $st, array $d): array
{
    $was = $st['mode'];
    $st['mode']  = (($d['mode'] ?? '') === 'relay') ? 'relay' : 'direct';
    $st['tick']  = max(60, min(3600, (int) ($d['tick'] ?? 300)));
    $st['every'] = max(300, min(86400, (int) ($d['ping'] ?? 1800)));
    $st['status_every'] = max(0, min(86400, (int) ($d['status_every'] ?? 0)));
    if ($was !== 'relay' && $st['mode'] === 'relay') {
        $st['status_at'] = 0;      // appena in modalita' firewall: lo stato completo subito
    }
    tdpanop_relay_save($st);
    if ($was !== $st['mode']) {
        tdpanop_relay_schedule($st['mode']);
    }
    return $st;
}

/* Il pannello non vuole piu' sentire questa copia (sito tolto o disattivato, token cambiato,
 * un'altra copia del sito gia' collegata): si torna al solo ping della mezz'ora. */
function tdpanop_relay_refused(array $st, $d, int $now): array
{
    if (is_array($d) && in_array((int) ($d['_code'] ?? 0), [401, 403, 409], true)) {
        $st['mode'] = 'direct';
        $st['ping_at'] = $now;
        tdpanop_relay_save($st);
        tdpanop_relay_schedule('direct');
    }
    return $st;
}

/* Impronta di questa installazione (cartella del sito): una copia con lo stesso token e lo
 * stesso indirizzo (vecchio server dopo un trasloco, copia locale) non parla al posto del sito. */
function tdpanop_relay_fp(): string
{
    return substr(sha1('wp|' . (defined('ABSPATH') ? ABSPATH : __DIR__)), 0, 16);
}

/* E' ora di farsi sentire? $grace: secondi di ritardo tollerati (il giro dopo una visita
 * aspetta che il cron abbia avuto la sua occasione). */
function tdpanop_relay_due(int $now, int $grace = 0): bool
{
    if (tdpanop_relay_hub() === '') {
        return false;
    }
    $st = tdpanop_relay_state();
    // un po' di tolleranza: il cron di WordPress parte con qualche secondo di ritardo variabile,
    // e senza si salterebbe un giro su due (10 minuti invece di 5)
    if ($st['mode'] === 'relay') {
        return $now - (int) $st['tick_at'] >= (int) $st['tick'] - 30 + $grace;
    }
    $contact = (int) get_option(TDPANOP_RELAY_SEEN, 0);
    return $now - $contact >= (int) $st['every'] + $grace && $now - (int) $st['ping_at'] >= (int) $st['every'] - 60 + $grace;
}

/* Un giro alla volta. Con MySQL: GET_LOCK, atomico e rilasciato da solo se il processo muore; il
 * nome vale per tutto il server MySQL, quindi dentro c'e' questo sito (database, prefisso, cartella).
 * Senza (test): un'option col momento d'inizio, superata dopo 15 minuti. */
function tdpanop_relay_lock_name(): string
{
    global $wpdb;
    $db = defined('DB_NAME') ? DB_NAME : '';
    $prefix = (isset($wpdb) && is_object($wpdb) && isset($wpdb->prefix)) ? $wpdb->prefix : '';
    return 'tdpanop_relay_' . substr(md5($db . '|' . $prefix . '|' . (defined('ABSPATH') ? ABSPATH : '')), 0, 20);
}

function tdpanop_relay_lock(int $now): bool
{
    global $wpdb;
    if (isset($wpdb) && is_object($wpdb) && method_exists($wpdb, 'get_var')) {
        return (string) $wpdb->get_var("SELECT GET_LOCK('" . tdpanop_relay_lock_name() . "', 0)") === '1';
    }
    if (add_option(TDPANOP_RELAY_LOCK, $now, '', 'no')) {
        return true;
    }
    if ($now - (int) get_option(TDPANOP_RELAY_LOCK, 0) > 900) {   // giro precedente morto a meta'
        update_option(TDPANOP_RELAY_LOCK, $now, false);
        return true;
    }
    return false;
}

function tdpanop_relay_unlock(): void
{
    global $wpdb;
    if (isset($wpdb) && is_object($wpdb) && method_exists($wpdb, 'get_var')) {
        $wpdb->get_var("SELECT RELEASE_LOCK('" . tdpanop_relay_lock_name() . "')");
        return;
    }
    delete_option(TDPANOP_RELAY_LOCK);
}

function tdpanop_relay_post(string $hub, array $body, int $timeout)
{
    $r = wp_remote_post($hub . '/api/agent/relay', [
        'timeout' => $timeout,
        'headers' => [
            'Content-Type'     => 'application/json',
            'Accept'           => 'application/json',
            'X-Sentinel-Token' => tdpanop_token(),
            'User-Agent'       => 'SentinelTD-Agent/' . TDPANOP_VERSION . '; ' . home_url('/'),
        ],
        'body'    => wp_json_encode($body),
        'redirection' => 0,     // il token non segue un redirect verso un altro indirizzo
    ]);
    if (is_wp_error($r)) {
        return ['_error' => $r->get_error_message()];
    }
    $code = (int) wp_remote_retrieve_response_code($r);
    $d = json_decode((string) wp_remote_retrieve_body($r), true);
    if ($code !== 200 || !is_array($d)) {
        return ['_error' => 'HTTP ' . $code, '_code' => $code];
    }
    return $d;
}

function tdpanop_relay_status(bool $refresh, $auto): array
{
    if ($refresh) {
        // ricerca degli aggiornamenti dal backend: anche i plugin a licenza rispondono
        $d = tdpanop_relay_admin_call('tdpanop_status', ['refresh' => '1', 'wp_auto_updates' => $auto === null ? '' : (string) $auto]);
        if (is_array($d) && isset($d['extensions']) && is_array($d['extensions'])) {
            return $d;
        }
    }
    $req = new WP_REST_Request('GET', '/tdpanopticon/v1/status');
    if ($refresh) {
        $req->set_param('refresh', '1');
    }
    if ($auto !== null) {
        $req->set_param('wp_auto_updates', (string) $auto);
    }
    $r = tdpanop_status($req);
    return ($r instanceof WP_REST_Response) ? (array) $r->get_data() : (array) $r;
}

/* Zip di un lavoro di installazione, scaricato dal pannello col token del sito. */
function tdpanop_relay_download(string $hub, string $path)
{
    if (strpos($path, '/api/agent/relay/file/') !== 0) {
        return new WP_Error('relay', 'indirizzo del pacchetto non valido');
    }
    require_once ABSPATH . 'wp-admin/includes/file.php';
    $tmp = wp_tempnam('tdpanop-relay.zip');
    $r = wp_remote_get($hub . $path, [
        'timeout'  => 300,
        'stream'   => true,
        'filename' => $tmp,
        'redirection' => 0,
        'headers'  => ['X-Sentinel-Token' => tdpanop_token()],
    ]);
    if (is_wp_error($r) || (int) wp_remote_retrieve_response_code($r) !== 200) {
        @unlink($tmp);
        return new WP_Error('relay', 'pacchetto non scaricato da Sentinel TD' . (is_wp_error($r) ? ': ' . $r->get_error_message() : ''));
    }
    return $tmp;
}

/*
 * Aggiornamento nel BACKEND del sito, in modalita' firewall (2.41.1).
 * I plugin e i temi a licenza (Elementor Pro, ACF Pro, Gravity Forms, WP Rocket, YOOtheme…)
 * spesso caricano il proprio aggiornatore solo nel backend: per questo, senza firewall, Sentinel
 * li aggiorna da admin-ajax.php. In modalita' firewall Sentinel non arriva al sito, e il giro gira
 * nel cron o dopo una visita, che backend non sono: il file di quei prodotti non arrivava.
 * Qui il sito chiama il proprio admin-ajax.php, puntando direttamente alla macchina su cui gira
 * (127.0.0.1, poi l'indirizzo del server): la richiesta non esce dal server e non passa dal
 * firewall. Stesso token, stessa funzione che Sentinel usa senza firewall. Se il server non
 * risponde cosi', null: si aggiorna come prima, dentro il giro.
 */
function tdpanop_relay_admin_update(array $job)
{
    $body = [];
    foreach (['type', 'slug', 'expected', 'backup'] as $k) {
        $body[$k] = isset($job[$k]) && is_scalar($job[$k]) ? (string) $job[$k] : '';
    }
    // "type non valido": richiesta arrivata senza i dati, si prova l'indirizzo successivo
    return tdpanop_relay_admin_call('tdpanop_update', $body, function ($d) {
        return !(empty($d['ok']) && strpos((string) ($d['error'] ?? ''), 'type non valido') === 0);
    });
}

/* Chiamata al proprio admin-ajax.php puntando alla macchina stessa (vedi sopra). Torna la
 * risposta JSON del connettore, o null se il server non risponde cosi'. */
function tdpanop_relay_admin_call(string $action, array $body, $accept = null)
{
    $url = admin_url('admin-ajax.php');
    $host = (string) parse_url($url, PHP_URL_HOST);
    if ($host === '') {
        return null;
    }
    $ips = ['127.0.0.1'];
    $own = isset($_SERVER['SERVER_ADDR']) ? (string) $_SERVER['SERVER_ADDR'] : '';
    if ($own !== '' && filter_var($own, FILTER_VALIDATE_IP) && !in_array($own, $ips, true)) {
        $ips[] = $own;
    }
    $token = tdpanop_token();
    foreach ($ips as $ip) {
        $pin = function ($handle, $args, $req_url) use ($host, $ip, $action) {
            if (strpos((string) $req_url, $action) !== false && defined('CURLOPT_RESOLVE')) {
                $ipv = strpos($ip, ':') !== false ? '[' . $ip . ']' : $ip;
                curl_setopt($handle, CURLOPT_RESOLVE, [$host . ':443:' . $ipv, $host . ':80:' . $ipv]);
            }
        };
        add_action('http_api_curl', $pin, 10, 3);
        try {
            $r = wp_remote_post(add_query_arg(['action' => $action, '_' => time()], $url), [
                'timeout'     => 280,
                'redirection' => 0,
                // stessa macchina, indirizzo fissato: il certificato puo' essere quello d'origine
                // (Cloudflare) e non serve verificarlo, come fa WordPress con le sue richieste a se stesso
                'sslverify'   => false,
                'headers'     => ['X-Sentinel-Token' => $token, 'Authorization' => 'Bearer ' . $token],
                'body'        => $body,
            ]);
        } finally {
            remove_action('http_api_curl', $pin, 10);
        }
        if (is_wp_error($r) || (int) wp_remote_retrieve_response_code($r) !== 200) {
            continue;
        }
        $d = json_decode((string) wp_remote_retrieve_body($r), true);
        if (!is_array($d) || !(array_key_exists('ok', $d) || array_key_exists('extensions', $d))) {
            continue;          // risposta che non e' del connettore (pagina del server, "0")
        }
        if ($accept !== null && !$accept($d)) {
            continue;
        }
        return $d;
    }
    return null;
}

/* Un lavoro del pannello, con le stesse funzioni delle chiamate REST. $deps (per i test):
 * admin (aggiornamento nel backend), update (aggiornamento dentro il giro). */
function tdpanop_relay_run(array $job, string $hub, array $deps = []): array
{
    try {
        $action = (string) ($job['action'] ?? '');
        $req = new WP_REST_Request('POST');
        foreach (['type', 'slug', 'expected', 'backup', 'file', 'kind', 'activate'] as $k) {
            if (isset($job[$k]) && is_scalar($job[$k])) {
                $req->set_param($k, (string) $job[$k]);
            }
        }
        $tmp = '';
        if ($action === 'update') {
            $r = null;
            if (in_array((string) ($job['type'] ?? ''), ['plugin', 'theme'], true)) {
                // prima nel backend (plugin e temi a licenza), poi dentro il giro come prima
                $r = ($deps['admin'] ?? 'tdpanop_relay_admin_update')($job);
            }
            if ($r === null) {
                $r = ($deps['update'] ?? 'tdpanop_update')($req);
            }
        } elseif ($action === 'rollback') {
            $r = tdpanop_rollback($req);
        } elseif ($action === 'uninstall') {
            $r = tdpanop_uninstall($req);
        } elseif ($action === 'install') {
            $tmp = tdpanop_relay_download($hub, (string) ($job['download'] ?? ''));
            if (is_wp_error($tmp)) {
                return ['ok' => false, 'error' => $tmp->get_error_message()];
            }
            $name = basename((string) ($job['filename'] ?? 'package.zip'));
            $req->set_file_params(['package' => ['name' => $name, 'tmp_name' => $tmp, 'error' => 0, 'size' => (int) @filesize($tmp)]]);
            $r = tdpanop_install($req);
        } else {
            return ['ok' => false, 'error' => 'lavoro sconosciuto: ' . $action];
        }
        if ($tmp !== '') {
            @unlink($tmp);
        }
        if (is_wp_error($r)) {
            return ['ok' => false, 'error' => $r->get_error_message()];
        }
        return ($r instanceof WP_REST_Response) ? (array) $r->get_data() : (array) $r;
    } catch (\Throwable $e) {
        return ['ok' => false, 'error' => $e->getMessage()];
    }
}

/* Esito di un giro, a parole (pagina Impostazioni → Sentinel TD). */
function tdpanop_relay_outcome_text(string $out): string
{
    $head = explode(':', $out, 2);
    $tail = isset($head[1]) ? trim($head[1]) : '';
    switch ($head[0]) {
        case 'ping':
            return tdpanop__('chiamata riuscita: Sentinel TD raggiunge il sito direttamente');
        case 'report':
            return $tail !== '' ? sprintf(tdpanop__('modalità firewall: stato mandato, lavori eseguiti: %s'), $tail)
                : tdpanop__('modalità firewall: stato mandato a Sentinel TD');
        case 'ping-failed':
        case 'report-failed':
            return sprintf(tdpanop__('chiamata non riuscita: %s'), $tail !== '' ? $tail : '?');
        case 'no-hub':
            return tdpanop__('indirizzo di Sentinel TD mancante: collega il sito o installa il connettore scaricato dal pannello');
        case 'busy':
            return tdpanop__('una chiamata a Sentinel TD è già in corso, riprova fra un minuto');
        default:
            return $out;
    }
}

/**
 * Il giro: ping o, in modalita' firewall, stato + lavori + esiti. Torna cosa ha fatto.
 * $deps (per i test): post, status, run, now, budget.
 */
function tdpanop_relay_tick(array $deps = []): string
{
    $post   = $deps['post'] ?? 'tdpanop_relay_post';
    $status = $deps['status'] ?? 'tdpanop_relay_status';
    $run    = $deps['run'] ?? 'tdpanop_relay_run';
    $now    = (int) ($deps['now'] ?? time());
    // un lavoro nuovo comincia solo entro questo tempo: ogni lavoro ha poi i suoi 5 minuti
    $budget = (float) ($deps['budget'] ?? 150);
    $force  = !empty($deps['force']);      // pulsante "Chiama Sentinel TD ora"

    if ($force && tdpanop_relay_hub() === '') {
        return 'no-hub';
    }
    if (!$force && !tdpanop_relay_due($now)) {
        return 'wait';
    }
    if (!tdpanop_relay_lock($now)) {
        return 'busy';
    }
    if (!empty($deps['status_now'])) {
        $st = tdpanop_relay_state();
        $st['status_at'] = 0;
        tdpanop_relay_save($st);
    }
    if (!empty($deps['nojobs'])) {
        $budget = -1;
    }
    $out = tdpanop_relay_round($post, $status, $run, $now, $budget);
    // l'esito resta per la pagina Impostazioni → Sentinel TD
    $st = tdpanop_relay_state();
    $st['last'] = $out;
    $st['last_at'] = $now;
    tdpanop_relay_save($st);
    return $out;
}

function tdpanop_relay_round($post, $status, $run, int $now, float $budget): string
{
    $hub  = tdpanop_relay_hub();
    $base = ['url' => home_url('/'), 'connector' => TDPANOP_VERSION, 'cms' => 'wp', 'fp' => tdpanop_relay_fp()];
    try {
        ignore_user_abort(true);
        $st = tdpanop_relay_state();
        if ($st['mode'] !== 'relay') {
            $st['ping_at'] = $now;
            tdpanop_relay_save($st);
            $d = $post($hub, $base + ['kind' => 'ping'], 15);
            if (!is_array($d) || isset($d['_error'])) {
                return 'ping-failed: ' . (is_array($d) ? (string) $d['_error'] : '?');
            }
            $st = tdpanop_relay_apply($st, $d);
            if ($st['mode'] !== 'relay') {
                return 'ping';
            }
        }
        $st['tick_at'] = $now;
        tdpanop_relay_save($st);

        $start   = microtime(true);
        $results = [];
        $refresh = false;
        $auto    = null;
        $wait    = false;
        $done    = 0;
        // stato completo solo ogni status_every secondi (Impostazioni del pannello), su richiesta di
        // ricalcolo e dopo un lavoro; negli altri giri la sola domanda "ci sono lavori?", leggera
        $needStatus = ($now - (int) $st['status_at']) >= (int) $st['status_every'] - 30;
        $sentStatus = false;
        $searched = null;      // una sola ricerca degli aggiornamenti per giro
        for ($round = 0; $round < 50; $round++) {
            $over = (microtime(true) - $start) > $budget;
            $body = $base + [
                'kind'    => 'report',
                'jobs_ok' => true,
                'results' => $results,
                'wait'    => $wait && !$over,
                'nojobs'  => $over,
            ];
            if ($needStatus || $refresh) {
                $body['status'] = $status($refresh, $auto);
                $st['status_at'] = $now;
                $needStatus = false;
                $sentStatus = true;
            }
            $d = $post($hub, $body, $wait ? 60 : 30);
            if (!is_array($d) || isset($d['_error'])) {
                tdpanop_relay_refused($st, $d, $now);
                return 'report-failed: ' . (is_array($d) ? (string) $d['_error'] : '?');
            }
            $results = [];
            $refresh = false;
            $st = tdpanop_relay_apply($st, $d);
            if (isset($d['wp_auto_updates'])) {
                $auto = (string) $d['wp_auto_updates'];
            }
            $jobs = (isset($d['jobs']) && is_array($d['jobs'])) ? $d['jobs'] : [];
            if (!$jobs) {
                if (!empty($d['need_status']) && !$sentStatus) {
                    // il pannello non ha uno stato recente: un altro giro, con lo stato
                    $needStatus = true;
                    continue;
                }
                break;
            }
            foreach ($jobs as $job) {
                if (!is_array($job) || empty($job['id'])) {
                    continue;
                }
                $id = (string) $job['id'];
                if ((microtime(true) - $start) > $budget) {
                    // tempo finito prima di cominciarlo: torna in coda per il prossimo giro
                    $results[] = ['id' => $id, 'result' => ['_requeue' => true]];
                    continue;
                }
                if (($job['action'] ?? '') === 'refresh') {
                    // ricerca degli aggiornamenti adesso: lo stato fresco va con l'esito (il ciclo
                    // degli aggiornamenti lo aspetta per decidere cosa aggiornare). Una sola per giro.
                    if ($searched === null) {
                        $searched = $status(true, $auto);
                    }
                    $results[] = ['id' => $id, 'result' => ['ok' => true, 'status' => $searched]];
                    $st['status_at'] = $now;
                    $sentStatus = true;
                    continue;
                }
                if ((microtime(true) - $start) > $budget) {
                    // tempo finito prima di cominciarlo: torna in coda per il prossimo giro
                    $results[] = ['id' => $id, 'result' => ['_requeue' => true]];
                    continue;
                }
                if (function_exists('set_time_limit')) {
                    @set_time_limit(300);   // ogni lavoro ha i suoi 5 minuti
                }
                $results[] = ['id' => $id, 'result' => $run($job, $hub)];
                $done++;
                $needStatus = true;    // dopo un lavoro, lo stato nuovo
            }
            $wait = true;
        }
        return 'report' . ($done ? ':' . $done : '');
    } finally {
        tdpanop_relay_unlock();
    }
}

add_filter('cron_schedules', function ($s) {
    // in modalita' firewall: ogni quanto il pannello vuole sentire il sito (Impostazioni)
    $tdpanop_st = get_option(TDPANOP_RELAY_STATE, []);
    $tdpanop_tick = is_array($tdpanop_st) ? (int) ($tdpanop_st['tick'] ?? 300) : 300;
    $s['tdpanop_5min'] = ['interval' => max(60, min(3600, $tdpanop_tick)), 'display' => 'Sentinel TD (modalità firewall)'];
    $s['tdpanop_15min'] = ['interval' => 900, 'display' => 'Sentinel TD (15 min)'];
    return $s;
});
add_action('tdpanop_relay_tick', function () {
    try {
        tdpanop_relay_tick();
    } catch (\Throwable $e) {
        // mai rompere il cron del sito
    }
});
add_action('init', function () {
    if (function_exists('wp_next_scheduled') && !wp_next_scheduled('tdpanop_relay_tick')) {
        $st = tdpanop_relay_state();
        wp_schedule_event(time() + 60, $st['mode'] === 'relay' ? 'tdpanop_5min' : 'tdpanop_15min', 'tdpanop_relay_tick');
    }
});
// Se il cron non parte (la richiesta che WordPress fa a se stesso si ferma sul firewall), il giro
// parte dopo una visita, a pagina gia' consegnata al visitatore: solo dove PHP sa chiudere la
// risposta prima (PHP-FPM, LiteSpeed) e solo se il giro e' in ritardo di due minuti.
add_action('shutdown', function () {
    if ((function_exists('wp_doing_cron') && wp_doing_cron()) || (function_exists('wp_doing_ajax') && wp_doing_ajax())
        || (defined('REST_REQUEST') && REST_REQUEST) || (defined('WP_CLI') && WP_CLI)) {
        return;
    }
    if (!function_exists('fastcgi_finish_request') && !function_exists('litespeed_finish_request')) {
        return;
    }
    try {
        if (!tdpanop_relay_due(time(), 120)) {
            return;
        }
        function_exists('fastcgi_finish_request') ? fastcgi_finish_request() : litespeed_finish_request();
        tdpanop_relay_tick();
    } catch (\Throwable $e) {
        // mai rompere una pagina per questo
    }
}, 999);
if (function_exists('register_deactivation_hook')) {
    register_deactivation_hook(__FILE__, function () {
        if (function_exists('wp_clear_scheduled_hook')) {
            wp_clear_scheduled_hook('tdpanop_relay_tick');
        }
    });
}

} // fine blocco condizionale (vedi sopra)

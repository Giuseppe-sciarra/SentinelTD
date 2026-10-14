"""Traduzioni WordPress non applicate: nelle notifiche sono in giallo ("da riprovare"), mai
"fallito". Prima arrivavano come "❌ 1 fallito su 1" anche quando bastava aspettare che
wordpress.org pubblicasse il pacchetto nuovo su tutti i suoi server."""
import unittest

import test_screenshot_schedule  # noqa: F401  (percorso di app e variabili d'ambiente)
from app import notify

RETRY = {"name": "Traduzioni (1)", "from": "", "to": "", "ok": False, "retry": True,
         "error": "traduzioni non applicate: Elementor (en_GB): installata, ma WordPress la ripropone subito"}
OK = {"name": "Yoast SEO", "from": "28.6", "to": "28.7", "ok": True, "error": ""}
BAD = {"name": "Rank Math", "from": "1.0.270", "to": "", "ok": False, "error": "download fallito"}


class SiteReportTests(unittest.TestCase):
    def render(self, results):
        return notify.enrich("site_report", {"site_name": "Albergo", "site_url": "https://a.test", "cms": "wp",
                                             "results": results}, escape=True, language="it")

    def test_translations_alone_are_yellow_not_failed(self):
        out = self.render([RETRY])
        self.assertEqual((out["outcome_icon"], out["outcome_text"]), ("⏸", "0 aggiornati, 1 da riprovare"))
        self.assertEqual((out["failed_count"], out["retry_count"]), (0, 1))
        self.assertIn("da riprovare", out["report_table"])
        self.assertNotIn("fallito", out["report_table"])
        self.assertIn("#b26a00", out["report_table"])          # giallo, non il rosso dei falliti
        self.assertIn("⏸ Traduzioni (1) — da riprovare", out["updates_lines"])

    def test_with_updates_the_site_is_not_failed(self):
        out = self.render([OK, RETRY])
        self.assertEqual((out["outcome_icon"], out["outcome_text"]), ("⏸", "1 aggiornato, 1 da riprovare"))

    def test_a_real_failure_stays_red(self):
        out = self.render([BAD, RETRY])
        self.assertEqual(out["outcome_icon"], "❌")
        self.assertEqual(out["failed_count"], 1)


class CycleSummaryTests(unittest.TestCase):
    def test_cycle_counts_translations_to_retry_apart(self):
        rep = [{"site": "Albergo", "url": "https://a.test", "ok": [{"name": "Yoast SEO", "from": "28.6", "to": "28.7"}],
                "failed": [], "manual": [], "held": [], "retry": [{"name": "Traduzioni (1)", "error": RETRY["error"]}]}]
        out = notify.enrich("cycle_summary", {"report": rep, "when": "15/10 00:13"}, language="it")
        self.assertTrue(out["headline"].startswith("⚠️"))
        self.assertNotIn("fallit", out["headline"])
        self.assertIn("⏸ 1 da riprovare", out["summary"])
        self.assertIn("Traduzioni (1) da riprovare", out["summary"])
        self.assertIn("⏸ 1 da riprovare", out["summary_email"])
        self.assertNotIn("❌", out["summary_email"])


if __name__ == "__main__":
    unittest.main()

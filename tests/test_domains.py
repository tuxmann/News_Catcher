"""Tests for domain allowlist matching."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from domains_store import (
    add_bad_domain,
    bad_domain_refusal_message,
    host_allowed,
    host_is_bad,
    load_bad_domains,
    load_domains,
    save_domains,
)


class TestHostAllowed(unittest.TestCase):
    def test_subdomains_match_base(self) -> None:
        allowed = {"economist.com", "reuters.com"}
        self.assertTrue(host_allowed("www.economist.com", allowed))
        self.assertTrue(host_allowed("economist.com", allowed))
        self.assertTrue(host_allowed("graphics.reuters.com", allowed))
        self.assertFalse(host_allowed("evil.com", allowed))
        self.assertFalse(host_allowed("notreuters.com", allowed))


class TestBadDomains(unittest.TestCase):
    def test_add_bad_domain_moves_off_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "domains.json"
            bad = Path(tmp) / "domains_bad.json"
            save_domains(good, ["example.com", "other.com"])
            add_bad_domain(bad, good, "example.com")
            self.assertEqual(load_domains(good), {"other.com"})
            self.assertEqual(load_bad_domains(bad), {"example.com"})

    def test_host_is_bad_matches_subdomains(self) -> None:
        bad = {"broken.com"}
        self.assertTrue(host_is_bad("www.broken.com", bad))
        self.assertFalse(host_is_bad("working.com", bad))

    def test_bad_domain_refusal_mentions_temporary_override(self) -> None:
        bot_msg = bad_domain_refusal_message("phys.org", for_bot=True)
        self.assertIn("phys.org", bot_msg)
        self.assertIn("/override_bad_domain", bot_msg)
        self.assertIn("temporarily override", bot_msg.casefold())
        self.assertIn("/remove_bad_domain", bot_msg)

        gui_msg = bad_domain_refusal_message("phys.org", for_bot=False)
        self.assertIn("temporarily override", gui_msg.casefold())
        self.assertIn("domains_bad.json", gui_msg)
        self.assertNotIn("/override_bad_domain", gui_msg)


if __name__ == "__main__":
    unittest.main()

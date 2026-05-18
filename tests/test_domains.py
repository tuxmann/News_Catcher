"""Tests for domain allowlist matching."""

from __future__ import annotations

import unittest

from domains_store import host_allowed


class TestHostAllowed(unittest.TestCase):
    def test_subdomains_match_base(self) -> None:
        allowed = {"economist.com", "reuters.com"}
        self.assertTrue(host_allowed("www.economist.com", allowed))
        self.assertTrue(host_allowed("economist.com", allowed))
        self.assertTrue(host_allowed("graphics.reuters.com", allowed))
        self.assertFalse(host_allowed("evil.com", allowed))
        self.assertFalse(host_allowed("notreuters.com", allowed))


if __name__ == "__main__":
    unittest.main()

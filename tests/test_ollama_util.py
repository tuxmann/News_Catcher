"""Tests for Ollama model resolution."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ollama_util import resolve_ollama_model


class TestResolveOllamaModel(unittest.TestCase):
    @patch("ollama_util.list_ollama_models")
    def test_exact_match(self, mock_list) -> None:
        mock_list.return_value = ["llama3.1:8b", "mistral:7b"]
        model, msg = resolve_ollama_model("http://127.0.0.1:11434", "llama3.1:8b")
        self.assertEqual(model, "llama3.1:8b")
        self.assertIsNone(msg)

    @patch("ollama_util.list_ollama_models")
    def test_fallback_when_missing(self, mock_list) -> None:
        mock_list.return_value = ["llama3.1:8b", "mistral:7b"]
        model, msg = resolve_ollama_model("http://127.0.0.1:11434", "llama3.2")
        self.assertEqual(model, "llama3.1:8b")
        self.assertIn("not installed", msg or "")

    @patch("ollama_util.list_ollama_models")
    def test_unreachable(self, mock_list) -> None:
        mock_list.return_value = []
        model, msg = resolve_ollama_model("http://127.0.0.1:11434", "llama3.2")
        self.assertIsNone(model)
        self.assertIn("Cannot reach Ollama", msg or "")

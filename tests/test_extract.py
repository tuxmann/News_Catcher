"""Offline extraction tests using saved HTML fixtures."""

from __future__ import annotations

import unittest
from pathlib import Path

from extract import extract_article

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# (filename, fake canonical URL, substring expected in extracted text)
CASES: list[tuple[str, str, str]] = [
    (
        "economist_sample.html",
        "https://www.economist.com/finance/2024/01/01/fake",
        "inflation",
    ),
    (
        "reuters_sample.html",
        "https://www.reuters.com/markets/fake-article/",
        "equities",
    ),
    (
        "washingtonpost_sample.html",
        "https://www.washingtonpost.com/politics/fake/",
        "Lawmakers",
    ),
    (
        "intercept_sample.html",
        "https://theintercept.com/2024/01/01/fake/",
        "records",
    ),
    (
        "nypost_sample.html",
        "https://nypost.com/fake-article/",
        "Soho",
    ),
]


class TestParagraphPreservation(unittest.TestCase):
    def test_removes_standalone_sign_up_here_paragraph(self) -> None:
        html = b"""<html><body><article>
<p>Real body.</p>
<p>Sign up here.</p>
<p>More body.</p>
</article></body></html>"""
        article = extract_article(html, "https://www.reuters.com/world/example/")
        self.assertNotIn("Sign up here", article.text)
        self.assertIn("Real body", article.text)
        self.assertIn("More body", article.text)

    def test_sign_up_here_paragraph_match_is_case_insensitive(self) -> None:
        html = b"""<html><body><article>
<p>Lead.</p>
<p>SIGN UP HERE.</p>
<p>Tail.</p>
</article></body></html>"""
        article = extract_article(html, "https://www.reuters.com/world/example/")
        self.assertNotIn("SIGN UP HERE", article.text)
        self.assertIn("Lead", article.text)
        self.assertIn("Tail", article.text)

    def test_keeps_sign_up_here_when_not_its_own_paragraph(self) -> None:
        html = b"""<html><body><article>
<p>For more, Sign up here. We continue.</p>
</article></body></html>"""
        article = extract_article(html, "https://www.reuters.com/world/example/")
        self.assertIn("Sign up here", article.text)

    def test_trafilatura_keeps_blank_lines_between_p_tags(self) -> None:
        html = b"""<html><body><article>
<p>First paragraph here.</p>
<p>Second paragraph here.</p>
<p>Third mentions Kuwait.</p>
</article></body></html>"""
        article = extract_article(html, "https://www.reuters.com/world/example/")
        self.assertIn("here.\n\nSecond", article.text)
        self.assertGreaterEqual(article.text.count("\n\n"), 2)
        self.assertIn("Kuwait", article.text)


class TestImageExtraction(unittest.TestCase):
    def test_extracts_figure_images_with_captions(self) -> None:
        html = b"""<html><body><article>
<figure>
  <img src="/img/one.jpg" alt="Alt one"/>
  <figcaption>Caption one</figcaption>
</figure>
<figure>
  <img src="https://cdn.example.com/two.webp" alt="Alt two"/>
  <figcaption>Caption two</figcaption>
</figure>
</article></body></html>"""
        article = extract_article(html, "https://www.reuters.com/world/example/")
        self.assertGreaterEqual(len(article.images), 2)
        self.assertEqual(article.images[0].url, "https://www.reuters.com/img/one.jpg")
        self.assertEqual(article.images[0].caption, "Caption one")
        self.assertEqual(article.images[1].url, "https://cdn.example.com/two.webp")
        self.assertEqual(article.images[1].caption, "Caption two")

    def test_extracts_json_ld_image_entries(self) -> None:
        html = b"""<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "NewsArticle",
  "image": [
    {"url": "https://images.example.com/slide1.jpg", "caption": "Slide 1 caption"},
    {"url": "https://images.example.com/slide2.jpg", "caption": "Slide 2 caption"},
    {"url": "https://images.example.com/slide3.jpg", "caption": "Slide 3 caption"}
  ]
}
</script>
</head><body><p>Body text</p></body></html>"""
        article = extract_article(html, "https://www.reuters.com/world/example/")
        urls = [img.url for img in article.images]
        captions = [img.caption for img in article.images]
        self.assertIn("https://images.example.com/slide1.jpg", urls)
        self.assertIn("https://images.example.com/slide2.jpg", urls)
        self.assertIn("https://images.example.com/slide3.jpg", urls)
        self.assertIn("Slide 1 caption", captions)
        self.assertIn("Slide 2 caption", captions)
        self.assertIn("Slide 3 caption", captions)


class TestExtractFixtures(unittest.TestCase):
    def test_each_fixture_returns_non_empty_body(self) -> None:
        for filename, url, needle in CASES:
            path = FIXTURES_DIR / filename
            with self.subTest(filename=filename):
                raw = path.read_bytes()
                article = extract_article(raw, url)
                self.assertTrue(article.text.strip(), f"empty text for {filename}")
                self.assertIn(
                    needle.lower(),
                    article.text.lower(),
                    f"expected {needle!r} in extraction for {filename}",
                )


if __name__ == "__main__":
    unittest.main()

"""Core utilities: JSON repair, text processing, safe Markdown, graphs, config."""

from __future__ import annotations

import networkx as nx
from django.test import SimpleTestCase, TestCase

from apps.core.config import get_config, mask, save_overrides
from apps.core.geo import get_country
from apps.core.geo.archetypes import ARCHETYPES
from apps.core.graphs import pagerank
from apps.core.llm.jsonfix import JSONExtractionError, as_float, as_list, as_str_list, extract_json
from apps.core.models import SiteConfig
from apps.core.text.markdown import render_markdown
from apps.core.text.retrieval import BM25, from_bytes, rrf, to_bytes
from apps.core.text.sentiment import emotion, sentiment
from apps.core.text.textutils import camel_tag, chunk_text, clean_text, keywords, normalize_url, slug_handle


class ExtractJSONTests(SimpleTestCase):
    def test_plain_object(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_code_fence_and_prose(self):
        text = 'Sure! Here is the JSON you asked for:\n```json\n{"title": "Bread VAT", "n": 2}\n```\nHope it helps.'
        self.assertEqual(extract_json(text), {"title": "Bread VAT", "n": 2})

    def test_trailing_commas_and_smart_quotes(self):
        self.assertEqual(extract_json("{“a”: [1, 2,], “b”: “x”,}"), {"a": [1, 2], "b": "x"})

    def test_python_literals_and_single_quotes(self):
        self.assertEqual(extract_json("{'ok': True, 'value': None}"), {"ok": True, "value": None})

    def test_truncated_output_is_closed(self):
        value = extract_json('{"labels": [{"id": 1, "stance": -0.5}, {"id": 2, "stan')
        self.assertEqual(value["labels"][0], {"id": 1, "stance": -0.5})

    def test_raw_newlines_inside_strings(self):
        self.assertEqual(extract_json('{"text": "line one\nline two"}'), {"text": "line one\nline two"})

    def test_expect_list_unwraps_container(self):
        self.assertEqual(extract_json('{"items": [1, 2, 3]}', list), [1, 2, 3])

    def test_expect_dict_wraps_list(self):
        self.assertEqual(extract_json('[{"a": 1}]', dict), {"a": 1})
        self.assertEqual(extract_json("[1, 2]", dict), {"items": [1, 2]})

    def test_garbage_raises(self):
        with self.assertRaises(JSONExtractionError):
            extract_json("no json here at all")
        with self.assertRaises(JSONExtractionError):
            extract_json("")

    def test_coercion_helpers(self):
        self.assertEqual(as_float("0.7", 0, -1, 1), 0.7)
        self.assertEqual(as_float("5", 0, -1, 1), 1)
        self.assertEqual(as_float("abc", 0.25), 0.25)
        self.assertEqual(as_list("x"), ["x"])
        self.assertEqual(as_str_list(["a", " ", None, "b"]), ["a", "b"])


class TextTests(SimpleTestCase):
    def test_clean_text_strips_html_and_whitespace(self):
        self.assertEqual(clean_text("<p>Hello&nbsp;   <b>world</b></p>"), "Hello world")

    def test_chunking_overlaps_and_covers_text(self):
        text = " ".join(f"Sentence number {i} talks about the finance bill." for i in range(120))
        chunks = chunk_text(text, max_chars=500, overlap=80)
        self.assertGreater(len(chunks), 5)
        self.assertTrue(all(len(c) <= 600 for c in chunks))
        self.assertIn("Sentence number 119", chunks[-1])

    def test_keywords(self):
        words = keywords("The mobile money excise duty raises costs for mobile money agents and traders in Nairobi.", 5)
        self.assertTrue(any("mobile money" in w for w in words))

    def test_normalize_url_drops_tracking(self):
        self.assertEqual(
            normalize_url("https://Nation.Africa/news/x/?utm_source=tw&id=3#top"),
            normalize_url("https://nation.africa/news/x?id=3"),
        )

    def test_handles_and_tags(self):
        self.assertTrue(slug_handle("Kenya Revenue Authority").isidentifier())
        self.assertEqual(camel_tag("reject finance bill"), "RejectFinanceBill")
        self.assertEqual(camel_tag("Finance Bill 2025"), "FinanceBill2025")
        self.assertEqual(camel_tag("2% levy on mobile money transfers"), "LevyMobileMoneyTransfers")

    def test_sentiment_understands_local_expressions(self):
        self.assertLess(sentiment("Hii ushuru ni wizi mtupu, serikali inatuumiza"), 0)
        self.assertGreater(sentiment("This is a great and fair policy, well done"), 0)
        self.assertLess(sentiment("Wueh, this tax is a scam. Tumechoka!"), 0)

    def test_emotion(self):
        label, intensity = emotion("I am furious and outraged, this is a disgrace!")
        self.assertEqual(label, "anger")
        self.assertGreater(intensity, 0)

    def test_bm25_ranks_relevant_document_first(self):
        docs = ["VAT on bread raises prices", "Football results from the weekend", "Bread prices and VAT debate in parliament"]
        scores = BM25(docs).scores("bread VAT")
        self.assertLess(scores[1], min(scores[0], scores[2]))

    def test_vector_roundtrip_and_rrf(self):
        vec = from_bytes(to_bytes([0.1, 0.2, 0.3]))
        self.assertAlmostEqual(float(vec[1]), 0.2, places=5)
        fused = rrf([[(0, 1.0), (1, 0.5)], [(1, 1.0), (2, 0.4)]], top_k=3)
        self.assertEqual(fused[0][0], 1)


class MarkdownSafetyTests(SimpleTestCase):
    def test_script_is_escaped(self):
        html = render_markdown("# Title\n\n<script>alert(1)</script>\n\n**bold** and [link](https://example.org)")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn('href="https://example.org"', html)

    def test_javascript_links_are_not_rendered(self):
        html = render_markdown("[click](javascript:alert(1))")
        self.assertNotIn('href="javascript', html)

    def test_attribute_injection_in_links(self):
        html = render_markdown('[x](https://a.org/"onmouseover="alert(1))')
        self.assertNotIn('"onmouseover=', html)

    def test_tables_and_lists(self):
        html = render_markdown("| A | B |\n|---|---|\n| 1 | 2 |\n\n- one\n- two")
        self.assertIn("<table", html)
        self.assertIn("<li>one</li>", html)


class GraphTests(SimpleTestCase):
    def test_pagerank_matches_networkx_definition(self):
        graph = nx.DiGraph()
        graph.add_weighted_edges_from([("a", "b", 1), ("b", "c", 1), ("c", "a", 1), ("d", "a", 2)])
        ranks = pagerank(graph)
        self.assertAlmostEqual(sum(ranks.values()), 1.0, places=6)
        self.assertGreater(ranks["a"], ranks["d"])

    def test_pagerank_handles_empty_and_dangling(self):
        self.assertEqual(pagerank(nx.Graph()), {})
        graph = nx.DiGraph()
        graph.add_edge("a", "b")
        ranks = pagerank(graph)
        self.assertGreater(ranks["b"], ranks["a"])


class GeoTests(SimpleTestCase):
    def test_kenya_has_47_counties_with_population(self):
        kenya = get_country("KE")
        self.assertEqual(len(kenya.regions), 47)
        self.assertTrue(all(r.population > 0 for r in kenya.regions))
        self.assertIn("Kenya", kenya.subreddits)

    def test_unknown_country_falls_back_to_generic(self):
        self.assertEqual(get_country("ZZ").code, "XX")

    def test_archetypes_have_valid_weights(self):
        self.assertGreaterEqual(len(ARCHETYPES), 20)
        for archetype in ARCHETYPES:
            self.assertTrue(archetype.key and archetype.label)


class ConfigTests(TestCase):
    def test_overrides_and_masking(self):
        save_overrides({"LLM_PROVIDER": "fake", "TAVILY_API_KEY": "tvly-secret-1234567"})
        config = get_config(refresh=True)
        self.assertEqual(config["LLM_PROVIDER"], "fake")
        self.assertEqual(config["TAVILY_API_KEY"], "tvly-secret-1234567")
        self.assertNotIn("secret", mask(config["TAVILY_API_KEY"]))
        self.assertTrue(SiteConfig.objects.exists())
        save_overrides({"LLM_PROVIDER": "", "TAVILY_API_KEY": ""})
        get_config(refresh=True)

    def test_numeric_and_bool_coercion(self):
        save_overrides({"LLM_CONCURRENCY": "3", "RESPECT_ROBOTS_TXT": "false"})
        config = get_config(refresh=True)
        self.assertEqual(config["LLM_CONCURRENCY"], 3)
        self.assertIs(config["RESPECT_ROBOTS_TXT"], False)
        save_overrides({"LLM_CONCURRENCY": "", "RESPECT_ROBOTS_TXT": ""})
        get_config(refresh=True)

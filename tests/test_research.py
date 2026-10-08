"""Research connectors (parsed from realistic API payloads), extraction and the pipeline."""

from __future__ import annotations

import io
from unittest import mock

import httpx
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.core.geo import get_country
from apps.core.jobs import run_inline
from apps.projects.models import Project
from apps.research.connectors import CONNECTORS, CONNECTORS_BY_KEY, CostTier, SearchContext, available_connectors, default_selection, parse_date
from apps.research.connectors.social import anonymize, parse_telegram
from apps.research.connectors.web import _relative_date, parse_rss
from apps.research.extract import extract_html, extract_upload, fetch_article, parse_social_csv
from apps.research.http import Fetcher
from apps.research.models import Source
from apps.research.pipeline import add_text, add_upload, observed_stance_summary, plan_tasks, save_items, score_relevance, DEPTH

from .helpers import FakeLLMMixin, fixture, mock_client

NO_WAIT = mock.patch.multiple("apps.research.http", HOST_INTERVALS={}, DEFAULT_INTERVAL=0.0)


def make_ctx(routes: dict, *, country: str = "KE", config: dict | None = None, limit: int = 10, robots: bool = False) -> SearchContext:
    fetcher = Fetcher(user_agent="BarazaTest/1.0", respect_robots=robots, client=mock_client(routes))
    return SearchContext(country=get_country(country), fetcher=fetcher, config=config or {}, limit=limit, timelimit="m")


REDDIT_LISTING = {
    "data": {
        "children": [
            {"data": {"permalink": "/r/Kenya/comments/abc/finance_bill/", "title": "Finance Bill 2024 megathread", "selftext": "VAT on bread is madness.", "author": "wanjiku_ke", "created_utc": 1718700000, "score": 950, "num_comments": 2, "subreddit": "Kenya"}},
            {"data": {"permalink": "/r/Kenya/comments/def/tax/", "title": "Is the mobile money tax fair?", "selftext": "", "author": "otieno", "created_utc": 1718600000, "score": 40, "num_comments": 0, "subreddit": "Kenya"}},
        ]
    }
}
REDDIT_THREAD = [
    REDDIT_LISTING,
    {"data": {"children": [
        {"data": {"permalink": "/r/Kenya/comments/abc/finance_bill/c1/", "body": "We are overtaxed already. #RejectFinanceBill", "author": "kamau", "created_utc": 1718700500, "score": 300}},
        {"data": {"permalink": "/r/Kenya/comments/abc/finance_bill/c2/", "body": "[deleted]", "author": "x", "score": 1}},
    ]}},
]


class ConnectorParsingTests(TestCase):
    def setUp(self):
        NO_WAIT.start()

    def tearDown(self):
        NO_WAIT.stop()

    def test_registry_is_ordered_free_first_and_defaults_need_no_keys(self):
        infos = available_connectors({})
        tiers = [CostTier.ORDER.index(i["cost"]) for i in infos]
        self.assertEqual(tiers, sorted(tiers))
        defaults = default_selection({})
        self.assertIn("google_news", defaults)
        self.assertIn("social_index", defaults)
        for key in defaults:
            self.assertIn(CONNECTORS_BY_KEY[key].cost, (CostTier.FREE,))
        self.assertTrue(all(c.description and c.setup for c in CONNECTORS))

    def test_paid_connectors_need_configuration(self):
        for key in ("apify", "twitterapi_io", "x_api", "tavily", "youtube"):
            self.assertFalse(CONNECTORS_BY_KEY[key].is_configured({}), key)
        self.assertTrue(CONNECTORS_BY_KEY["youtube"].is_configured({"YOUTUBE_API_KEY": "k"}))

    def test_google_news_uses_country_edition(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, text=fixture("google_news.xml"))

        ctx = make_ctx({"news.google.com": handler})
        items = CONNECTORS_BY_KEY["google_news"].search("finance bill", ctx)
        self.assertIn("gl=KE", seen[0])
        self.assertIn("when%3A31d", seen[0])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "MPs to debate Finance Bill as traders protest VAT on bread")
        self.assertEqual(items[0].author, "Daily Nation")
        self.assertEqual(items[0].published_at.year, 2024)

    def test_parse_atom_feed(self):
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Treasury statement</title>
        <link href="https://treasury.go.ke/press/1"/><summary>Bread VAT update</summary><updated>2024-06-20T10:00:00Z</updated></entry></feed>"""
        items = parse_rss(atom)
        self.assertEqual(items[0].url, "https://treasury.go.ke/press/1")
        self.assertEqual(parse_rss("not xml"), [])

    def test_gdelt_filters_languages_and_country(self):
        captured = {}

        def handler(request):
            captured.update(dict(request.url.params))
            return httpx.Response(200, json={"articles": [
                {"url": "https://www.standardmedia.co.ke/a", "title": "Finance Bill protests", "seendate": "20240620T101500Z", "domain": "standardmedia.co.ke", "language": "English", "sourcecountry": "Kenya"},
                {"url": "https://example.cn/b", "title": "Unrelated", "seendate": "20240620T101500Z", "domain": "example.cn", "language": "Chinese"},
            ]})

        items = CONNECTORS_BY_KEY["gdelt"].search("Kenya finance bill protests", make_ctx({"api.gdeltproject.org": handler}))
        self.assertIn("sourcecountry:kenya", captured["query"].lower())
        self.assertNotIn(" kenya ", f" {captured['query'].split('sourcecountry')[0].lower()} ")
        self.assertEqual([i.url for i in items], ["https://www.standardmedia.co.ke/a"])
        self.assertEqual(items[0].published_at.hour, 10)

    def test_gdelt_plain_text_error_is_not_fatal(self):
        items = CONNECTORS_BY_KEY["gdelt"].search("x", make_ctx({"api.gdeltproject.org": httpx.Response(200, text="Your search contained a phrase that was too short.")}))
        self.assertEqual(items, [])

    def test_reddit_public_json_posts_and_comments(self):
        def handler(request):
            path = request.url.path
            if "/comments/abc" in path:
                return httpx.Response(200, json=REDDIT_THREAD)
            return httpx.Response(200, json=REDDIT_LISTING)

        items = CONNECTORS_BY_KEY["reddit"].search("finance bill", make_ctx({"www.reddit.com": handler}))
        posts = [i for i in items if i.kind == "social_post"]
        comments = [i for i in items if i.kind == "comment"]
        self.assertEqual(len(posts), 2)  # de-duplicated across subreddit + global search
        self.assertEqual(posts[0].metrics["score"], 950)
        self.assertEqual(len(comments), 1)  # [deleted] dropped
        self.assertIn("overtaxed", comments[0].text)

    def test_youtube_videos_and_comments(self):
        routes = {
            "/youtube/v3/search": {"items": [{"id": {"videoId": "vid1"}, "snippet": {"title": "Finance Bill debate", "description": "Citizen TV", "channelTitle": "Citizen TV Kenya", "publishedAt": "2024-06-19T18:00:00Z"}}]},
            "/youtube/v3/commentThreads": {"items": [{"id": "t1", "snippet": {"totalReplyCount": 4, "topLevelComment": {"id": "c1", "snippet": {"textOriginal": "Hii ni wizi! Reject the bill.", "authorDisplayName": "@mkenya", "likeCount": 210, "publishedAt": "2024-06-19T19:00:00Z"}}}}]},
        }
        items = CONNECTORS_BY_KEY["youtube"].search("finance bill", make_ctx(routes, config={"YOUTUBE_API_KEY": "k"}))
        self.assertEqual([i.kind for i in items], ["snippet", "comment"])
        self.assertEqual(items[1].metrics["likes"], 210)
        self.assertIn("lc=c1", items[1].url)

    def test_telegram_preview_parsing(self):
        items = parse_telegram(fixture("telegram.html"), "newsdesk")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].url, "https://t.me/newsdesk/1201")
        self.assertEqual(items[0].metrics["views"], 12500)
        self.assertEqual(items[1].metrics["views"], 980)

    def test_bluesky_and_mastodon(self):
        bsky = {"posts": [{"uri": "at://did:plc:xyz/app.bsky.feed.post/3kabc", "author": {"handle": "amina.bsky.social"}, "record": {"text": "Bread VAT will hurt families", "createdAt": "2024-06-20T08:00:00Z"}, "likeCount": 5, "repostCount": 1, "replyCount": 0}]}
        items = CONNECTORS_BY_KEY["bluesky"].search("bread vat", make_ctx({"public.api.bsky.app": bsky}))
        self.assertEqual(items[0].url, "https://bsky.app/profile/amina.bsky.social/post/3kabc")

        statuses = [{"url": "https://mastodon.social/@a/1", "content": "<p>Reject the <b>Finance Bill</b></p>", "created_at": "2024-06-20T08:00:00Z", "account": {"acct": "a"}, "favourites_count": 2, "reblogs_count": 0, "replies_count": 1}]
        items = CONNECTORS_BY_KEY["mastodon"].search("finance bill", make_ctx({"/api/v1/timelines/tag/FinanceBill": statuses}))
        self.assertEqual(items[0].text, "Reject the Finance Bill")

    def test_x_api_and_twitterapi_io(self):
        x_payload = {"data": [{"id": "17", "text": "The Finance Bill is necessary to cut debt", "author_id": "u1", "created_at": "2024-06-20T08:00:00.000Z", "public_metrics": {"like_count": 12, "retweet_count": 3, "reply_count": 2, "impression_count": 900}}], "includes": {"users": [{"id": "u1", "username": "econ_ke"}]}}
        items = CONNECTORS_BY_KEY["x_api"].search("finance bill", make_ctx({"api.x.com": x_payload}, config={"X_BEARER_TOKEN": "t"}))
        self.assertEqual((items[0].author, items[0].metrics["views"]), ("econ_ke", 900))

        io_payload = {"tweets": [{"id": "18", "url": "https://x.com/a/status/18", "text": "#RejectFinanceBill2024 now!", "author": {"userName": "gen_z_ke"}, "createdAt": "Thu Jun 20 08:00:00 +0000 2024", "likeCount": 99}]}
        items = CONNECTORS_BY_KEY["twitterapi_io"].search("finance bill", make_ctx({"api.twitterapi.io": io_payload}, config={"TWITTERAPI_IO_KEY": "k"}))
        self.assertEqual(items[0].published_at.year, 2024)
        self.assertEqual(items[0].metrics["likes"], 99)

    def test_apify_generic_row_mapping(self):
        rows = [{"fullText": "LinkedIn post on the Finance Bill and SMEs", "postUrl": "https://www.linkedin.com/posts/x", "author": {"name": "A. Mwangi"}, "postedAt": "2024-06-20T08:00:00Z", "numLikes": "41"}]
        with mock.patch.dict("os.environ", {"APIFY_ACTOR_X": "off", "APIFY_ACTOR_TIKTOK": "off", "APIFY_ACTOR_FACEBOOK": "off"}):
            items = CONNECTORS_BY_KEY["apify"].search("finance bill", make_ctx({"api.apify.com": rows}, config={"APIFY_TOKEN": "t"}))
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0].platform, items[0].metrics["likes"]), ("linkedin", 41))

    def test_wikipedia_reference(self):
        def handler(request):
            params = request.url.params
            if params.get("list") == "search":
                return httpx.Response(200, json={"query": {"search": [{"title": "Kenya Revenue Authority"}]}})
            return httpx.Response(200, json={"query": {"pages": {"1": {"extract": "The Kenya Revenue Authority (KRA) is the agency responsible for tax collection."}}}})

        items = CONNECTORS_BY_KEY["wikipedia"].search("KRA", make_ctx({"en.wikipedia.org": handler}))
        self.assertEqual(items[0].url, "https://en.wikipedia.org/wiki/Kenya_Revenue_Authority")
        self.assertEqual(items[0].kind, "reference")

    def test_dates_and_helpers(self):
        self.assertEqual(parse_date("20240620T101500Z").minute, 15)
        self.assertEqual(parse_date(1718700000).year, 2024)
        self.assertEqual(parse_date("Tue, 18 Jun 2024 07:00:00 GMT").day, 18)
        self.assertIsNone(parse_date("someday"))
        self.assertIsNotNone(_relative_date("3 days ago"))
        self.assertTrue(anonymize("real_handle").startswith("user_"))
        self.assertNotIn("real_handle", anonymize("real_handle"))


class ExtractionTests(TestCase):
    def setUp(self):
        NO_WAIT.start()

    def tearDown(self):
        NO_WAIT.stop()

    def test_extract_html_main_text(self):
        title, text = extract_html(fixture("article.html"), "https://example.co.ke/a")
        self.assertIn("bread", text)
        self.assertNotIn("Copyright", text)
        self.assertIn("Finance Bill", title)

    def test_fetch_article_respects_robots(self):
        routes = {"/robots.txt": "User-agent: *\nDisallow: /private/", "example.co.ke": fixture("article.html")}
        fetcher = Fetcher(user_agent="BarazaTest/1.0", respect_robots=True, client=mock_client(routes))
        self.assertEqual(fetch_article("https://example.co.ke/private/a", fetcher, use_jina=False), ("", ""))
        title, text = fetch_article("https://example.co.ke/news/a", fetcher, use_jina=False)
        self.assertIn("Treasury", text)

    def test_fetch_article_skips_social_domains(self):
        fetcher = Fetcher(user_agent="t", respect_robots=False, client=mock_client({}))
        self.assertEqual(fetch_article("https://x.com/a/status/1", fetcher), ("", ""))

    def test_social_csv(self):
        data = "Date,Tweet,Likes,Platform\n2024-06-20,Reject the bill!,5,x\n2024-06-21,,1,x\n2024-06-21,Taxes fund roads,2,facebook\n".encode()
        rows = parse_social_csv(data)
        self.assertEqual([r["text"] for r in rows], ["Reject the bill!", "Taxes fund roads"])
        self.assertEqual(rows[1]["platform"], "facebook")
        kind, text = extract_upload("export.csv", data)
        self.assertEqual(kind, "social_csv")

    def test_docx_and_text_uploads(self):
        import docx

        document = docx.Document()
        document.add_paragraph("Memo: the bread VAT proposal and its effect on households.")
        buffer = io.BytesIO()
        document.save(buffer)
        kind, text = extract_upload("memo.docx", buffer.getvalue())
        self.assertIn("bread VAT", text)
        self.assertEqual(extract_upload("notes.txt", "Plain notes".encode("utf-16"))[1], "Plain notes")


BRIEF = {
    "title": "Finance Bill 2024: VAT on bread",
    "refined_question": "How will Kenyans react to 16% VAT on bread?",
    "summary": "Treasury proposes VAT on bread and higher mobile money excise duty.",
    "key_issues": ["cost of living", "bread prices"],
    "search_queries": ["Finance Bill 2024 VAT bread Kenya", "mobile money excise duty Kenya"],
    "social_keywords": ["RejectFinanceBill", "bread tax"],
    "hashtags": {"oppose": ["#RejectFinanceBill2024"], "support": []},
    "stakeholders": [{"name": "National Treasury"}],
}


class PipelineTests(FakeLLMMixin, TestCase):
    def setUp(self):
        super().setUp()
        NO_WAIT.start()
        self.project = Project.objects.create(title="Bread VAT", prompt="VAT on bread in Kenya", country="KE", depth="quick", brief=BRIEF)

    def tearDown(self):
        NO_WAIT.stop()
        super().tearDown()

    def test_plan_respects_query_kinds_and_caps(self):
        connectors = [CONNECTORS_BY_KEY[k] for k in ("google_news", "reddit", "wikipedia", "gdelt")]
        plan = plan_tasks(BRIEF, connectors, DEPTH["quick"])
        by = {}
        for connector, query in plan:
            by.setdefault(connector.key, []).append(query)
        self.assertIn("Finance Bill 2024 VAT bread Kenya", by["google_news"])
        self.assertIn("RejectFinanceBill", by["reddit"])
        self.assertEqual(by["wikipedia"][0], BRIEF["title"])

    def test_relevance_scoring(self):
        from apps.research.connectors import RawItem

        items = [RawItem(url="a", title="VAT on bread in Kenya", text="bread prices"), RawItem(url="b", title="Football", text="Harambee Stars win")]
        scores = score_relevance(items, BRIEF)
        self.assertEqual(scores[0], 1.0)
        self.assertLess(scores[1], 0.1)

    def test_save_items_dedupes_and_anonymises(self):
        from apps.research.connectors import RawItem

        items = [
            RawItem(url="https://www.reddit.com/r/Kenya/1?utm_source=x", title="Bread VAT", text="Reject it", platform="reddit", kind="social_post", author="real_user"),
            RawItem(url="https://www.reddit.com/r/Kenya/1", title="Bread VAT", text="Reject it, longer version", platform="reddit", kind="social_post", author="real_user"),
        ]
        saved = save_items(self.project, items, BRIEF, {"ANONYMIZE_SOCIAL_AUTHORS": True})
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].author.startswith("user_"))
        self.assertIn("longer", saved[0].snippet)
        self.assertEqual(save_items(self.project, items, BRIEF, {}), [])  # already stored

    def test_full_research_job_with_mocked_internet(self):
        def gnews(request):
            return httpx.Response(200, text=fixture("google_news.xml"))

        def reddit(request):
            if "/comments/abc" in request.url.path:
                return httpx.Response(200, json=REDDIT_THREAD)
            return httpx.Response(200, json=REDDIT_LISTING)

        routes = {
            "news.google.com/rss": gnews,
            "www.reddit.com": reddit,
            "nation.africa": fixture("article.html"),
            "/robots.txt": httpx.Response(404),
        }
        fetcher = Fetcher(user_agent="BarazaTest/1.0", respect_robots=True, client=mock_client(routes))
        with mock.patch("apps.research.pipeline.make_fetcher", return_value=fetcher):
            job = run_inline(
                "apps.research.pipeline.run_research",
                kind="research",
                label="research",
                project=self.project,
                payload={"project_id": str(self.project.pk), "connectors": ["google_news", "reddit", "youtube"]},
            )
        self.assertEqual(job.status, "succeeded", job.error)
        self.assertGreaterEqual(job.result["new_sources"], 4)
        self.assertTrue(any("need setup" in line[2] for line in job.log))  # youtube without a key
        sources = self.project.sources.all()
        self.assertTrue(sources.filter(platform="reddit", stance__isnull=False).exists())
        self.assertTrue(all(s.author.startswith("user_") or not s.author for s in sources.filter(platform="reddit")))
        summary = observed_stance_summary(self.project)
        self.assertGreater(summary["n_stance"], 0)
        self.assertAlmostEqual(summary["support_share"] + summary["oppose_share"] + summary["neutral_share"], 1.0, places=2)

    def test_connector_failure_is_isolated(self):
        fetcher = Fetcher(user_agent="t", respect_robots=False, client=mock_client({"news.google.com": httpx.Response(500, text="boom")}))
        with mock.patch("apps.research.pipeline.make_fetcher", return_value=fetcher), mock.patch("apps.research.http.time.sleep"):
            job = run_inline("apps.research.pipeline.run_research", kind="research", label="r", project=self.project, payload={"project_id": str(self.project.pk), "connectors": ["google_news"]})
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.result["connectors"]["google_news"]["errors"], 2)
        self.assertTrue(any(level == "warning" and "No sources" in msg for _, level, msg in job.log))

    def test_manual_text_and_csv_upload(self):
        source = add_text(self.project, "Briefing", "Treasury says the bread VAT raises revenue.")
        self.assertTrue(source.included and source.full_text)
        upload = SimpleUploadedFile("posts.csv", b"text,platform\nWe reject the bread tax,x\nGood for revenue,facebook\n")
        created = add_upload(self.project, upload)
        self.assertEqual(len(created), 2)
        self.assertEqual(self.project.sources.filter(connector="upload_csv").count(), 2)
        self.assertEqual(Source.objects.filter(project=self.project).count(), 3)

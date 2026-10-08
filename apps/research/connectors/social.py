"""
Social media connectors.

Order of preference (cheapest first):
1. Official free APIs (Reddit, YouTube Data API, Bluesky, Mastodon).
2. Public web previews (Telegram channels).
3. Search-engine-indexed public posts for platforms with no free API
   (X/Twitter, Facebook, LinkedIn, TikTok, Instagram) - free, ToS-friendly,
   but only snippets.
4. Paid, low-cost providers (X API pay-per-use, twitterapi.io, Apify actors).

We never log in to personal accounts or bypass access controls.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from apps.core.text.textutils import clean_text

from ..http import FetchError
from .base import TIMELIMIT_DAYS, Connector, CostTier, RawItem, SearchContext, parse_date

REDDIT_TIME = {"d": "day", "w": "week", "m": "month", "y": "year"}


class Reddit(Connector):
    key = "reddit"
    name = "Reddit"
    platform = "reddit"
    cost = CostTier.FREE
    query_kind = "social"
    description = "Posts and top comments from country subreddits (e.g. r/Kenya, r/Nairobi) and Reddit-wide search."
    setup = "Works without keys via public JSON. For reliability create a free 'script' app at reddit.com/prefs/apps and add REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET."
    terms = "Reddit's Data API terms allow free non-commercial research use with rate limits."
    docs_url = "https://www.reddit.com/dev/api/"

    _token_lock = threading.Lock()
    _token: tuple[str, float] | None = None

    def _oauth_token(self, ctx: SearchContext) -> str | None:
        cid, secret = ctx.config.get("REDDIT_CLIENT_ID"), ctx.config.get("REDDIT_CLIENT_SECRET")
        if not (cid and secret):
            return None
        with self._token_lock:
            if Reddit._token and Reddit._token[1] > time.time() + 60:
                return Reddit._token[0]
            response = ctx.fetcher.post(
                "https://www.reddit.com/api/v1/access_token",
                data={"grant_type": "client_credentials"},
                auth=(cid, secret),
            )
            if response.status_code >= 400:
                ctx.log(f"Reddit OAuth failed (HTTP {response.status_code}); using public JSON.")
                return None
            data = response.json()
            Reddit._token = (data.get("access_token", ""), time.time() + float(data.get("expires_in", 3600)))
            return Reddit._token[0]

    def _get(self, path: str, params: dict, ctx: SearchContext):
        token = self._oauth_token(ctx)
        if token:
            return ctx.fetcher.get_json(f"https://oauth.reddit.com{path}", params=params, headers={"Authorization": f"bearer {token}"})
        return ctx.fetcher.get_json(f"https://www.reddit.com{path}.json", params=params)

    def search(self, query, ctx: SearchContext):
        t = REDDIT_TIME.get(ctx.timelimit or "y", "year")
        listings = []
        for sub in ctx.country.subreddits[:2]:
            try:
                listings.append(self._get(f"/r/{sub}/search", {"q": query, "restrict_sr": "1", "sort": "relevance", "t": t, "limit": str(ctx.limit)}, ctx))
            except FetchError as exc:
                ctx.log(f"Reddit r/{sub}: {exc}")
        country = ctx.country.name if ctx.country.code != "XX" else ""
        try:
            listings.append(self._get("/search", {"q": f"{query} {country}".strip(), "sort": "relevance", "t": t, "limit": str(ctx.limit)}, ctx))
        except FetchError as exc:
            ctx.log(f"Reddit search: {exc}")

        items: list[RawItem] = []
        seen = set()
        posts = []
        for listing in listings:
            for child in (listing.get("data") or {}).get("children", []):
                post = child.get("data") or {}
                if not post.get("permalink") or post["permalink"] in seen:
                    continue
                seen.add(post["permalink"])
                posts.append(post)
        posts.sort(key=lambda p: -(p.get("num_comments", 0) + p.get("score", 0)))
        for post in posts[: ctx.limit]:
            url = f"https://www.reddit.com{post['permalink']}"
            items.append(
                RawItem(
                    url=url,
                    title=clean_text(post.get("title"), 300),
                    text=clean_text(f"{post.get('title', '')}\n\n{post.get('selftext', '')}"),
                    platform="reddit",
                    kind="social_post",
                    author=post.get("author", ""),
                    published_at=parse_date(post.get("created_utc")),
                    metrics={"score": post.get("score", 0), "comments": post.get("num_comments", 0)},
                    meta={"subreddit": post.get("subreddit")},
                )
            )
        # Top comments carry most of the public sentiment.
        for post in posts[: max(2, ctx.limit // 3)]:
            if not post.get("num_comments"):
                continue
            try:
                thread = self._get(post["permalink"].rstrip("/"), {"limit": "15", "sort": "top", "depth": "1"}, ctx)
            except FetchError:
                continue
            if not isinstance(thread, list) or len(thread) < 2:
                continue
            for child in (thread[1].get("data") or {}).get("children", [])[:12]:
                comment = child.get("data") or {}
                body = clean_text(comment.get("body"))
                if not body or body in ("[deleted]", "[removed]") or not comment.get("permalink"):
                    continue
                items.append(
                    RawItem(
                        url=f"https://www.reddit.com{comment['permalink']}",
                        title=f"Comment on: {clean_text(post.get('title'), 120)}",
                        text=body,
                        platform="reddit",
                        kind="comment",
                        author=comment.get("author", ""),
                        published_at=parse_date(comment.get("created_utc")),
                        metrics={"score": comment.get("score", 0)},
                        meta={"subreddit": post.get("subreddit"), "parent": post.get("permalink")},
                    )
                )
        return items


class YouTube(Connector):
    key = "youtube"
    name = "YouTube comments (Data API)"
    platform = "youtube"
    cost = CostTier.FREE_KEY
    query_kind = "web"
    requires = ("YOUTUBE_API_KEY",)
    description = "Comments under news videos (e.g. local TV stations) are one of the richest free sources of public opinion in Africa."
    setup = "Create a free API key in Google Cloud Console (enable 'YouTube Data API v3') and paste it in Settings."
    price_note = "Free quota of 10,000 units per day (about 50 searches plus thousands of comments)."
    terms = "YouTube API Services Terms; store only what you need for research."
    docs_url = "https://developers.google.com/youtube/v3/docs"

    def search(self, query, ctx: SearchContext):
        key = ctx.config["YOUTUBE_API_KEY"]
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": str(min(10, max(3, ctx.limit // 2))),
            "order": "relevance",
            "relevanceLanguage": "en",
            "key": key,
        }
        if ctx.country.code != "XX":
            params["regionCode"] = ctx.country.code
        if ctx.timelimit in TIMELIMIT_DAYS:
            after = datetime.now(timezone.utc) - timedelta(days=TIMELIMIT_DAYS[ctx.timelimit])
            params["publishedAfter"] = after.strftime("%Y-%m-%dT%H:%M:%SZ")
        data = ctx.fetcher.get_json("https://www.googleapis.com/youtube/v3/search", params=params)
        items = []
        for video in data.get("items", []):
            video_id = (video.get("id") or {}).get("videoId")
            snippet = video.get("snippet") or {}
            if not video_id:
                continue
            url = f"https://www.youtube.com/watch?v={video_id}"
            items.append(
                RawItem(
                    url=url,
                    title=clean_text(snippet.get("title"), 300),
                    text=clean_text(snippet.get("description")) or clean_text(snippet.get("title")),
                    platform="youtube",
                    kind="snippet",
                    author=snippet.get("channelTitle", ""),
                    published_at=parse_date(snippet.get("publishedAt")),
                    meta={"video_id": video_id, "channel": snippet.get("channelTitle")},
                )
            )
            try:
                comments = ctx.fetcher.get_json(
                    "https://www.googleapis.com/youtube/v3/commentThreads",
                    params={"part": "snippet", "videoId": video_id, "maxResults": "40", "order": "relevance", "textFormat": "plainText", "key": key},
                )
            except FetchError as exc:  # comments disabled, quota, etc.
                ctx.log(f"YouTube comments for {video_id}: {exc}")
                continue
            for thread in comments.get("items", []):
                top = ((thread.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
                text = clean_text(top.get("textOriginal") or top.get("textDisplay"))
                if not text:
                    continue
                comment_id = (thread.get("snippet") or {}).get("topLevelComment", {}).get("id") or thread.get("id")
                items.append(
                    RawItem(
                        url=f"{url}&lc={comment_id}",
                        title=f"Comment on: {clean_text(snippet.get('title'), 120)}",
                        text=text,
                        platform="youtube",
                        kind="comment",
                        author=top.get("authorDisplayName", ""),
                        published_at=parse_date(top.get("publishedAt")),
                        metrics={"likes": top.get("likeCount", 0), "comments": (thread.get("snippet") or {}).get("totalReplyCount", 0)},
                        meta={"video_id": video_id},
                    )
                )
        return items


class Bluesky(Connector):
    key = "bluesky"
    name = "Bluesky"
    platform = "bluesky"
    cost = CostTier.FREE
    query_kind = "social"
    description = "Open social network with a free public search API. Smaller African audience, but growing among journalists and academics."
    setup = "Works without keys when the public API allows it; otherwise add BLUESKY_HANDLE and an app password (Settings → App passwords on bsky.app)."
    terms = "Open AT Protocol; public posts."
    docs_url = "https://docs.bsky.app/"

    _session_lock = threading.Lock()
    _session: tuple[str, float] | None = None

    def _token(self, ctx: SearchContext) -> str | None:
        handle, password = ctx.config.get("BLUESKY_HANDLE"), ctx.config.get("BLUESKY_APP_PASSWORD")
        if not (handle and password):
            return None
        with self._session_lock:
            if Bluesky._session and Bluesky._session[1] > time.time():
                return Bluesky._session[0]
            response = ctx.fetcher.post(
                "https://bsky.social/xrpc/com.atproto.server.createSession", json={"identifier": handle, "password": password}
            )
            if response.status_code >= 400:
                return None
            Bluesky._session = (response.json().get("accessJwt", ""), time.time() + 3600)
            return Bluesky._session[0]

    def search(self, query, ctx: SearchContext):
        params = {"q": query, "limit": str(min(50, ctx.limit * 2)), "sort": "top", "lang": "en"}
        token = self._token(ctx)
        if token:
            data = ctx.fetcher.get_json("https://bsky.social/xrpc/app.bsky.feed.searchPosts", params=params, headers={"Authorization": f"Bearer {token}"})
        else:
            data = ctx.fetcher.get_json("https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts", params=params)
        items = []
        for post in data.get("posts", []):
            record = post.get("record") or {}
            author = post.get("author") or {}
            rkey = (post.get("uri") or "").rsplit("/", 1)[-1]
            handle = author.get("handle", "")
            if not (handle and rkey):
                continue
            items.append(
                RawItem(
                    url=f"https://bsky.app/profile/{handle}/post/{rkey}",
                    title=clean_text(record.get("text"), 140),
                    text=clean_text(record.get("text")),
                    platform="bluesky",
                    kind="social_post",
                    author=handle,
                    published_at=parse_date(record.get("createdAt")),
                    metrics={"likes": post.get("likeCount", 0), "shares": post.get("repostCount", 0), "comments": post.get("replyCount", 0)},
                )
            )
        return items


class Mastodon(Connector):
    key = "mastodon"
    name = "Mastodon (hashtags)"
    platform = "mastodon"
    cost = CostTier.FREE
    query_kind = "social"
    default_enabled = False
    description = "Public hashtag timelines on a Mastodon server. Small African audience; useful for tech and academic voices."
    setup = "Works out of the box with mastodon.social; change MASTODON_INSTANCE to use another server."
    terms = "Public posts on the Fediverse; respect instance rules."
    docs_url = "https://docs.joinmastodon.org/methods/timelines/"

    def search(self, query, ctx: SearchContext):
        instance = (ctx.config.get("MASTODON_INSTANCE") or "https://mastodon.social").rstrip("/")
        tag = "".join(ch for ch in query.replace("#", "").title() if ch.isalnum())[:60]
        if not tag:
            return []
        statuses = ctx.fetcher.get_json(f"{instance}/api/v1/timelines/tag/{tag}", params={"limit": str(min(40, ctx.limit * 2))})
        items = []
        for status in statuses if isinstance(statuses, list) else []:
            text = clean_text(status.get("content"))
            if not text or not status.get("url"):
                continue
            items.append(
                RawItem(
                    url=status["url"],
                    title=clean_text(text, 140),
                    text=text,
                    platform="mastodon",
                    kind="social_post",
                    author=(status.get("account") or {}).get("acct", ""),
                    published_at=parse_date(status.get("created_at")),
                    metrics={"likes": status.get("favourites_count", 0), "shares": status.get("reblogs_count", 0), "comments": status.get("replies_count", 0)},
                )
            )
        return items


class TelegramChannels(Connector):
    key = "telegram"
    name = "Telegram public channels"
    platform = "telegram"
    cost = CostTier.FREE
    query_kind = "social"
    requires = ("TELEGRAM_CHANNELS",)
    description = "Searches the public web preview (t.me/s/…) of channels you list - e.g. news channels. Very important in Ethiopia and growing elsewhere."
    setup = "List channel usernames (comma-separated, without @) in Settings → TELEGRAM_CHANNELS."
    terms = "Public channel previews only; no login, no private groups."
    docs_url = "https://telegram.org/faq_channels"

    def search(self, query, ctx: SearchContext):
        channels = [c.strip().lstrip("@") for c in (ctx.config.get("TELEGRAM_CHANNELS") or "").split(",") if c.strip()]
        items = []
        for channel in channels[:15]:
            try:
                response = ctx.fetcher.get(f"https://t.me/s/{channel}", params={"q": query})
            except FetchError as exc:
                ctx.log(f"Telegram {channel}: {exc}")
                continue
            if response.status_code >= 400:
                continue
            items.extend(parse_telegram(response.text, channel)[: ctx.limit])
        return items


def parse_telegram(html: str, channel: str) -> list[RawItem]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for wrap in soup.select("div.tgme_widget_message"):
        text_el = wrap.select_one("div.tgme_widget_message_text")
        if not text_el:
            continue
        text = clean_text(text_el.get_text("\n"))
        link_el = wrap.select_one("a.tgme_widget_message_date")
        time_el = wrap.select_one("time")
        views_el = wrap.select_one("span.tgme_widget_message_views")
        url = link_el.get("href", "") if link_el else ""
        if not url:
            post_id = wrap.get("data-post")
            url = f"https://t.me/{post_id}" if post_id else ""
        if not url:
            continue
        out.append(
            RawItem(
                url=url,
                title=clean_text(text, 140),
                text=text,
                platform="telegram",
                kind="social_post",
                author=channel,
                published_at=parse_date(time_el.get("datetime") if time_el else None),
                metrics={"views": _parse_count(views_el.get_text() if views_el else "")},
            )
        )
    return out


def _parse_count(text: str) -> int:
    text = (text or "").strip().upper()
    try:
        if text.endswith("K"):
            return int(float(text[:-1]) * 1_000)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1_000_000)
        return int(float(text)) if text else 0
    except ValueError:
        return 0


class SocialIndex(Connector):
    """Public posts as indexed by search engines (site: queries)."""

    key = "social_index"
    name = "X, Facebook, LinkedIn, TikTok & Instagram (search-indexed)"
    platform = "x"
    cost = CostTier.FREE
    query_kind = "social"
    description = (
        "Finds public posts from platforms that have no free API by searching the web index with site: filters. "
        "Free and terms-friendly, but returns snippets (not full threads) and favours popular posts."
    )
    setup = "Works out of the box. For full X/Facebook/LinkedIn data use the paid connectors below."
    terms = "Uses only what search engines publicly index; no logins, no scraping behind walls."

    SITES = [
        ("x", "x.com"),
        ("facebook", "facebook.com"),
        ("linkedin", "linkedin.com/posts"),
        ("tiktok", "tiktok.com"),
        ("instagram", "instagram.com"),
    ]

    def _ddgs(self):
        from ddgs import DDGS

        return DDGS(timeout=15)

    def search(self, query, ctx: SearchContext):
        country = ctx.country.name if ctx.country.code != "XX" else ""
        engine = self._ddgs()
        items = []
        per_site = max(3, ctx.limit // 2)
        for platform, site in self.SITES:
            q = f"site:{site} {query} {country}".strip()
            try:
                results = engine.text(q, region="wt-wt", safesearch="moderate", timelimit=ctx.timelimit, max_results=per_site)
            except Exception as exc:  # ddgs raises its own exception types
                ctx.log(f"{platform} index search: {exc.__class__.__name__}")
                continue
            for r in results or []:
                url = r.get("href") or ""
                if site.split("/")[0] not in url:
                    continue
                items.append(
                    RawItem(
                        url=url,
                        title=clean_text(r.get("title"), 300),
                        text=clean_text(r.get("body")),
                        platform=platform,
                        kind="snippet",
                        meta={"via": "search_index"},
                    )
                )
        return items


class XApi(Connector):
    key = "x_api"
    name = "X (Twitter) official API"
    platform = "x"
    cost = CostTier.PAID
    query_kind = "social"
    default_enabled = False
    requires = ("X_BEARER_TOKEN",)
    description = "Recent-search endpoint of the official X API v2 (last 7 days)."
    setup = "Requires a developer account and a paid/pay-per-use plan; paste the Bearer token in Settings."
    price_note = "Read access is paid; check developer.x.com for current pay-per-use pricing."
    terms = "X Developer Agreement."
    docs_url = "https://developer.x.com/en/docs/x-api/tweets/search/api-reference/get-tweets-search-recent"

    def search(self, query, ctx: SearchContext):
        params = {
            "query": f"({query}) -is:retweet lang:en",
            "max_results": str(max(10, min(100, ctx.limit * 3))),
            "tweet.fields": "created_at,public_metrics,lang,author_id",
            "expansions": "author_id",
            "user.fields": "username",
        }
        data = ctx.fetcher.get_json(
            "https://api.x.com/2/tweets/search/recent", params=params, headers={"Authorization": f"Bearer {ctx.config['X_BEARER_TOKEN']}"}
        )
        users = {u.get("id"): u.get("username", "") for u in (data.get("includes") or {}).get("users", [])}
        items = []
        for tweet in data.get("data", []):
            metrics = tweet.get("public_metrics") or {}
            items.append(
                RawItem(
                    url=f"https://x.com/i/web/status/{tweet.get('id')}",
                    title=clean_text(tweet.get("text"), 140),
                    text=clean_text(tweet.get("text")),
                    platform="x",
                    kind="social_post",
                    author=users.get(tweet.get("author_id"), ""),
                    published_at=parse_date(tweet.get("created_at")),
                    metrics={"likes": metrics.get("like_count", 0), "shares": metrics.get("retweet_count", 0), "comments": metrics.get("reply_count", 0), "views": metrics.get("impression_count", 0)},
                )
            )
        return items


class TwitterApiIo(Connector):
    key = "twitterapi_io"
    name = "twitterapi.io (X data)"
    platform = "x"
    cost = CostTier.PAID
    query_kind = "social"
    default_enabled = False
    requires = ("TWITTERAPI_IO_KEY",)
    description = "Third-party X/Twitter search API with pay-as-you-go pricing - one of the cheapest ways to get full tweets."
    setup = "Create an account at twitterapi.io and paste the API key in Settings."
    price_note = "Pay-as-you-go, roughly $0.15 per 1,000 tweets (check current pricing)."
    terms = "Third-party data provider; review their terms and X's policies for your use case."
    docs_url = "https://docs.twitterapi.io/"

    def search(self, query, ctx: SearchContext):
        data = ctx.fetcher.get_json(
            "https://api.twitterapi.io/twitter/tweet/advanced_search",
            params={"query": f"{query} lang:en -filter:retweets", "queryType": "Top"},
            headers={"X-API-Key": ctx.config["TWITTERAPI_IO_KEY"]},
        )
        items = []
        for tweet in (data.get("tweets") or [])[: ctx.limit * 3]:
            author = tweet.get("author") or {}
            items.append(
                RawItem(
                    url=tweet.get("url") or f"https://x.com/i/web/status/{tweet.get('id')}",
                    title=clean_text(tweet.get("text"), 140),
                    text=clean_text(tweet.get("text")),
                    platform="x",
                    kind="social_post",
                    author=author.get("userName", ""),
                    published_at=parse_date(tweet.get("createdAt")),
                    metrics={"likes": tweet.get("likeCount", 0), "shares": tweet.get("retweetCount", 0), "comments": tweet.get("replyCount", 0), "views": tweet.get("viewCount", 0)},
                )
            )
        return items


APIFY_DEFAULT_ACTORS = {
    "x": ("apidojo~tweet-scraper", {"searchTerms": ["{query}"], "maxItems": "{limit}", "sort": "Top"}),
    "tiktok": ("clockworks~tiktok-scraper", {"searchQueries": ["{query}"], "resultsPerPage": "{limit}"}),
    "linkedin": ("harvestapi~linkedin-post-search", {"searchQueries": ["{query}"], "maxPosts": "{limit}"}),
    "facebook": ("apify~facebook-posts-scraper", {"startUrls": [{"url": "{page}"}], "resultsLimit": "{limit}"}),
}

TEXT_KEYS = ("text", "fullText", "full_text", "postText", "message", "content", "caption", "description", "commentary", "title")
URL_KEYS = ("url", "postUrl", "post_url", "link", "permalink", "webVideoUrl", "shareUrl", "twitterUrl")
DATE_KEYS = ("createdAt", "created_at", "createTimeISO", "time", "timestamp", "date", "publishedAt", "postedAt", "createTime")
LIKE_KEYS = ("likeCount", "likes", "favorite_count", "diggCount", "reactionsCount", "numLikes", "likesCount")
SHARE_KEYS = ("retweetCount", "shareCount", "shares", "reposts", "numShares", "repostCount")
COMMENT_KEYS = ("replyCount", "commentCount", "comments", "numComments", "commentsCount")


def _first(item: dict, keys) -> object:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", []):
            return value
    return None


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _fill(template, values: dict):
    if isinstance(template, str):
        if template in ("{limit}",):
            return int(values["limit"])
        return template.format(**values)
    if isinstance(template, list):
        return [_fill(v, values) for v in template]
    if isinstance(template, dict):
        return {k: _fill(v, values) for k, v in template.items()}
    return template


class Apify(Connector):
    key = "apify"
    name = "Apify scrapers (X, TikTok, LinkedIn, Facebook)"
    platform = "x"
    cost = CostTier.FREEMIUM
    query_kind = "social"
    default_enabled = False
    requires = ("APIFY_TOKEN",)
    description = "Runs community-maintained scrapers on Apify's cloud and imports public posts from platforms without free APIs."
    setup = (
        "Create a free Apify account (includes monthly free platform credit), copy your API token into Settings. "
        "Optional: override actors with APIFY_ACTOR_X / _TIKTOK / _LINKEDIN / _FACEBOOK and list Facebook page URLs in APIFY_FACEBOOK_PAGES."
    )
    price_note = "Free monthly credit covers small studies; beyond that typically a few dollars per thousand posts (varies by actor)."
    terms = "Scraping public data may still conflict with a platform's terms. Use for public-interest research, minimise personal data, and follow your ethics approval."
    docs_url = "https://docs.apify.com/api/v2"

    def search(self, query, ctx: SearchContext):
        token = ctx.config["APIFY_TOKEN"]
        items: list[RawItem] = []
        pages = [p.strip() for p in os.environ.get("APIFY_FACEBOOK_PAGES", "").split(",") if p.strip()]
        for platform, (default_actor, template) in APIFY_DEFAULT_ACTORS.items():
            actor = os.environ.get(f"APIFY_ACTOR_{platform.upper()}", default_actor)
            if not actor or actor.lower() == "off":
                continue
            targets = pages[:3] if platform == "facebook" else [None]
            if platform == "facebook" and not pages:
                continue
            for page in targets:
                payload = _fill(template, {"query": query, "limit": min(50, ctx.limit * 2), "page": page or ""})
                try:
                    response = ctx.fetcher.post(
                        f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                        params={"token": token, "timeout": "120", "format": "json"},
                        json=payload,
                        timeout=150,
                        retries=0,
                    )
                except FetchError as exc:
                    ctx.log(f"Apify {platform}: {exc}")
                    continue
                if response.status_code >= 400:
                    ctx.log(f"Apify {platform} ({actor}): HTTP {response.status_code} {response.text[:120]}")
                    continue
                try:
                    rows = response.json()
                except json.JSONDecodeError:
                    continue
                for row in rows if isinstance(rows, list) else []:
                    if not isinstance(row, dict):
                        continue
                    text = clean_text(str(_first(row, TEXT_KEYS) or ""))
                    url = str(_first(row, URL_KEYS) or "")
                    if not text or not url:
                        continue
                    author = row.get("author") or row.get("authorMeta") or row.get("user") or {}
                    author_name = author.get("userName") or author.get("name") or author.get("username") if isinstance(author, dict) else str(author)
                    items.append(
                        RawItem(
                            url=url,
                            title=clean_text(text, 140),
                            text=text,
                            platform=platform,
                            kind="social_post",
                            author=str(author_name or ""),
                            published_at=parse_date(_first(row, DATE_KEYS)),
                            metrics={
                                "likes": _int(_first(row, LIKE_KEYS)),
                                "shares": _int(_first(row, SHARE_KEYS)),
                                "comments": _int(_first(row, COMMENT_KEYS)),
                            },
                            meta={"actor": actor},
                        )
                    )
        return items


def anonymize(author: str) -> str:
    """Replace a handle with a keyed one-way pseudonym (stable within one installation)."""
    if not author:
        return ""
    from django.conf import settings

    digest = hmac.new(settings.SECRET_KEY.encode("utf-8"), author.strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()
    return "user_" + digest[:10]

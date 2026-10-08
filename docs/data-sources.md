# Data sources

Baraza follows one rule: **free first, then the lowest cost, and never behind a login.** Every connector can be switched on or off per research run, and the **Data sources** page in the app shows which ones are ready, what they cost and how to set them up.

> Prices and free quotas change often. Figures below are indicative; always check the provider's current pricing page.

## At a glance

| Connector | Platform | Cost | Setup | Default |
|---|---|---|---|---|
| DuckDuckGo web search | Web | Free | None | On |
| DuckDuckGo news search | News | Free | None | On |
| Google News (RSS) | News, country editions | Free | None | On |
| GDELT | Global news index | Free | None | On |
| Wikipedia | Background | Free | None | On |
| Reddit | Reddit | Free | None (optional free API app) | On |
| Search-indexed social posts | X, Facebook, LinkedIn, TikTok, Instagram | Free | None | On |
| YouTube comments | YouTube | Free with key | API key | On when key set |
| Bluesky | Bluesky | Free | None (optional app password) | On |
| Telegram public channels | Telegram | Free | Channel list | On when channels set |
| Your RSS feeds | Any | Free | Feed list | On when feeds set |
| Mastodon hashtags | Mastodon | Free | None | Off |
| SearXNG | Web, news | Free, self-hosted | Docker | On when URL set |
| Tavily | Web | Free credits, then paid | API key | Off |
| Brave Search API | Web | Free credits, then paid | API key | Off |
| Serper | Google News | Free credits, then paid | API key | Off |
| Apify | X, TikTok, LinkedIn, Facebook | Free monthly credit, then paid | API token | Off |
| twitterapi.io | X | Paid (pay as you go) | API key | Off |
| X API | X | Paid | Bearer token | Off |
| Manual: paste, URL, upload | Anything | Free | None | Always |

## Getting social media data: platform by platform

### X (Twitter)

X is the most important platform for Kenyan policy debate (#RejectFinanceBill2024, #OccupyParliament) and the hardest to access for free since 2023.

1. **Free: search-indexed posts (on by default).** Baraza searches public search engines for `site:x.com <topic> <country>`. You get the text snippet of popular public posts. No account, no scraping of X itself. Coverage favours popular posts and recent indexing.
2. **Free: what you already have.** Export posts you collected yourself, or analytics for accounts you manage, as CSV and upload them (Research → Add evidence → Upload). Any CSV with a `text` (or `tweet`, `content`, `body`, `message`) column works; `date`, `likes`, `platform` and `url` columns are optional.
3. **Free for eligible researchers.** Under the EU Digital Services Act (Article 40), vetted researchers studying systemic risks in the EU can request data access. This rarely fits an African policy study, but is worth knowing.
4. **Lowest cost: twitterapi.io.** A third-party X search API with pay-as-you-go pricing (in the order of USD 0.15 per 1,000 tweets at the time of writing). A deep research run uses a few hundred tweets, i.e. cents.
5. **Low cost: Apify.** The default actor `apidojo~tweet-scraper` runs on Apify's free monthly platform credit for small studies.
6. **Official X API.** Read access is paid; X has moved toward pay-per-use pricing. Use it if your institution already has access.

Not recommended: Nitter instances and old scraping libraries such as snscrape stopped working reliably after X closed guest access; logging in with a personal account to scrape breaks X's terms and can get the account banned.

### Facebook

1. **Free: search-indexed public posts and pages (on by default).**
2. **Free for researchers: Meta Content Library.** Meta offers academic and non-profit researchers free access to public content from Facebook and Instagram pages, groups and profiles through the Meta Content Library and API (replacing CrowdTangle, which closed in August 2024). Access is by application through ICPSR (University of Michigan). An MSc or PhD project with ethics approval is a good candidate. Export results as CSV and upload them to Baraza.
3. **Free: Page Insights.** If you manage the page (for example a ministry's page), export its posts and comments.
4. **Low cost: Apify.** Set `APIFY_FACEBOOK_PAGES` to public page URLs (for example national newspapers' pages) to collect posts with the `apify~facebook-posts-scraper` actor.

Private groups and personal profiles are never collected.

### LinkedIn

LinkedIn has no public research API and its user agreement prohibits scraping, so be conservative.

1. **Free: search-indexed public posts (on by default)** via `site:linkedin.com/posts`. Good for professional, business-association and civil-service voices.
2. **Low cost: Apify** with the `harvestapi~linkedin-post-search` actor (public posts by keyword, no login cookies). Review the actor's terms and your ethics approval first.
3. Courts in the United States have treated scraping of *public* data differently from breaching a user agreement (hiQ Labs v. LinkedIn ended with hiQ found in breach of contract). Collect only what you need, do not use logged-in sessions, and minimise personal data.

### TikTok and Instagram

1. **Free: search-indexed posts (on by default).**
2. **Free for researchers:** the TikTok Research API (by application; eligibility has been expanding beyond the US and Europe) and the Meta Content Library for Instagram.
3. **Low cost:** Apify actors (`clockworks~tiktok-scraper` by default).

### YouTube (strongly recommended)

Comments under news videos from local TV stations (Citizen TV, NTV, KTN, Channels TV, eNCA, JoyNews...) are one of the richest free sources of public opinion in Africa. Create a free API key: Google Cloud Console → new project → enable **YouTube Data API v3** → Credentials → API key. The free quota is 10,000 units per day; a search costs 100 units and a page of comments 1 unit, so a standard research run uses a few hundred to a couple of thousand units. Add `YOUTUBE_API_KEY` in Settings. Academic researchers can also apply to the YouTube Researcher Program.

### Reddit

Works out of the box through public JSON (r/Kenya, r/Nairobi, r/Nigeria, r/Lagos, r/southafrica, r/ghana, r/Uganda, r/tanzania, r/Rwanda, r/Ethiopia, plus Reddit-wide search). For reliability create a free "script" app at reddit.com/prefs/apps and set `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`. Reddit's Data API terms allow free non-commercial research use within rate limits.

### Telegram

Telegram channels are important in Ethiopia and growing elsewhere. List public channel usernames (without `@`) in `TELEGRAM_CHANNELS`; Baraza searches their public web preview (`t.me/s/<channel>?q=...`). No login, no private groups.

### WhatsApp

WhatsApp is where much African political conversation happens, but it is private and end-to-end encrypted, and Baraza does not collect it. Instead, the simulation models WhatsApp-style groups (family, neighbours, chamas, faith and work groups), including forwarding of rumours. If people donate messages or a fact-checking tipline shares anonymised data with consent, paste or upload it as evidence.

### Bluesky and Mastodon

Small African audiences but useful for academic, tech and journalist voices. Bluesky search works without an account; if unauthenticated search is refused, create a free app password (Settings → App passwords on Bluesky) and set `BLUESKY_HANDLE` and `BLUESKY_APP_PASSWORD`.

## News and the web

* **DuckDuckGo (ddgs)**: an open-source metasearch library that queries several engines. Free, no key. If you see rate-limit errors, slow down or use SearXNG.
* **Google News RSS**: country editions (Kenya, Nigeria, South Africa, Ghana, Uganda, Tanzania, Ethiopia...), excellent for local outlets.
* **GDELT DOC 2.0**: worldwide news monitoring filtered to the country's own media; free; one request every five seconds.
* **Your RSS feeds**: newspapers, blogs, government press releases, parliamentary feeds.
* **SearXNG (self-hosted)**: the most reliable free option for heavy use. With Docker: `docker compose --profile searxng up -d`, then set `SEARXNG_URL=http://localhost:8888` (or `http://searxng:8080` from inside Docker Compose). The included `deploy/searxng/settings.yml` enables the JSON API; change its `secret_key`.
* **Tavily, Brave, Serper**: commercial search APIs with free monthly credits or sign-up credits; useful when free metasearch is rate-limited.
* **Full articles** are fetched only when robots.txt allows it, with an honest user agent and per-site rate limits. JavaScript-heavy pages fall back to the free Jina Reader service (`JINA_API_KEY` raises its limits).

## Suggested setups

| Goal | Connectors |
|---|---|
| Zero cost, zero setup | Defaults (web, news, Google News, GDELT, Wikipedia, Reddit, search-indexed social, Bluesky) |
| Zero cost, best quality | Defaults + YouTube key + Reddit app + Telegram channels + local news RSS + SearXNG |
| Academic study of X/Facebook | Above + Meta Content Library export (CSV upload) + twitterapi.io or Apify for X |
| Government communications team | Above + exports from your own pages' analytics |

## Responsible collection

* Baraza never logs in to personal accounts, bypasses paywalls or access controls, or posts anything.
* Authors of collected posts are pseudonymised by default (`ANONYMIZE_SOCIAL_AUTHORS=True`), and only the text needed for analysis is stored.
* robots.txt is respected for page fetching (`RESPECT_ROBOTS_TXT=True`).
* Collected data stays in your own database. Delete projects you no longer need.
* Comply with the platform's terms, your country's data protection law and your institution's ethics approval. See [ethics.md](ethics.md).

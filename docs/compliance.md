# Compliance Audit

Every target site is checked against `robots.txt` and Terms of Service before any scraper is
written. This table is the source of truth for that decision. Sites are drawn from four tiers —
T0 sandbox (deterministic CI targets), T1 public/permissive (real volume, permissive access), T2
JS-heavy (real SPA/GraphQL rendering), T3 messy HTML (inconsistent legacy markup) — and the final
six sites are selected from each tier's candidates as each is audited.

| Site | URL | robots.txt allows? | Crawl-delay | ToS notes | Data type | Decision |
|---|---|---|---|---|---|---|
| Books to Scrape | https://books.toscrape.com/ | Yes — `robots.txt` returns HTTP 404 (does not exist); no `Disallow` rules exist to violate | None specified | Homepage banner states verbatim: *"This is a demo website for web scraping purposes. Prices and ratings here were randomly assigned and have no real meaning."* Built and maintained by Zyte (formerly Scrapinghub) explicitly as a public scraping sandbox; no ToS page found restricting automated access. | Fictional book catalog: title, price, star rating, stock availability, description, category, UPC — all synthetic per the site's own disclaimer | **APPROVED** — T0 sandbox, primary Phase 1 target |
| Quotes to Scrape (JS + scroll) | https://quotes.toscrape.com/js/ and /scroll | Yes — `robots.txt` returns HTTP 404 (does not exist); same operator/setup as Books to Scrape | None specified | Same Zyte-run public scraping-practice site as Books to Scrape; the `/js/` variant renders quotes via an embedded JS array + `document.write`, the `/scroll` variant loads quotes via scroll-triggered AJAX (`/api/quotes`) — both purpose-built to be scraped. | Fictional quotes: text, author, tags — synthetic demo data | **APPROVED** — T0 sandbox, Phase 2.2 targets (JS-rendered + infinite scroll) |
| Wikipedia (English) | https://en.wikipedia.org/wiki/\<Title\> | Partially — generic `User-agent: *` block disallows `/w/`, `/api/`, and `/wiki/Special:`, but does **not** disallow ordinary `/wiki/<Title>` article pages, which is the only path this scraper uses. No `Crawl-delay` set for `*` (only for specific named bots). | None for `*` | Wikimedia Foundation Terms of Use permit automated use of the Project Websites as long as it is not "abusive or disruptive," does not place undue burden on the service, and follows the Robot Policy / User-Agent Policy — satisfied by an honest User-Agent and a conservative rate limit (1 rps). Live-tested: the site does return `429` under rapid unthrottled requests, confirming the rate limit is enforced and must be respected, not just declared. | Real encyclopedia articles: title, short description, lead-paragraph summary — genuine, large-scale public content | **APPROVED** — T1, Phase 2.7 target. Discovery via a curated list of ~30 article titles (all verified individually), not a search/API endpoint, since `/w/api.php` and `/wiki/Special:Search` are both disallowed. |
| ~~arXiv~~ (`export.arxiv.org`) | https://export.arxiv.org/api/query | **No** — `robots.txt` is `User-agent: * / Disallow: /`, i.e. the entire API subdomain is disallowed for all automated agents. | — | — | Bibliographic/abstract metadata via the official Atom API | **REJECTED** — a full-site `Disallow: /` is unambiguous; there is no compliant path on this subdomain regardless of ToS. |
| ~~OpenLibrary~~ | https://openlibrary.org/subjects/\<name\> | Technically yes for `/subjects/*` (only `/search/subjects`, `/api`, and `/search` are disallowed) | 10s for named bots | Not the blocker — the site itself returned `HTTP 303` to `/verify_human?next=...` on a plain, honest-UA request, i.e. an active bot-verification challenge in front of the page, independent of what `robots.txt` permits. | Book/work catalog metadata | **REJECTED** — Hard Rule 4 (never target sites with active anti-bot/verification challenges), even though `robots.txt` alone would have allowed it. |
| ~~PyPI~~ | https://pypi.org/pypi/\<name\>/json | **No** for the JSON API — `robots.txt` explicitly disallows `/pypi/*/json`, `/search*`, and `/simple/`. | — | — | Package metadata | **REJECTED** — the one thing that would justify "API + HTML fallback" (the JSON API) is exactly what's disallowed; falls back to only `/project/<name>/` HTML with no compliant bulk-discovery path. |
| ~~Project Gutenberg~~ | https://www.gutenberg.org/ebooks/\<id\> | Yes technically — only `/ebooks/search` is disallowed | None specified | Terms of Use are explicit and unambiguous: *"This website is intended for human users only. Any perceived use of automated tools to access this website will result in a temporary or permanent block of your IP address."* It directs automated/bulk access to a separate mirror network instead of the main site. | Public-domain book bibliographic records | **REJECTED** — `robots.txt` alone would have allowed it, but the ToS explicitly forbids automated access to this specific host; scraping the main site anyway would violate CLAUDE.md's compliance standard even without a robots.txt violation. |
| T2 candidate (Discourse forum, GitHub Topics, or public-GraphQL SPA) | TODO | TODO | TODO | TODO | TODO | TODO — not yet audited; exact site not yet selected from the tier's candidate pool |
| T3 candidate (Wayback Machine CDX API archive pages) | TODO | TODO | TODO | TODO | TODO | TODO — not yet audited |

## Audit method

1. Fetch `<site>/robots.txt` directly (not from memory) and record the actual response.
2. Note any `Crawl-delay` directive; if present, it is enforced by the fetcher (see
   `docs/adr/0003-two-fetch-strategies.md`) and is never bypassed.
3. Read the site's ToS / acceptable-use page, if one exists, for language addressing automated
   access, scraping, or data reuse.
4. Record the decision as `APPROVED`, `REJECTED`, or `APPROVED WITH CONDITIONS` (state the
   condition — e.g. rate limit, endpoint restriction).
5. Never guess a row. An unaudited site stays `TODO` until it has actually been checked.

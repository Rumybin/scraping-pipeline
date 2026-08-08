# Compliance Audit

Every target site is checked against `robots.txt` and Terms of Service before any scraper is
written. This table is the source of truth for that decision. Sites are drawn from four tiers —
T0 sandbox (deterministic CI targets), T1 public/permissive (real volume, permissive access), T2
JS-heavy (real SPA/GraphQL rendering), T3 messy HTML (inconsistent legacy markup) — and the final
six sites are selected from each tier's candidates as each is audited.

| Site | URL | robots.txt allows? | Crawl-delay | ToS notes | Data type | Decision |
|---|---|---|---|---|---|---|
| Books to Scrape | https://books.toscrape.com/ | Yes — `robots.txt` returns HTTP 404 (does not exist); no `Disallow` rules exist to violate | None specified | Homepage banner states verbatim: *"This is a demo website for web scraping purposes. Prices and ratings here were randomly assigned and have no real meaning."* Built and maintained by Zyte (formerly Scrapinghub) explicitly as a public scraping sandbox; no ToS page found restricting automated access. | Fictional book catalog: title, price, star rating, stock availability, description, category, UPC — all synthetic per the site's own disclaimer | **APPROVED** — T0 sandbox, primary Phase 1 target |
| T0 candidate (`quotes.toscrape.com/js/`) | TODO | TODO | TODO | TODO | TODO | TODO — not yet audited |
| T1 candidate (Wikipedia/Wikidata, arXiv, PyPI, OpenLibrary, or data.go.id/BPS) | TODO | TODO | TODO | TODO | TODO | TODO — not yet audited; exact site not yet selected from the tier's candidate pool |
| T1 candidate (see above pool) | TODO | TODO | TODO | TODO | TODO | TODO — not yet audited; exact site not yet selected from the tier's candidate pool |
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

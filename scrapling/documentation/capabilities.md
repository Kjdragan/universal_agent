# Scrapling Capabilities

## Fetch Modes

- `basic` - fastest; plain HTTP-level scraping
- `dynamic` - browser-assisted rendering / anti-bot resilience
- `stealthy` - highest anti-bot posture, slowest
- adaptive - starts lower and escalates when blocked

## Input Support

- JSON list of URLs
- JSON object with `urls` + `options`
- Search-result payloads with recursive URL extraction from fields like `url`, `link`, `href`, `source_url`

## Output

- One markdown file per URL
- Metadata block includes URL, domain, status, fetcher tier, scrape time, and selected job metadata

## Anti-Bot Behavior

- Detects bot/challenge responses from status/content markers
- Can escalate to stronger fetch tiers
- Can return partial content when all tiers are blocked

## Current Limits

- Paywalled content remains mostly inaccessible
- Some menu/taxonomy residue may still appear for complex sites
- Historical run reports may contain absolute paths from older directory naming

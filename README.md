# RAM-Global News Feed Backend

Automated backend that powers the "Breaking News" malaria widget on ram-global.org.

## How it works

1. A GitHub Actions workflow (`.github/workflows/update-news.yml`) runs every 20 minutes.
2. It executes `fetch_news.py`, which:
   - Queries Google News RSS restricted to a curated list of trusted domains (WHO, Reuters, AP, BBC, NPR, CIDRAP, Devex, The Lancet, Malaria No More, STAT News, Nature, The Guardian, CNN, NIH, CDC).
   - Filters headlines for genuine malaria relevance (not passing mentions).
   - Deduplicates near-identical wire stories.
   - Guarantees a minimum of 6 articles by progressively relaxing the recency window and, as a last resort, the relevance filter, only as much as needed.
3. The result is written to `news.json` and committed back to this repository automatically.
4. The website widget fetches `news.json` directly from `raw.githubusercontent.com`, which serves proper CORS headers out of the box, no proxy required.

## Editing the trusted source list

Open `fetch_news.py` and edit the `TRUSTED_DOMAINS` list.

## Manually triggering an update

Go to the Actions tab in this repository and run the "Update Malaria News Feed" workflow manually.

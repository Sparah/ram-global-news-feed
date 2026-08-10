# RAM-Global News Feed Backend

Automated backend that powers the "Breaking News" malaria widget on ram-global.org.

## How it works

1. A GitHub Actions workflow (`.github/workflows/update-news.yml`) runs every 2 hours.
2. It executes `fetch_news.py`, which combines two layers:
   - **Curation layer (breadth):** Google News RSS restricted to a curated list of trusted domains (WHO, Reuters, AP, BBC, NPR, CIDRAP, Devex, The Lancet, Malaria No More, STAT News, Nature, The Guardian, CNN, NIH, CDC). These articles use a branded gradient card since Google News redirect links cannot be resolved to real images server-side (Google blocks non-browser resolution).
   - **Image layer (real photos):** Direct RSS feeds from BBC Health, The Guardian Health, and NPR Health, which embed real `<media:thumbnail>`/`<enclosure>` images natively. These are filtered down to malaria-relevant stories only, using the same headline-relevance check as the curation layer.
3. Both layers are merged and deduplicated by title. If the same story appears in both layers, the version with a real image wins.
4. A minimum of 6 articles is guaranteed by progressively relaxing the recency window and, as a last resort, the relevance filter.
5. The result is written to `news.json`, committed back to the repo, and the jsDelivr CDN cache is explicitly purged so the website widget always reflects the latest run within moments.

## Why not scrape images from every source?

Google News wraps every article link in an obfuscated redirect that requires JavaScript to resolve -- plain server-side HTTP requests cannot reach the real publisher page, so `og:image` scraping was tested and failed (0% success rate). Some trusted sources (WHO, CIDRAP, Malaria No More) also don't publish a simple, stable public RSS feed with images, so rather than guess at fragile URLs, only verified, stable, image-embedding feeds were added directly.

## Adding another direct image-feed source

Add an object to `DIRECT_IMAGE_FEEDS` in `fetch_news.py` with a `name` and `url`. It will automatically be filtered for malaria relevance and merged into the output.

## Editing the trusted source list (curation layer)

Open `fetch_news.py` and edit the `TRUSTED_DOMAINS` list.

## Manually triggering an update

Go to the Actions tab in this repository and run the "Update Malaria News Feed" workflow manually.

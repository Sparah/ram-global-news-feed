import json
import re
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

TRUSTED_DOMAINS = [
    "who.int", "reuters.com", "apnews.com", "bbc.com", "npr.org",
    "cidrap.umn.edu", "devex.com", "thelancet.com", "malarianomore.org",
    "statnews.com", "nature.com", "theguardian.com", "cnn.com", "nih.gov", "cdc.gov"
]

RELEVANT_KEYWORDS = ["malaria", "plasmodium", "anti-malarial", "antimalarial"]
IDEAL_MAX_AGE_DAYS = 30
MIN_ARTICLES = 6
MAX_ARTICLES = 18
USER_AGENT = "Mozilla/5.0 (compatible; RAMGlobalNewsBot/1.0; +https://github.com/Sparah/ram-global-news-feed)"
IMAGE_FETCH_TIMEOUT = 8

OG_IMAGE_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']', re.IGNORECASE),
]


def build_trusted_url():
    site_filter = "+OR+".join(f"site:{d}" for d in TRUSTED_DOMAINS)
    query = f"malaria+({site_filter})"
    return f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def build_fallback_url():
    return "https://news.google.com/rss/search?q=malaria&hl=en-US&gl=US&ceid=US:en"


def fetch_feed(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def try_resolve_image(article_link):
    """
    Attempt to follow the Google News redirect link to the real publisher
    page and extract its og:image (falling back to twitter:image).
    Returns None on any failure -- caller falls back to the gradient card.
    """
    try:
        req = urllib.request.Request(article_link, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=IMAGE_FETCH_TIMEOUT) as resp:
            final_url = resp.geturl()

            if "news.google.com" in final_url:
                return None

            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                return None

            raw = resp.read(300000)
            html = raw.decode("utf-8", errors="ignore")

        for pattern in OG_IMAGE_PATTERNS:
            match = pattern.search(html)
            if match:
                image_url = match.group(1).strip()
                if image_url.startswith("http"):
                    return image_url
        return None
    except Exception:
        return None


def parse_feed(xml_bytes):
    articles = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return articles

    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pubdate_el = item.find("pubDate")
        source_el = item.find("source")

        raw_title = title_el.text.strip() if title_el is not None and title_el.text else ""
        link = link_el.text.strip() if link_el is not None and link_el.text else ""
        pubdate_raw = pubdate_el.text.strip() if pubdate_el is not None and pubdate_el.text else ""
        source_name = source_el.text.strip() if source_el is not None and source_el.text else ""

        if not raw_title or not link or not pubdate_raw:
            continue

        if not source_name:
            parts = raw_title.split(" - ")
            source_name = parts[-1].strip() if len(parts) > 1 else "News Source"

        title = raw_title
        suffix = f" - {source_name}"
        if title.endswith(suffix):
            title = title[: -len(suffix)]

        try:
            pub_dt = parsedate_to_datetime(pubdate_raw)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        age_days = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 86400.0
        lower_title = title.lower()
        relevant = any(kw in lower_title for kw in RELEVANT_KEYWORDS)

        articles.append({
            "title": title,
            "link": link,
            "source": source_name,
            "pubDate": pub_dt.isoformat(),
            "ageDays": round(age_days, 2),
            "relevant": relevant,
        })

    return articles


def normalize_for_dedup(title):
    cleaned = re.sub(r"[^a-z0-9\s]", "", title.lower())
    return " ".join(cleaned.split()[:8])


def dedup(articles):
    seen = set()
    result = []
    for a in articles:
        key = normalize_for_dedup(a["title"])
        if key in seen:
            continue
        seen.add(key)
        result.append(a)
    return result


def select_articles(parsed):
    parsed.sort(key=lambda a: a["pubDate"], reverse=True)
    stale = False

    tier1 = [a for a in parsed if a["relevant"] and a["ageDays"] <= IDEAL_MAX_AGE_DAYS]
    if len(tier1) >= MIN_ARTICLES:
        return tier1[:MAX_ARTICLES], False

    tier2 = [a for a in parsed if a["relevant"]]
    if len(tier2) >= MIN_ARTICLES:
        return tier2[:MAX_ARTICLES], len(tier2) > len(tier1)

    tier3 = parsed[: max(MIN_ARTICLES, len(tier2))]
    return tier3[:MAX_ARTICLES], len(tier3) > len(tier2)


def main():
    all_articles = []

    try:
        trusted_xml = fetch_feed(build_trusted_url())
        all_articles.extend(parse_feed(trusted_xml))
    except Exception as e:
        print(f"Trusted feed fetch failed: {e}")

    all_articles = dedup(all_articles)

    if len(all_articles) < MIN_ARTICLES:
        try:
            fallback_xml = fetch_feed(build_fallback_url())
            fallback_articles = parse_feed(fallback_xml)
            all_articles = dedup(all_articles + fallback_articles)
        except Exception as e:
            print(f"Fallback feed fetch failed: {e}")

    selected, stale = select_articles(all_articles)

    resolved_count = 0
    output_articles = []
    for a in selected:
        image_url = try_resolve_image(a["link"])
        if image_url:
            resolved_count += 1
        output_articles.append({
            "title": a["title"],
            "link": a["link"],
            "source": a["source"],
            "pubDate": a["pubDate"],
            "image": image_url,
        })

    print(f"Resolved images for {resolved_count} of {len(output_articles)} articles")

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "stale": stale,
        "count": len(output_articles),
        "imagesResolved": resolved_count,
        "articles": output_articles,
    }

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(output_articles)} articles (stale={stale})")


if __name__ == "__main__":
    main()

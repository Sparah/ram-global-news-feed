import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

TRUSTED_DOMAINS = [
    "who.int", "reuters.com", "apnews.com", "bbc.com", "npr.org",
    "cidrap.umn.edu", "devex.com", "thelancet.com", "malarianomore.org",
    "statnews.com", "nature.com", "theguardian.com", "cnn.com", "nih.gov", "cdc.gov"
]

DIRECT_IMAGE_FEEDS = [
    {"name": "BBC Health", "url": "https://feeds.bbci.co.uk/news/health/rss.xml"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/society/health/rss"},
    {"name": "NPR Health", "url": "https://feeds.npr.org/1128/rss.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/health_medicine/malaria.xml"},
    {"name": "STAT News", "url": "https://www.statnews.com/feed/"},
]

RELEVANT_KEYWORDS = ["malaria", "plasmodium", "anti-malarial", "antimalarial"]
IDEAL_MAX_AGE_DAYS = 30
MIN_ARTICLES = 6
MAX_ARTICLES = 18
USER_AGENT = "Mozilla/5.0 (compatible; RAMGlobalNewsBot/1.0; +https://github.com/Sparah/ram-global-news-feed)"
IMAGE_FETCH_TIMEOUT = 8

MEDIA_NS = "{http://search.yahoo.com/mrss/}"
IMG_TAG_PATTERN = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

OG_IMAGE_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']', re.IGNORECASE),
]


def build_trusted_google_url():
    site_filter = "+OR+".join(f"site:{d}" for d in TRUSTED_DOMAINS)
    query = f"malaria+({site_filter})"
    return f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def build_fallback_google_url():
    return "https://news.google.com/rss/search?q=malaria&hl=en-US&gl=US&ceid=US:en"


def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def scrape_og_image(page_url):
    """
    Fetches a REAL (non-redirect-wrapped) article URL directly and scrapes
    its og:image / twitter:image meta tag. Safe to use for direct-feed
    articles since their links point straight to the publisher, unlike
    Google News's obfuscated redirect links.
    """
    try:
        req = urllib.request.Request(page_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=IMAGE_FETCH_TIMEOUT) as resp:
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


def parse_pubdate(raw):
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def normalize_for_dedup(title):
    cleaned = re.sub(r"[^a-z0-9\s]", "", title.lower())
    return " ".join(cleaned.split()[:8])


def is_relevant(title):
    lower = title.lower()
    return any(kw in lower for kw in RELEVANT_KEYWORDS)


def parse_google_news_feed(xml_bytes):
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

        pub_dt = parse_pubdate(pubdate_raw)
        if pub_dt is None:
            continue

        age_days = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 86400.0

        articles.append({
            "title": title,
            "link": link,
            "source": source_name,
            "pubDate": pub_dt.isoformat(),
            "ageDays": round(age_days, 2),
            "relevant": is_relevant(title),
            "image": None,
        })

    return articles


def extract_image_from_item(item):
    thumb = item.find(f"{MEDIA_NS}thumbnail")
    if thumb is not None and thumb.get("url"):
        return thumb.get("url")

    content = item.find(f"{MEDIA_NS}content")
    if content is not None and content.get("url"):
        return content.get("url")

    enclosure = item.find("enclosure")
    if enclosure is not None and enclosure.get("type", "").startswith("image") and enclosure.get("url"):
        return enclosure.get("url")

    description_el = item.find("description")
    if description_el is not None and description_el.text:
        match = IMG_TAG_PATTERN.search(description_el.text)
        if match:
            return match.group(1)

    return None


def parse_direct_feed(xml_bytes, source_name):
    articles = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return articles

    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pubdate_el = item.find("pubDate")

        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        link = link_el.text.strip() if link_el is not None and link_el.text else ""
        pubdate_raw = pubdate_el.text.strip() if pubdate_el is not None and pubdate_el.text else ""

        if not title or not link or not pubdate_raw:
            continue

        if not is_relevant(title):
            continue

        pub_dt = parse_pubdate(pubdate_raw)
        if pub_dt is None:
            continue

        age_days = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 86400.0

        image = extract_image_from_item(item)
        if not image:
            image = scrape_og_image(link)

        articles.append({
            "title": title,
            "link": link,
            "source": source_name,
            "pubDate": pub_dt.isoformat(),
            "ageDays": round(age_days, 2),
            "relevant": True,
            "image": image,
        })

    return articles


def dedup_merge(articles):
    seen = {}
    order = []
    for a in articles:
        key = normalize_for_dedup(a["title"])
        if key not in seen:
            seen[key] = a
            order.append(key)
        else:
            existing = seen[key]
            if not existing.get("image") and a.get("image"):
                existing["image"] = a["image"]
    return [seen[k] for k in order]


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
        trusted_xml = fetch_url(build_trusted_google_url())
        all_articles.extend(parse_google_news_feed(trusted_xml))
    except Exception as e:
        print(f"Google News trusted feed failed: {e}")

    for feed in DIRECT_IMAGE_FEEDS:
        try:
            xml_bytes = fetch_url(feed["url"])
            direct_articles = parse_direct_feed(xml_bytes, feed["name"])
            with_images = sum(1 for a in direct_articles if a.get("image"))
            print(f"{feed['name']}: {len(direct_articles)} malaria-relevant articles found, {with_images} with images")
            all_articles.extend(direct_articles)
        except Exception as e:
            print(f"Direct feed '{feed['name']}' failed: {e}")

    all_articles = dedup_merge(all_articles)

    if len([a for a in all_articles if a["relevant"]]) < MIN_ARTICLES:
        try:
            fallback_xml = fetch_url(build_fallback_google_url())
            fallback_articles = parse_google_news_feed(fallback_xml)
            all_articles = dedup_merge(all_articles + fallback_articles)
        except Exception as e:
            print(f"Google News fallback feed failed: {e}")

    selected, stale = select_articles(all_articles)
    images_found = sum(1 for a in selected if a.get("image"))

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "stale": stale,
        "count": len(selected),
        "imagesResolved": images_found,
        "articles": [
            {
                "title": a["title"],
                "link": a["link"],
                "source": a["source"],
                "pubDate": a["pubDate"],
                "image": a.get("image"),
            }
            for a in selected
        ],
    }

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(selected)} articles, {images_found} with real images (stale={stale})")


if __name__ == "__main__":
    main()

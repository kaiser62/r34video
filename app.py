from flask import Flask, render_template, request, Response, stream_with_context, jsonify
import re
import requests
import logging
import html
import os
import gc
import sqlite3
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Lock, Thread
import time
from functools import lru_cache

# Configuration
USE_PROXY = os.getenv('USE_PROXY', 'true').lower() == 'true'
DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
MAX_WORKERS = int(os.getenv('MAX_WORKERS', '2'))
CACHE_SIZE = int(os.getenv('CACHE_SIZE', '128'))
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '8'))
MULTI_TAG_PAGES = int(os.getenv('MULTI_TAG_PAGES', '3'))
MULTI_TAG_CACHE_TTL = int(os.getenv('MULTI_TAG_CACHE_TTL', '300'))
TAGS_DB_PATH = os.getenv('TAGS_DB_PATH', 'tags.db')

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False  # Faster JSON serialization
BASE_URL = "https://rule34video.com"
ALLOWED_HOSTS = {"rule34video.com", "www.rule34video.com"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

PROXIES = {
    "http": "http://192.168.1.140:8887",
    "https": "http://192.168.1.140:8887"
} if USE_PROXY else None

# Optimize logging for production
if DEBUG_MODE:
    logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s] %(message)s')
else:
    logging.basicConfig(level=logging.WARNING, format='[%(levelname)s] %(message)s')

# Optimized thread pool for free tier
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
active_futures: dict[str, Future] = {}
future_lock = Lock()

# Session pooling for better connection reuse
session_pool = requests.Session()
session_pool.headers.update(HEADERS)

# --- Tag index (sqlite, persists known tags for autocomplete + exclusion filtering) ---
_tag_db_lock = Lock()


def get_db():
    return sqlite3.connect(TAGS_DB_PATH, timeout=10)


def init_tag_db():
    with _tag_db_lock, get_db() as conn:
        # WAL allows concurrent readers alongside a writer, needed once >1 gunicorn worker
        # process (or many gevent greenlets) touch this file at once.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS tag_counts (tag TEXT PRIMARY KEY, count INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS video_tags (video_id TEXT, tag TEXT, PRIMARY KEY(video_id, tag))")
        # Videos whose *complete* tag list has been scraped (via resolve), as opposed to
        # video_tags rows seeded from a single category listing, which only prove one tag.
        conn.execute("CREATE TABLE IF NOT EXISTS resolved_videos (video_id TEXT PRIMARY KEY)")
        conn.commit()


init_tag_db()


def slugify_tag(tag: str) -> str:
    return re.sub(r"\s+", "-", tag.strip().lower())


def index_tags(tags, video_id=None):
    if not tags:
        return
    with _tag_db_lock, get_db() as conn:
        for raw in tags:
            tag = slugify_tag(raw)
            if not tag:
                continue
            conn.execute(
                "INSERT INTO tag_counts(tag, count) VALUES (?, 1) "
                "ON CONFLICT(tag) DO UPDATE SET count = count + 1",
                (tag,),
            )
            if video_id:
                conn.execute(
                    "INSERT OR IGNORE INTO video_tags(video_id, tag) VALUES (?, ?)",
                    (video_id, tag),
                )
        conn.commit()


def get_video_tags(video_id: str) -> set:
    with _tag_db_lock, get_db() as conn:
        rows = conn.execute("SELECT tag FROM video_tags WHERE video_id = ?", (video_id,)).fetchall()
    return {r[0] for r in rows}


def mark_resolved(video_id: str):
    with _tag_db_lock, get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO resolved_videos(video_id) VALUES (?)", (video_id,))
        conn.commit()


def is_fully_resolved(video_id: str) -> bool:
    with _tag_db_lock, get_db() as conn:
        row = conn.execute("SELECT 1 FROM resolved_videos WHERE video_id = ?", (video_id,)).fetchone()
    return row is not None


def suggest_tags(prefix: str, limit: int = 15) -> list[str]:
    prefix = slugify_tag(prefix)
    if not prefix:
        return []
    with _tag_db_lock, get_db() as conn:
        rows = conn.execute(
            "SELECT tag FROM tag_counts WHERE tag LIKE ? ORDER BY count DESC, tag ASC LIMIT ?",
            (prefix + "%", limit),
        ).fetchall()
    return [r[0] for r in rows]


def extract_video_id_from_url(url: str):
    try:
        return url.strip("/").split("/")[-2]
    except Exception:
        return None


def is_allowed_url(url: str) -> bool:
    """Only allow fetching/proxying URLs on the source site's own host, to prevent
    /resolve and /stream from being used as an open SSRF proxy."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and parsed.hostname in ALLOWED_HOSTS
    except Exception:
        return False


def parse_query(q: str):
    """Split a query string into include tags and exclude tags (prefixed with '-')."""
    tokens = q.strip().split()
    include, exclude = [], []
    for t in tokens:
        if not t:
            continue
        if t.startswith("-") and len(t) > 1:
            exclude.append(slugify_tag(t[1:]))
        else:
            include.append(slugify_tag(t))
    return include, exclude


def seed_tag_index():
    try:
        html_text = get_html(f"{BASE_URL}/latest-updates/1/")
        index_tags(extract_popular_tags(html_text))
    except Exception as e:
        logging.error(f"[SEED] Failed to seed tag index: {e}")


# Memory optimization
def periodic_gc():
    while True:
        time.sleep(120)  # Run garbage collection every 2 minutes
        gc.collect()

Thread(target=periodic_gc, daemon=True).start()

@lru_cache(maxsize=CACHE_SIZE)
def get_html(url: str) -> str:
    logging.debug(f"Fetching HTML from: {url}")
    try:
        response = session_pool.get(
            url, 
            timeout=REQUEST_TIMEOUT,
            proxies=PROXIES
        )
        
        # Only write debug file in debug mode
        if DEBUG_MODE:
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(response.text)

        response.raise_for_status()
        logging.debug(f"HTML fetched successfully: {len(response.text)} characters")
        return response.text
    except Exception as e:
        logging.error(f"Failed to fetch HTML: {e}")
        return ""


@lru_cache(maxsize=64)
def extract_tags_from_video_html(page_html: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    tag_elements = soup.select(".wrap .tag_item")
    return sorted({a.get_text(strip=True) for a in tag_elements})


@lru_cache(maxsize=32)
def extract_popular_tags(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    tags = set()
    for a in soup.select("a.item[href*='/categories/']"):
        name_el = a.select_one(".name")
        if not name_el:
            continue
        count_el = name_el.select_one(".count")
        if count_el:
            count_el.extract()
        text = name_el.get_text(strip=True)
        if text:
            tags.add(text)
    return sorted(tags)


def extract_videos(html_text: str) -> list[dict]:
    logging.debug("Extracting videos from HTML...")
    soup = BeautifulSoup(html_text, "html.parser")
    video_items = soup.select("div.item.thumb")

    videos = []
    for item in video_items:
        link_tag = item.select_one('a.js-open-popup[href*="/video/"]')
        if not link_tag:
            continue  # skip non-video blocks

        href = link_tag['href']
        full_link = urljoin(BASE_URL, href)
        title = link_tag.get('title') or ""
        video_id = href.strip("/").split("/")[-2]  # e.g. /video/3821357/title/ → 3821357

        thumbnail_tag = item.select_one("img.thumb.lazy-load")
        thumbnail = thumbnail_tag.get("data-original") if thumbnail_tag else ""
        
        # Try alternative thumbnail sources if primary not found
        if not thumbnail and thumbnail_tag:
            thumbnail = thumbnail_tag.get("src") or thumbnail_tag.get("data-src") or ""
            
        # If still no thumbnail, try other image selectors
        if not thumbnail:
            alt_thumbnail = item.select_one("img")
            if alt_thumbnail:
                thumbnail = alt_thumbnail.get("src") or alt_thumbnail.get("data-src") or alt_thumbnail.get("data-original") or ""
        
        logging.debug(f"[VIDEO] Extracted thumbnail for {video_id}: {thumbnail}")

        duration_tag = item.select_one(".time")
        duration = duration_tag.text.strip() if duration_tag else ""

        is_hd = "HD" if item.select_one(".quality") else ""

        videos.append({
            "id": video_id,
            "link": full_link,
            "thumbnail": thumbnail,
            "title": title.strip(),
            "is_hd": is_hd,
            "duration": duration,
            "tags": [],
        })

    logging.debug(f"Returning {len(videos)} videos")
    return videos






def extract_direct_stream_urls_from_html(page_html: str) -> dict:
    logging.debug("Extracting direct stream URLs from video page HTML...")
    soup = BeautifulSoup(page_html, "html.parser")
    stream_links = soup.select(".wrap a.tag_item") or soup.select("a[href*='.mp4']")
    urls = {}
    for a in stream_links:
        href = a.get("href")
        label = a.text.strip()
        if href and "mp4" in href.lower():
            urls[label or f"Quality {len(urls) + 1}"] = html.unescape(href)

    if not urls:
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string:
                mp4_urls = re.findall(r'["\'](https?://[^"\']+\.mp4)["\']', script.string)
                for i, url in enumerate(mp4_urls):
                    urls[f"Source {i + 1}"] = html.unescape(url)

    logging.debug(f"Found {len(urls)} stream links")
    return urls


def resolve_all_video_urls(video_page_url: str) -> dict:
    logging.debug(f"Resolving all video URLs and tags from page: {video_page_url}")
    try:
        html_page = get_html(video_page_url)
        soup = BeautifulSoup(html_page, "html.parser")

        # Extract stream URLs
        urls = extract_direct_stream_urls_from_html(html_page)

        # Extract tags
        tag_elements = soup.select(".tag_item")
        tags = sorted({tag.get_text(strip=True) for tag in tag_elements})

        # Extract title
        title_element = soup.select_one(".title_video, h1.title, h1")
        title = title_element.text.strip() if title_element else ""

        video_id = extract_video_id_from_url(video_page_url)
        if video_id and html_page:
            index_tags(tags, video_id=video_id)
            mark_resolved(video_id)

        return {
            "streams": urls,
            "tags": tags,
            "title": title
        }
    except Exception as e:
        logging.error(f"Error resolving stream and tags: {e}")
        return {"streams": {}, "tags": [], "title": ""}


def threaded_resolve(video_page_url: str) -> dict:
    # ThreadPoolExecutor already caps concurrency at MAX_WORKERS and queues the
    # rest; no need to cancel someone else's in-flight resolve to make room.
    future = executor.submit(resolve_all_video_urls, video_page_url)
    with future_lock:
        active_futures[video_page_url] = future
    try:
        return future.result(timeout=REQUEST_TIMEOUT + 2)
    except Exception as e:
        logging.error(f"[THREAD ERROR] {e}")
        return {"streams": {}, "tags": [], "title": ""}
    finally:
        with future_lock:
            active_futures.pop(video_page_url, None)


def background_cleaner(interval: int = 300):  # More frequent cleanup
    while True:
        time.sleep(interval)
        with future_lock:
            completed = [url for url, f in active_futures.items() if f.done() or f.cancelled()]
            for url in completed:
                logging.debug(f"[CLEANUP] Removing completed thread: {url}")
                del active_futures[url]


Thread(target=seed_tag_index, daemon=True).start()


# --- Multi-tag AND search with '-tag' exclusion ---
_multi_tag_cache = {}
_multi_tag_cache_lock = Lock()


def fetch_tag_candidates(tag: str, pages: int = MULTI_TAG_PAGES) -> dict:
    """Fetch up to `pages` of a tag's exact category listing; return {video_id: video_dict}."""
    result = {}
    for p in range(1, pages + 1):
        url = f"{BASE_URL}/categories/{tag}/" if p == 1 else f"{BASE_URL}/categories/{tag}/{p}/"
        html_text = get_html(url)
        if not html_text:
            break
        vids = extract_videos(html_text)
        if not vids:
            break
        for v in vids:
            result[v["id"]] = v
            index_tags([tag], video_id=v["id"])
    return result


def filter_excluded(candidates: list, exclude: list) -> list:
    exclude_set = set(exclude)
    kept = []
    need_resolve = []

    for v in candidates:
        cached = get_video_tags(v["id"])
        if cached & exclude_set:
            continue  # definitely has an excluded tag, drop without resolving
        if is_fully_resolved(v["id"]):
            kept.append(v)  # complete tag list known, and it doesn't overlap exclude_set
        else:
            # cache (if any) is only a partial tag list (e.g. seeded from one category
            # listing) — not enough to prove absence of an excluded tag, must resolve
            need_resolve.append(v)

    if need_resolve:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(resolve_all_video_urls, v["link"]): v for v in need_resolve}
            for fut, v in futures.items():
                try:
                    data = fut.result(timeout=REQUEST_TIMEOUT + 2)
                    tags = {slugify_tag(t) for t in data.get("tags", [])}
                    if not (tags & exclude_set):
                        kept.append(v)
                except Exception as e:
                    logging.error(f"[EXCLUDE-CHECK] Failed for {v.get('link')}: {e}")

    return kept


def multi_tag_search(include: list, exclude: list) -> list:
    if not include:
        return []

    with ThreadPoolExecutor(max_workers=min(len(include), MAX_WORKERS + 2)) as ex:
        futures = {ex.submit(fetch_tag_candidates, tag): tag for tag in include}
        per_tag_results = {}
        for fut, tag in futures.items():
            try:
                per_tag_results[tag] = fut.result(timeout=REQUEST_TIMEOUT * MULTI_TAG_PAGES + 5)
            except Exception as e:
                logging.error(f"[MULTI-TAG] Failed fetching tag '{tag}': {e}")
                per_tag_results[tag] = {}

    id_sets = [set(v.keys()) for v in per_tag_results.values()]
    common_ids = set.intersection(*id_sets) if id_sets and all(id_sets) else set()

    merged = {}
    for tag_results in per_tag_results.values():
        for vid, data in tag_results.items():
            if vid in common_ids and vid not in merged:
                merged[vid] = data

    candidates = list(merged.values())
    if exclude:
        candidates = filter_excluded(candidates, exclude)
    return candidates


def cached_multi_tag_search(include: list, exclude: list) -> list:
    key = (tuple(sorted(include)), tuple(sorted(exclude)))
    now = time.time()
    with _multi_tag_cache_lock:
        entry = _multi_tag_cache.get(key)
        if entry and now - entry[0] < MULTI_TAG_CACHE_TTL:
            return entry[1]

    result = multi_tag_search(include, exclude)

    with _multi_tag_cache_lock:
        _multi_tag_cache[key] = (now, result)
        if len(_multi_tag_cache) > 100:
            oldest_key = min(_multi_tag_cache, key=lambda k: _multi_tag_cache[k][0])
            del _multi_tag_cache[oldest_key]

    return result


@app.route("/")
def index():
    page = request.args.get("page", "1")
    query = request.args.get("q", "").strip()
    
    logging.debug(f"[INDEX] Loading page {page}, query='{query}'")
    logging.debug(f"[CONFIG] USE_PROXY={USE_PROXY}, DEBUG_MODE={DEBUG_MODE}")
    
    if query:
        include, exclude = parse_query(query)
        logging.debug(f"[SEARCH] include={include} exclude={exclude} page={page}")

        if len(include) <= 1 and not exclude:
            # Fast path: original free-text/single-tag site search
            formatted_query = query.replace(" ", "-")
            search_url = f"{BASE_URL}/search/{formatted_query}?sort_by=post_date;from:{page}"
            logging.debug(f"[SEARCH] Search URL: {search_url}")

            html_text = get_html(search_url)
            videos = extract_videos(html_text)
            all_tags = extract_popular_tags(html_text)
            index_tags(all_tags)
        else:
            # Multi-tag AND search with optional '-tag' exclusion
            all_candidates = cached_multi_tag_search(include, exclude)
            page_size = 24
            start = (int(page) - 1) * page_size
            videos = all_candidates[start:start + page_size]
            all_tags = include

        logging.debug(f"[SEARCH] Query='{query}', Page={page} → {len(videos)} videos, {len(all_tags)} tags")
    else:
        # Handle normal home page
        html_text = get_html(f"{BASE_URL}/latest-updates/{page}/")
        all_tags = extract_popular_tags(html_text)
        videos = extract_videos(html_text)
        index_tags(all_tags)

        logging.debug(f"[INDEX] Found {len(videos)} videos, {len(all_tags)} tags")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        logging.debug("[INDEX] Returning JSON response")
        return jsonify(videos)

    logging.debug("[INDEX] Rendering template")
    return render_template("index.html", 
                         videos=videos, 
                         tags=all_tags, 
                         current_page=int(page),
                         query=query,
                         proxy_enabled=USE_PROXY)




@app.route("/resolve")
def resolve():
    video_url = request.args.get("url")
    logging.debug(f"[RESOLVE] Starting resolve for: {video_url}")
    
    if not video_url:
        logging.warning("[RESOLVE] Missing video URL")
        return jsonify({"streams": {}, "tags": [], "title": ""}), 400

    if not is_allowed_url(video_url):
        logging.warning(f"[RESOLVE] Rejected disallowed host: {video_url}")
        return jsonify({"streams": {}, "tags": [], "title": ""}), 400

    result = threaded_resolve(video_url)
    logging.debug(f"[RESOLVE] Result: {len(result.get('streams', {}))} streams, {len(result.get('tags', []))} tags")
    return jsonify(result)


@app.route("/stream")
def stream():
    video_url = request.args.get("url")
    logging.debug(f"[STREAM] Streaming request for: {video_url}")
    
    if not video_url:
        logging.warning("[STREAM] Missing video URL")
        return "Missing video URL", 400

    video_url = html.unescape(video_url)

    if not is_allowed_url(video_url):
        logging.warning(f"[STREAM] Rejected disallowed host: {video_url}")
        return "URL not allowed", 400

    headers = dict(HEADERS)
    range_header = request.headers.get("Range")

    if range_header:
        headers["Range"] = range_header
        logging.debug(f"[STREAM] Range request: {range_header}")

    try:
        logging.debug(f"[STREAM] Fetching from: {video_url}")
        r = session_pool.get(
            video_url, 
            headers=headers, 
            stream=True, 
            timeout=REQUEST_TIMEOUT,
            proxies=PROXIES
        )

        # Ensure we have the right content type
        content_type = r.headers.get("Content-Type", "video/mp4")
        logging.debug(f"[STREAM] Status: {r.status_code}, Content-Type: {content_type}")

        response = Response(
            stream_with_context(r.iter_content(chunk_size=8192)),  # Optimized chunk size for free tier
            status=r.status_code,
            content_type=content_type
        )

        # Copy all important headers
        for header in ["Content-Range", "Accept-Ranges", "Content-Length", "Cache-Control"]:
            if header in r.headers:
                response.headers[header] = r.headers[header]

        # Ensure these headers are set even if not in original response
        if "Accept-Ranges" not in r.headers:
            response.headers["Accept-Ranges"] = "bytes"

        # Add CORS headers
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Range, Origin, X-Requested-With"

        logging.debug(f"[STREAM] Successfully streaming {content_type}")
        return response
    except Exception as e:
        logging.error(f"[STREAM] Stream failed: {e}")
        return f"Stream failed: {e}", 500


@app.route("/api/tags/suggest")
def tags_suggest():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    return jsonify(suggest_tags(q))


@app.route("/health")
def health_check():
    """Health check endpoint for Render"""
    return {"status": "healthy", "service": "r34video-app"}, 200


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return render_template("index.html", 
                         videos=[], 
                         tags=[], 
                         current_page=1,
                         query="",
                         proxy_enabled=USE_PROXY), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    logging.error(f"Internal server error: {error}")
    return {"error": "Internal server error"}, 500


if __name__ == '__main__':
    # Start background cleaner thread
    Thread(target=background_cleaner, daemon=True).start()
    
    # Different startup behavior for development vs production
    if DEBUG_MODE:
        logging.info("="*50)
        logging.info("🚀 Starting R34Video TikTok Style App (Development)")
        logging.info("="*50)
        logging.info(f"📡 USE_PROXY: {USE_PROXY}")
        logging.info(f"🐛 DEBUG_MODE: {DEBUG_MODE}")
        logging.info(f"⚡ MAX_WORKERS: {MAX_WORKERS}")
        logging.info(f"💾 CACHE_SIZE: {CACHE_SIZE}")
        logging.info(f"⏱️  REQUEST_TIMEOUT: {REQUEST_TIMEOUT}s")
        logging.info(f"🌐 PROXIES: {PROXIES}")
        logging.info(f"🎯 BASE_URL: {BASE_URL}")
        logging.info("="*50)
        logging.info("🧹 Background cleaner thread started")
        
        app.run(host='0.0.0.0', port=5001, debug=True)
    else:
        # Production mode - let Gunicorn handle the app
        logging.warning("🚀 R34Video App starting in production mode")
        logging.warning("🧹 Background cleaner thread started")

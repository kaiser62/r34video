from flask import Flask, render_template, request, Response, stream_with_context, jsonify
import re
import requests
import logging
import html
import os
import gc
import sys
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Lock, Thread
import time
from functools import lru_cache

# Configuration optimized for serverless
USE_PROXY = os.getenv('USE_PROXY', 'false').lower() == 'true'  # Default false for serverless
DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
MAX_WORKERS = int(os.getenv('MAX_WORKERS', '1'))  # Optimized for Vercel
CACHE_SIZE = int(os.getenv('CACHE_SIZE', '32'))   # Reduced for serverless memory limits
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '10'))  # Increased for stability

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False  # Faster JSON serialization
BASE_URL = "https://rule34video.com"

# Alternative base URLs to try if main site fails
ALTERNATIVE_URLS = [
    "https://rule34video.com",
    "https://www.rule34video.com", 
    "http://rule34video.com"
]
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
    "DNT": "1",
    "Sec-Fetch-Dest": "document", 
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
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

# Configure session for better reliability
session_pool.verify = True  # SSL verification
session_pool.timeout = REQUEST_TIMEOUT
session_pool.max_redirects = 5

# Add retry strategy for better reliability
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS"]
)

adapter = HTTPAdapter(max_retries=retry_strategy)
session_pool.mount("http://", adapter)
session_pool.mount("https://", adapter)

logging.warning(f"🔧 [INIT] Session configured with {REQUEST_TIMEOUT}s timeout and retry strategy")

# Serverless-optimized memory management
def serverless_gc():
    """Aggressive garbage collection for serverless environments"""
    if os.getenv('FLASK_ENV') == 'production':
        gc.collect()
        
# Run GC after each request in production
@app.after_request
def cleanup_memory(response):
    if os.getenv('FLASK_ENV') == 'production':
        serverless_gc()
    return response

@lru_cache(maxsize=CACHE_SIZE)
def get_html(url: str) -> str:
    logging.warning(f"🌐 [GET_HTML] Starting fetch from: {url}")
    logging.warning(f"🔧 [GET_HTML] Config - USE_PROXY={USE_PROXY}, PROXIES={PROXIES}")
    logging.warning(f"🔧 [GET_HTML] Timeout: {REQUEST_TIMEOUT}s, Headers: {HEADERS['User-Agent'][:50]}...")
    
    try:
        response = session_pool.get(
            url, 
            timeout=REQUEST_TIMEOUT,
            proxies=PROXIES
        )
        
        logging.warning(f"📡 [GET_HTML] Response status: {response.status_code}")
        logging.warning(f"📏 [GET_HTML] Response length: {len(response.text)} chars")
        logging.warning(f"📄 [GET_HTML] Content-Type: {response.headers.get('Content-Type', 'Unknown')}")
        
        # Log first 500 chars for debugging
        if DEBUG_MODE:
            preview = response.text[:500].replace('\n', ' ').replace('\r', '')
            logging.warning(f"👁️ [GET_HTML] HTML Preview: {preview}...")
        
        # Skip debug file writing in serverless to avoid filesystem issues
        if DEBUG_MODE and not os.getenv('VERCEL'):
            try:
                with open("debug_page.html", "w", encoding="utf-8") as f:
                    f.write(response.text)
                logging.warning(f"💾 [GET_HTML] Saved debug file")
            except Exception as write_error:
                logging.warning(f"💾 [GET_HTML] Could not save debug file: {write_error}")

        response.raise_for_status()
        logging.warning(f"✅ [GET_HTML] Successfully fetched {len(response.text)} characters")
        return response.text
        
    except requests.exceptions.Timeout as e:
        logging.error(f"⏰ [GET_HTML] Timeout error after {REQUEST_TIMEOUT}s: {e}")
        return ""
    except requests.exceptions.ConnectionError as e:
        logging.error(f"🔌 [GET_HTML] Connection error: {e}")
        return ""
    except requests.exceptions.HTTPError as e:
        logging.error(f"🚫 [GET_HTML] HTTP error: {e} (Status: {response.status_code if 'response' in locals() else 'Unknown'})")
        return ""
    except Exception as e:
        logging.error(f"❌ [GET_HTML] Unexpected error: {type(e).__name__}: {e}")
        return ""


@lru_cache(maxsize=64)
def extract_tags_from_video_html(page_html: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    tag_elements = soup.select(".wrap .tag_item")
    return sorted({a.get_text(strip=True) for a in tag_elements})


@lru_cache(maxsize=32)
def extract_popular_tags(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    tag_elements = soup.select(".categories a") or soup.select(".tags a") or soup.select(".list a")
    return sorted({a.get_text(strip=True) for a in tag_elements if a.get_text(strip=True)})


def extract_videos(html_text: str) -> list[dict]:
    logging.warning(f"🎬 [EXTRACT_VIDEOS] Starting extraction from {len(html_text)} chars of HTML")
    
    if not html_text:
        logging.error(f"❌ [EXTRACT_VIDEOS] No HTML content provided")
        return []
    
    soup = BeautifulSoup(html_text, "html.parser")
    
    # Try different selectors to find video items
    selectors_to_try = [
        "div.item.thumb",
        ".item.thumb",
        "div.item",
        ".item",
        "div.thumb",
        ".video-item",
        "article",
        "div[class*='item']",
        "div[class*='thumb']",
        "div[class*='video']"
    ]
    
    video_items = []
    for selector in selectors_to_try:
        video_items = soup.select(selector)
        logging.warning(f"🔍 [EXTRACT_VIDEOS] Selector '{selector}': found {len(video_items)} items")
        if video_items:
            break
    
    if not video_items:
        logging.error(f"❌ [EXTRACT_VIDEOS] No video items found with any selector")
        # Log some of the HTML structure for debugging
        all_divs = soup.select("div")
        logging.warning(f"📋 [EXTRACT_VIDEOS] Total divs found: {len(all_divs)}")
        if all_divs:
            sample_classes = [div.get('class', []) for div in all_divs[:10]]
            logging.warning(f"📋 [EXTRACT_VIDEOS] Sample div classes: {sample_classes}")
        return []

    logging.warning(f"📦 [EXTRACT_VIDEOS] Processing {len(video_items)} video items")
    videos = []
    for i, item in enumerate(video_items):
        # Try different link selectors
        link_selectors = [
            'a.js-open-popup[href*="/video/"]',
            'a[href*="/video/"]',
            'a.js-open-popup',
            'a',
            '[href*="/video/"]'
        ]
        
        link_tag = None
        for link_selector in link_selectors:
            link_tag = item.select_one(link_selector)
            if link_tag:
                logging.warning(f"🔗 [EXTRACT_VIDEOS] Item {i}: Found link with selector '{link_selector}'")
                break
        
        if not link_tag:
            logging.warning(f"⚠️ [EXTRACT_VIDEOS] Item {i}: No link found, skipping")
            continue

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
        
        logging.warning(f"🖼️ [EXTRACT_VIDEOS] Item {i} (ID: {video_id}): thumbnail='{thumbnail[:100] if thumbnail else 'None'}...'")

        duration_tag = item.select_one(".time")
        duration = duration_tag.text.strip() if duration_tag else ""

        is_hd = "HD" if item.select_one(".quality") else ""

        video_data = {
            "id": video_id,
            "link": full_link,
            "thumbnail": thumbnail,
            "title": title.strip(),
            "is_hd": is_hd,
            "duration": duration,
            "tags": [],
        }
        videos.append(video_data)
        logging.warning(f"✅ [EXTRACT_VIDEOS] Item {i}: Successfully extracted video '{title[:50]}...' (ID: {video_id})")

    logging.warning(f"🎯 [EXTRACT_VIDEOS] Final result: {len(videos)} videos extracted")
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

        return {
            "streams": urls,
            "tags": tags,
            "title": title
        }
    except Exception as e:
        logging.error(f"Error resolving stream and tags: {e}")
        return {"streams": {}, "tags": [], "title": ""}


def threaded_resolve(video_page_url: str) -> dict:
    # Simplified for serverless - direct execution for better reliability
    if os.getenv('VERCEL') or MAX_WORKERS == 1:
        return resolve_all_video_urls(video_page_url)
    
    # Original threading logic for multi-worker environments
    with future_lock:
        if len(active_futures) >= MAX_WORKERS:
            oldest_url = next(iter(active_futures))
            logging.debug(f"[THREAD] Killing oldest thread: {oldest_url}")
            active_futures[oldest_url].cancel()
            del active_futures[oldest_url]

        future = executor.submit(resolve_all_video_urls, video_page_url)
        active_futures[video_page_url] = future

    try:
        return future.result(timeout=REQUEST_TIMEOUT + 2)
    except Exception as e:
        logging.error(f"[THREAD ERROR] {e}")
        return {"streams": {}, "tags": [], "title": ""}


def get_debug_context():
    """Get current debug context information"""
    try:
        return {
            'timestamp': time.time(),
            'proxy_enabled': USE_PROXY,
            'vercel_env': bool(os.getenv('VERCEL')),
            'debug_mode': DEBUG_MODE,
            'max_workers': MAX_WORKERS,
            'cache_size': CACHE_SIZE,
            'request_timeout': REQUEST_TIMEOUT,
            'base_url': BASE_URL,
            'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            'active_futures_count': len(active_futures),
            'flask_env': os.getenv('FLASK_ENV'),
            'user_agent': HEADERS.get('User-Agent', ''),
            'proxies_config': bool(PROXIES)
        }
    except Exception as e:
        return {'error': f'Debug context error: {e}'}

def background_cleaner(interval: int = 300):  # More frequent cleanup
    while True:
        time.sleep(interval)
        with future_lock:
            completed = [url for url, f in active_futures.items() if f.done() or f.cancelled()]
            for url in completed:
                logging.debug(f"[CLEANUP] Removing completed thread: {url}")
                del active_futures[url]


@app.route("/")
def index():
    page = request.args.get("page", "1")
    query = request.args.get("q", "").strip()
    
    # Enhanced debug logging
    logging.debug(f"[INDEX] Loading page {page}, query='{query}'")
    logging.debug(f"[CONFIG] USE_PROXY={USE_PROXY}, DEBUG_MODE={DEBUG_MODE}")
    logging.debug(f"[ENV] VERCEL={os.getenv('VERCEL')}, FLASK_ENV={os.getenv('FLASK_ENV')}")
    
    # Connection debug info
    debug_info = {
        'proxy_enabled': USE_PROXY,
        'vercel_env': bool(os.getenv('VERCEL')),
        'max_workers': MAX_WORKERS,
        'cache_size': CACHE_SIZE,
        'request_timeout': REQUEST_TIMEOUT,
        'base_url': BASE_URL,
        'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        'active_threads': len(active_futures)
    }
    
    try:
        logging.warning(f"🏠 [INDEX] Starting request: page={page}, query='{query}'")
        logging.warning(f"🔧 [INDEX] Current config: proxy={USE_PROXY}, debug={DEBUG_MODE}, workers={MAX_WORKERS}")
        
        if query:
            # Handle search functionality
            logging.warning(f"🔍 [SEARCH] Query='{query}', Page={page}")
            formatted_query = query.replace(" ", "-")
            search_url = f"{BASE_URL}/search/{formatted_query}?sort_by=post_date;from:{page}"
            logging.debug(f"[SEARCH] Search URL: {search_url}")
            
            html_text = get_html(search_url)
            logging.debug(f"[SEARCH] HTML length: {len(html_text)} chars")
            
            if not html_text:
                logging.error(f"[SEARCH] No HTML content received from {search_url}")
                videos, all_tags = [], []
            else:
                videos = extract_videos(html_text)
                all_tags = extract_popular_tags(html_text)
            
            logging.debug(f"[SEARCH] Query='{query}', Page={page} → {len(videos)} videos, {len(all_tags)} tags")
        else:
            # Try multiple home page URLs
            home_urls_to_try = [
                f"{BASE_URL}/latest-updates/{page}/",
                f"{BASE_URL}/latest/{page}/", 
                f"{BASE_URL}/page/{page}/",
                f"{BASE_URL}/latest-updates/",
                f"{BASE_URL}/",
                f"{BASE_URL}/videos/",
                f"{BASE_URL}/newest/"
            ]
            
            html_text = ""
            successful_url = None
            
            for home_url in home_urls_to_try:
                logging.warning(f"🏠 [INDEX] Trying URL: {home_url}")
                html_text = get_html(home_url)
                if html_text and len(html_text) > 1000:  # Reasonable minimum size
                    successful_url = home_url
                    logging.warning(f"✅ [INDEX] Success with URL: {home_url} ({len(html_text)} chars)")
                    break
                else:
                    logging.warning(f"❌ [INDEX] Failed/small response from: {home_url} ({len(html_text)} chars)")
            
            if not successful_url:
                logging.error(f"💥 [INDEX] All home URLs failed!")
            
            if not html_text:
                logging.error(f"[INDEX] No HTML content received from {home_url}")
                videos, all_tags = [], []
            else:
                all_tags = extract_popular_tags(html_text)
                videos = extract_videos(html_text)
            
            logging.debug(f"[INDEX] Found {len(videos)} videos, {len(all_tags)} tags")
            
    except Exception as e:
        logging.error(f"💥 [INDEX] Error fetching content: {e}")
        videos, all_tags = [], []
        
        # Try mock data as absolute fallback for testing
        if DEBUG_MODE and not videos:
            logging.warning("🧪 [INDEX] Adding mock data for testing")
            videos = [
                {
                    "id": "mock1",
                    "link": f"{BASE_URL}/video/mock1/test-video-1/",
                    "thumbnail": "https://via.placeholder.com/300x200/333/fff?text=Test+Video+1",
                    "title": "Test Video 1 (Mock Data)",
                    "is_hd": "HD",
                    "duration": "5:00",
                    "tags": ["test", "mock", "debug"]
                },
                {
                    "id": "mock2", 
                    "link": f"{BASE_URL}/video/mock2/test-video-2/",
                    "thumbnail": "https://via.placeholder.com/300x200/666/fff?text=Test+Video+2",
                    "title": "Test Video 2 (Mock Data)",
                    "is_hd": "",
                    "duration": "3:30", 
                    "tags": ["test", "debug"]
                }
            ]
            all_tags = ["test", "mock", "debug", "placeholder"]
            logging.warning(f"🧪 [INDEX] Mock data created: {len(videos)} videos")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        logging.debug("[INDEX] Returning JSON response")
        response_data = {
            'videos': videos,
            'debug_info': debug_info if DEBUG_MODE else None
        }
        return jsonify(videos)  # Keep existing format for compatibility

    logging.debug(f"[INDEX] Rendering template with {len(videos)} videos")
    
    # Add debug info about the data being passed to template
    if DEBUG_MODE or not videos:
        logging.warning(f"[INDEX] Template data: videos={len(videos)}, tags={len(all_tags)}, page={page}, query='{query}'")
        if not videos:
            logging.error("[INDEX] No videos to render - this will trigger fallback message")
    
    return render_template("index.html", 
                         videos=videos, 
                         tags=all_tags, 
                         current_page=int(page),
                         query=query,
                         proxy_enabled=USE_PROXY,
                         debug_info=debug_info)




@app.route("/resolve")
def resolve():
    video_url = request.args.get("url")
    logging.debug(f"[RESOLVE] Starting resolve for: {video_url}")
    
    if not video_url:
        logging.warning("[RESOLVE] Missing video URL")
        return jsonify({
            "streams": {}, 
            "tags": [], 
            "title": "",
            "error": "Missing video URL",
            "debug_info": get_debug_context() if DEBUG_MODE else None
        }), 400
    
    try:
        result = threaded_resolve(video_url)
        logging.debug(f"[RESOLVE] Result: {len(result.get('streams', {}))} streams, {len(result.get('tags', []))} tags")
        
        # Add debug info if enabled
        if DEBUG_MODE:
            result['debug_info'] = get_debug_context()
            result['request_url'] = video_url
            
        return jsonify(result)
    except Exception as e:
        logging.error(f"[RESOLVE] Error: {e}")
        return jsonify({
            "streams": {}, 
            "tags": [], 
            "title": "",
            "error": str(e),
            "debug_info": get_debug_context() if DEBUG_MODE else None
        }), 500


@app.route("/stream")
def stream():
    video_url = request.args.get("url")
    logging.debug(f"[STREAM] Streaming request for: {video_url}")
    
    if not video_url:
        logging.warning("[STREAM] Missing video URL")
        error_response = {
            "error": "Missing video URL",
            "debug_info": get_debug_context() if DEBUG_MODE else None
        }
        return jsonify(error_response), 400
        
    video_url = html.unescape(video_url)
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

        # Optimized chunk size for Vercel serverless
        chunk_size = 4096 if os.getenv('VERCEL') else 8192
        response = Response(
            stream_with_context(r.iter_content(chunk_size=chunk_size)),
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


@app.route("/health")
def health_check():
    """Health check endpoint with debug info"""
    return {
        "status": "healthy", 
        "service": "r34video-app",
        "proxy_enabled": USE_PROXY,
        "debug_mode": DEBUG_MODE,
        "vercel": bool(os.getenv('VERCEL')),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "active_futures": len(active_futures),
        "cache_size": CACHE_SIZE,
        "max_workers": MAX_WORKERS
    }, 200


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return render_template("index.html", 
                         videos=[], 
                         tags=[], 
                         current_page=1,
                         query="",
                         proxy_enabled=USE_PROXY), 404


@app.route('/test-fetch')
def test_fetch():
    """Test endpoint to check if HTML fetching works"""
    if not DEBUG_MODE and not os.getenv('VERCEL'):
        return {"error": "Test endpoint disabled"}, 403
    
    try:
        test_url = f"{BASE_URL}/latest-updates/1/"
        html_content = get_html(test_url)
        
        if not html_content:
            return {
                "error": "No HTML content received",
                "url": test_url,
                "proxy_enabled": USE_PROXY,
                "base_url": BASE_URL
            }, 500
            
        # Try to extract some basic info
        videos = extract_videos(html_content)
        tags = extract_popular_tags(html_content)
        
        return {
            "status": "success",
            "url": test_url,
            "html_length": len(html_content),
            "videos_found": len(videos),
            "tags_found": len(tags),
            "first_video": videos[0] if videos else None,
            "proxy_enabled": USE_PROXY,
            "debug_mode": DEBUG_MODE
        }
        
    except Exception as e:
        logging.error(f"[TEST-FETCH] Error: {e}")
        return {
            "error": str(e),
            "url": test_url if 'test_url' in locals() else "N/A",
            "proxy_enabled": USE_PROXY
        }, 500

@app.route('/favicon.ico')
def favicon():
    """Serve a simple favicon to prevent 404 errors"""
    import base64
    from io import BytesIO
    
    # Simple 16x16 transparent favicon
    favicon_data = base64.b64decode(
        'AAABAAEAEBAAAAEACABoBQAAFgAAACgAAAAQAAAAIAAAAAEACAAAAAAAAAEAAAAAAAAAAAAAAAEAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
        'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAP//'
        'AAD//wAA//8AAP//AAD//wAA//8AAP//AAD//wAA//8AAA=='
    )
    
    response = Response(favicon_data)
    response.headers['Content-Type'] = 'image/x-icon'
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


@app.route("/debug")
def debug_info():
    """Debug endpoint with comprehensive system information"""
    if not DEBUG_MODE and not os.getenv('VERCEL'):
        return {"error": "Debug mode disabled"}, 403
    
    import platform
    debug_data = {
        "timestamp": time.time(),
        "environment": {
            "proxy_enabled": USE_PROXY,
            "debug_mode": DEBUG_MODE,
            "vercel_env": bool(os.getenv('VERCEL')),
            "flask_env": os.getenv('FLASK_ENV'),
            "python_version": platform.python_version(),
            "platform": platform.platform()
        },
        "configuration": {
            "max_workers": MAX_WORKERS,
            "cache_size": CACHE_SIZE,
            "request_timeout": REQUEST_TIMEOUT,
            "base_url": BASE_URL,
            "proxies_enabled": bool(PROXIES)
        },
        "runtime_stats": {
            "active_futures": len(active_futures),
            "cache_info": get_html.cache_info()._asdict() if hasattr(get_html, 'cache_info') else None
        },
        "headers": dict(request.headers),
        "request_info": {
            "method": request.method,
            "url": request.url,
            "remote_addr": request.remote_addr,
            "user_agent": request.user_agent.string
        }
    }
    return jsonify(debug_data)

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors with debug info"""
    logging.error(f"Internal server error: {error}")
    error_response = {
        "error": "Internal server error",
        "debug_info": get_debug_context() if DEBUG_MODE else None
    }
    return jsonify(error_response), 500


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

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
    logging.debug(f"Fetching HTML from: {url}")
    try:
        response = session_pool.get(
            url, 
            timeout=REQUEST_TIMEOUT,
            proxies=PROXIES
        )
        
        # Skip debug file writing in serverless to avoid filesystem issues
        if DEBUG_MODE and not os.getenv('VERCEL'):
            try:
                with open("debug_page.html", "w", encoding="utf-8") as f:
                    f.write(response.text)
            except Exception:
                pass  # Ignore filesystem errors in serverless

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
    tag_elements = soup.select(".categories a") or soup.select(".tags a") or soup.select(".list a")
    return sorted({a.get_text(strip=True) for a in tag_elements if a.get_text(strip=True)})


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
    
    if query:
        # Handle search functionality
        logging.debug(f"[SEARCH] Query='{query}', Page={page}")
        formatted_query = query.replace(" ", "-")
        search_url = f"{BASE_URL}/search/{formatted_query}?sort_by=post_date;from:{page}"
        logging.debug(f"[SEARCH] Search URL: {search_url}")
        
        html_text = get_html(search_url)
        videos = extract_videos(html_text)
        all_tags = extract_popular_tags(html_text)
        
        logging.debug(f"[SEARCH] Query='{query}', Page={page} → {len(videos)} videos, {len(all_tags)} tags")
    else:
        # Handle normal home page
        html_text = get_html(f"{BASE_URL}/latest-updates/{page}/")
        all_tags = extract_popular_tags(html_text)
        videos = extract_videos(html_text)
        
        logging.debug(f"[INDEX] Found {len(videos)} videos, {len(all_tags)} tags")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        logging.debug("[INDEX] Returning JSON response")
        response_data = {
            'videos': videos,
            'debug_info': debug_info if DEBUG_MODE else None
        }
        return jsonify(videos)  # Keep existing format for compatibility

    logging.debug("[INDEX] Rendering template")
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

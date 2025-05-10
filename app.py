from flask import Flask, render_template, request, Response, stream_with_context, jsonify
import re
import requests
import logging
import html
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Lock, Thread
import time

app = Flask(__name__)
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
    "https": "http://192.168.1.140:8887",
}

logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s] %(message)s')

executor = ThreadPoolExecutor(max_workers=3)
active_futures: dict[str, Future] = {}
future_lock = Lock()


def get_html(url: str) -> str:
    logging.debug(f"Fetching HTML from: {url}")
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        response = session.get(url, timeout=10)
        with open("debug_page.html", "w", encoding="utf-8") as f:
            f.write(response.text)

        response.raise_for_status()
        logging.debug(f"HTML fetched successfully: {len(response.text)} characters")
        return response.text
    except Exception as e:
        logging.error(f"Failed to fetch HTML: {e}")
        return ""


def extract_tags_from_video_html(page_html: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    tag_elements = soup.select(".wrap .tag_item")
    return sorted({a.get_text(strip=True) for a in tag_elements})


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
    with future_lock:

        if len(active_futures) >= 3:
            oldest_url = next(iter(active_futures))
            logging.debug(f"[THREAD] Killing oldest thread: {oldest_url}")
            active_futures[oldest_url].cancel()
            del active_futures[oldest_url]

        future = executor.submit(resolve_all_video_urls, video_page_url)
        active_futures[video_page_url] = future

    try:
        return future.result(timeout=15)
    except Exception as e:
        logging.error(f"[THREAD ERROR] {e}")
        return {"streams": {}, "tags": [], "title": ""}


def background_cleaner(interval: int = 600):
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
    html_text = get_html(f"{BASE_URL}/latest-updates/{page}/")
    all_tags = extract_popular_tags(html_text)
    videos = extract_videos(html_text)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(videos)

    return render_template("index.html", videos=videos, tags=all_tags, current_page=int(page))


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    page = request.args.get("page", "01")
    if not query:
        return "Missing query", 400
    formatted_query = query.replace(" ", "-")
    search_url = f"{BASE_URL}/search/{formatted_query}?sort_by=post_date;from:{page}"
    html_text = get_html(search_url)
    videos = extract_videos(html_text)
    all_tags = extract_popular_tags(html_text)
    logging.debug(f"[SEARCH] Query='{query}', Page={page} → {len(videos)} videos")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(videos)
    return render_template("search.html", videos=videos, query=query, tags=all_tags, current_page=page)


@app.route("/resolve")
def resolve():
    video_url = request.args.get("url")
    logging.debug(f"[THREAD-RESOLVE] {video_url}")
    result = threaded_resolve(video_url)
    return jsonify(result)


@app.route("/stream")
@app.route("/stream")
def stream():
    video_url = request.args.get("url")
    video_url = html.unescape(video_url)
    headers = dict(HEADERS)
    range_header = request.headers.get("Range")

    if range_header:
        headers["Range"] = range_header

    try:
        r = requests.get(video_url, headers=headers, stream=True, timeout=10)

        # Ensure we have the right content type
        content_type = r.headers.get("Content-Type", "video/mp4")

        response = Response(
            stream_with_context(r.iter_content(chunk_size=16384)),  # Larger chunk size
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

        return response
    except Exception as e:
        logging.error(f"Stream failed: {e}")
        return f"Stream failed: {e}", 500


if __name__ == '__main__':
    Thread(target=background_cleaner, daemon=True).start()
    app.run(host='0.0.0.0', port=5001, debug=True)

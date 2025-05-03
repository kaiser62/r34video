from flask import Flask, render_template, request, Response, stream_with_context, jsonify
import re
import requests
import logging
import html
from urllib.parse import urljoin
from bs4 import BeautifulSoup

app = Flask(__name__)
BASE_URL = "https://rule34video.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": BASE_URL}
PROXIES = {
    "http": "http://192.168.1.140:8887",
    "https": "http://192.168.1.140:8887",
}

logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s] %(message)s')


def get_html(url: str) -> str:
    logging.debug(f"Fetching HTML from: {url}")
    try:
        response = requests.get(url, headers=HEADERS, proxies=PROXIES)
        response.raise_for_status()
        logging.debug(f"HTML fetched successfully: {len(response.text)} characters")
        return response.text
    except Exception as e:
        logging.error(f"Failed to fetch HTML: {e}")
        return ""


def extract_tags_from_video_html(page_html: str) -> list[str]:
    """Extracts tags from the video detail page."""
    soup = BeautifulSoup(page_html, "html.parser")
    tag_elements = soup.select(".wrap .tag_item")
    return sorted({a.get_text(strip=True) for a in tag_elements})


def extract_popular_tags(html_text: str) -> list[str]:
    """Extract popular tags from the main page for filtering."""
    soup = BeautifulSoup(html_text, "html.parser")
    # Try different selectors that might contain tag elements on the main page
    tag_elements = soup.select(".categories a") or soup.select(".tags a") or soup.select(".list a")
    return sorted({a.get_text(strip=True) for a in tag_elements if a.get_text(strip=True)})


def extract_videos(html_text: str) -> list[dict]:
    logging.debug("Extracting videos from HTML...")
    soup = BeautifulSoup(html_text, "html.parser")
    video_items = soup.select("div.item")  # or try 'div.video-item' if needed
    logging.debug(f"Found {len(video_items)} video containers")
    videos = []

    for item in video_items:
        a_tag = item.find("a", href=True)
        img_tag = item.find("img", {"original": True})

        # Try to get title from title_video class first, then fall back to img title
        title_el = item.select_one(".title_video")
        title = title_el.text.strip() if title_el else (img_tag.get("title") if img_tag else "Untitled")
        thumbnail = img_tag.get("original") if img_tag else ""

        # Extract inline tags if available
        tags_elements = item.find_all(class_="tag_item")
        tag_item_list = [tag.get_text(strip=True) for tag in tags_elements]
        logging.debug(f"Found {len(tag_item_list)} inline tags for this video")

        href = a_tag["href"] if a_tag else ""
        full_link = urljoin(BASE_URL, href)
        try:
            parts = href.strip("/").split("/")
            video_id = parts[-2] if len(parts) >= 2 else "unknown"
        except Exception:
            video_id = "unknown"

        duration_el = item.select_one(".time")
        duration = duration_el.text.strip() if duration_el else ""
        is_hd = "HD" if "hd" in item.decode().lower() else ""

        videos.append({
            "id": video_id,
            "link": full_link,
            "thumbnail": thumbnail,
            "title": title.strip(),
            "is_hd": is_hd,
            "duration": duration,
            "tags": tag_item_list,  # Add inline tags if available
        })

    logging.debug(f"Returning {len(videos)} videos")
    return videos


def extract_direct_stream_urls_from_html(page_html: str) -> dict:
    logging.debug("Extracting direct stream URLs from video page HTML...")
    soup = BeautifulSoup(page_html, "html.parser")

    # Try multiple selectors to find stream links
    stream_links = soup.select(".wrap a.tag_item") or soup.select("a[href*='.mp4']")

    urls = {}
    for a in stream_links:
        href = a.get("href")
        label = a.text.strip()
        if href and "mp4" in href.lower():
            urls[label or f"Quality {len(urls) + 1}"] = html.unescape(href)

    # If no direct links found, try to find them in script tags
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
        urls = extract_direct_stream_urls_from_html(html_page)
        tags = extract_tags_from_video_html(html_page)

        # Extract the correct title from the video page
        soup = BeautifulSoup(html_page, "html.parser")
        title_element = soup.select_one(".title_video") or soup.select_one("h1.title") or soup.select_one("h1")
        title = title_element.text.strip() if title_element else ""

        return {
            "streams": urls,
            "tags": tags,
            "title": title
        }
    except Exception as e:
        logging.error(f"Error resolving stream and tags: {e}")
        return {"streams": {}, "tags": [], "title": ""}


@app.route("/")
def index():
    html_text = get_html(f"{BASE_URL}/latest-updates/")
    # Extract popular tags for filtering instead of video page tags
    all_tags = extract_popular_tags(html_text)
    videos = extract_videos(html_text)
    logging.debug(f"[INDEX] Loaded {len(videos)} videos and {len(all_tags)} tags")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(videos)
    return render_template("index.html", videos=videos, tags=all_tags)


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return "Missing query", 400

    formatted_query = query.replace(" ", "-")
    search_url = f"{BASE_URL}/search/{formatted_query}"
    html_text = get_html(search_url)
    videos = extract_videos(html_text)
    # Extract popular tags for filtering from search results page
    all_tags = extract_popular_tags(html_text)

    logging.debug(f"[SEARCH] Query='{query}' → {len(videos)} videos, {len(all_tags)} tags")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(videos)

    return render_template("search.html", videos=videos, query=query, tags=all_tags)


@app.route("/resolve")
def resolve():
    video_url = request.args.get("url")
    logging.debug(f"Resolving via AJAX: {video_url}")
    result = resolve_all_video_urls(video_url)
    return jsonify(result)


@app.route("/stream")
def stream():
    video_url = request.args.get("url")
    video_url = html.unescape(video_url)
    headers = dict(HEADERS)
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header
    try:
        r = requests.get(video_url, headers=headers, stream=True, timeout=10, proxies=PROXIES)
        response = Response(
            stream_with_context(r.iter_content(chunk_size=8192)),
            status=r.status_code,
            content_type=r.headers.get("Content-Type", "video/mp4")
        )
        response.headers["Content-Range"] = r.headers.get("Content-Range", "")
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Content-Length"] = r.headers.get("Content-Length", "")
        return response
    except Exception as e:
        logging.error(f"Stream failed: {e}")
        return f"Stream failed: {e}", 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
"""
Post generated London tour articles to WordPress.com via REST API.
Auth: WordPress.com OAuth2 access token.
Site: londontours74.wordpress.com
"""
import re
import logging
import httpx

logger = logging.getLogger(__name__)

SITE = "londontours74.wordpress.com"
API_BASE = f"https://public-api.wordpress.com/rest/v1.1/sites/{SITE}"


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _clean_html(html: str, image_url: str = "") -> str:
    """Clean HTML for Medium compatibility when imported from WordPress."""
    clean = html
    # Remove hr tags
    clean = re.sub(r'<hr\s*/?>', '', clean)
    # Remove hashtag paragraph
    clean = re.sub(r'<p[^>]*class="hashtags"[^>]*>.*?</p>', '', clean, flags=re.DOTALL)
    # Strip class attributes from all tags
    clean = re.sub(r'(<[a-z]+)\s+class="[^"]*"', r'\1', clean)
    # Prepend image at top of content so Medium sees it
    if image_url:
        clean = f'<img src="{image_url}" alt=""/>\n' + clean
    return clean.strip()


def post_article(access_token: str, tour: dict, article_html: str) -> dict:
    """
    Post a single article to WordPress.com.
    Returns dict with 'url' on success, 'error' on failure.
    """
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", article_html, re.IGNORECASE | re.DOTALL)
    title = _strip_tags(h1.group(1)).strip() if h1 else tour["title"]

    tags_match = re.search(r'class="hashtags">(.*?)</p>', article_html, re.DOTALL)
    tags_text = tags_match.group(1) if tags_match else ""
    tags = [t.lstrip("#") for t in tags_text.split() if t.startswith("#")]

    # Extract excerpt — first <p> after <h1>
    excerpt_match = re.search(r"</h1>\s*<p>(.*?)</p>", article_html, re.IGNORECASE | re.DOTALL)
    excerpt = _strip_tags(excerpt_match.group(1))[:300] if excerpt_match else tour.get("description", "")[:300]

    image_url = tour.get("image_url", "")
    clean_content = _clean_html(article_html, image_url)

    payload = {
        "title": title,
        "content": clean_content,
        "status": "publish",
        "tags": ",".join(tags[:15]),
        "excerpt": excerpt,
        "format": "standard",
    }

    try:
        resp = httpx.post(
            f"{API_BASE}/posts/new",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        logger.info(f"WordPress post status={resp.status_code} for '{title[:50]}'")
        if resp.status_code in (200, 201):
            data = resp.json()
            return {"url": data.get("URL", ""), "id": data.get("ID", "")}
        else:
            logger.error(f"WordPress error: {resp.text[:300]}")
            return {"error": resp.text[:300]}
    except Exception as e:
        logger.error(f"WordPress exception: {e}")
        return {"error": str(e)}

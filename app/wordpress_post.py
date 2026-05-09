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
    clean = re.sub(r'<hr\s*/?>', '', clean)
    clean = re.sub(r'<p[^>]*class="hashtags"[^>]*>.*?</p>', '', clean, flags=re.DOTALL)
    clean = re.sub(r'(<[a-z]+)\s+class="[^"]*"', r'\1', clean)
    if image_url:
        clean = f'<img src="{image_url}" alt=""/>\n' + clean
    return clean.strip()


def _medium_button(slug: str = "") -> str:
    url = f"https://londoncsv.vercel.app/tour/{slug}/medium" if slug else "https://londoncsv.vercel.app"
    return f'\n<p><strong><a href="{url}">Import this article to Medium →</a></strong></p>'


def update_post(access_token: str, post_id: str, content: str) -> dict:
    """Update an existing WordPress post's content."""
    try:
        resp = httpx.post(
            f"{API_BASE}/posts/{post_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"content": content},
            timeout=20,
        )
        if resp.status_code in (200, 201):
            return {"ok": True}
        return {"error": resp.text[:300]}
    except Exception as e:
        return {"error": str(e)}


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
            wp_url = data.get("URL", "")
            post_id = data.get("ID", "")

            # Update post to add Medium import button
            if wp_url and post_id:
                slug = tour.get("slug", "")
                updated_content = clean_content + _medium_button(slug)
                update_post(access_token, post_id, updated_content)

            return {"url": wp_url, "id": post_id}
        else:
            logger.error(f"WordPress error: {resp.text[:300]}")
            return {"error": resp.text[:300]}
    except Exception as e:
        logger.error(f"WordPress exception: {e}")
        return {"error": str(e)}


def get_all_posts(access_token: str) -> list:
    """Fetch all published posts from WordPress."""
    posts = []
    offset = 0
    while True:
        resp = httpx.get(
            f"{API_BASE}/posts",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"status": "publish", "number": 100, "offset": offset, "fields": "ID,URL,content"},
            timeout=30,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        batch = data.get("posts", [])
        if not batch:
            break
        posts.extend(batch)
        offset += len(batch)
        if len(batch) < 100:
            break
    return posts

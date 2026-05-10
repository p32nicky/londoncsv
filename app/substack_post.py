"""
Post London tour articles to Substack via unofficial API.
Publication: nickmdavies.substack.com
"""
import os
import re
import requests

PUBLICATION = "nickmdavies.substack.com"
API_BASE = f"https://{PUBLICATION}/api/v1"
SESSION_COOKIE = os.environ.get("SUBSTACK_SID", "")


def _session():
    s = requests.Session()
    s.cookies.set("substack.sid", SESSION_COOKIE, domain=".substack.com")
    s.headers.update({"Content-Type": "application/json"})
    return s


def _html_to_substack(html: str) -> dict:
    """Convert HTML article to Substack draft body (ProseMirror JSON)."""
    # Substack accepts HTML wrapped in their doc format
    return {
        "type": "doc",
        "content": [
            {
                "type": "html",
                "attrs": {"html": html}
            }
        ]
    }


def post_tour(tour: dict) -> dict:
    """
    Create and publish a Substack post for a tour.
    Returns dict with 'url' on success, 'error' on failure.
    """
    if not SESSION_COOKIE:
        return {"error": "SUBSTACK_SID not set"}

    title = tour.get("title", "")
    article_html = tour.get("article_text", "") or ""
    link = tour.get("link", "")
    description = re.sub(r"<[^>]+>", "", tour.get("description", ""))[:300]
    image_url = tour.get("image_url", "")

    # Fallback if no article
    if not article_html:
        article_html = f"<p>{description}</p>"
    article_html += f'<p><a href="{link}">👉 Book this tour on Viator</a></p>'

    # Prepend image
    if image_url:
        article_html = f'<img src="{image_url}" alt="{title}"/>' + article_html

    s = _session()

    # Step 1: Create draft
    draft_payload = {
        "draft_title": title,
        "draft_subtitle": description,
        "draft_body": article_html,
        "section_chosen": False,
        "audience": "everyone",
        "draft_podcast_url": "",
        "draft_podcast_duration": None,
        "draft_video_upload_id": None,
        "draft_podcast_upload_id": None,
    }

    try:
        resp = s.post(f"{API_BASE}/drafts", json=draft_payload, timeout=20)
        if resp.status_code not in (200, 201):
            return {"error": f"Draft failed {resp.status_code}: {resp.text[:200]}"}

        draft_id = resp.json().get("id")
        if not draft_id:
            return {"error": "No draft ID returned"}

        # Step 2: Publish
        pub_resp = s.post(
            f"{API_BASE}/drafts/{draft_id}/publish",
            json={"send_email": False, "free_unlock": False},
            timeout=20,
        )
        if pub_resp.status_code in (200, 201):
            post_url = pub_resp.json().get("url", f"https://{PUBLICATION}/p/{draft_id}")
            return {"url": post_url, "id": draft_id}
        return {"error": f"Publish failed {pub_resp.status_code}: {pub_resp.text[:200]}"}

    except Exception as e:
        return {"error": str(e)}

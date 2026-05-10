"""
Post London tour articles to Tumblr via NPF API + OAuth1.
Blog: explore-londontours.tumblr.com
"""
import re
import time
import requests
from requests_oauthlib import OAuth1

BLOG = "explore-londontours.tumblr.com"
API_URL = f"https://api.tumblr.com/v2/blog/{BLOG}/post"

CONSUMER_KEY    = "TxHYOvd4AVFPBTiKy3AAAbpr9ztCJdFLa8fzTvSiJ9TV3vR1zx"
CONSUMER_SECRET = "p9rAvpJVp9CakR1XwN08jtXs797HHJumYO4MSRKdCqV14Kuh2x"
TOKEN           = "M18FBknEUfj5MtSzARSTy5FEoJLUReb2UxkccqyUqJ9CyJUkkj"
TOKEN_SECRET    = "XVscoOV1zTOx4xrJfL88ZzBksJPOrt06prA46ZhNi7yo8kMrvW"


def _auth():
    return OAuth1(CONSUMER_KEY, CONSUMER_SECRET, TOKEN, TOKEN_SECRET)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def post_tour(tour: dict) -> dict:
    """
    Post a single tour to Tumblr as a photo post.
    Returns dict with 'id' on success, 'error' on failure.
    """
    title = tour.get("title", "")
    description = _strip_html(tour.get("description", ""))[:300]
    link = tour.get("link", "")  # already TinyURL
    image_url = tour.get("image_url", "")
    keywords = tour.get("keywords", "") or ""

    # Build tags from keywords
    raw = [w.strip() for w in keywords.split(",") if w.strip()]
    tags = ["london", "londontours", "travel", "uk", "viator", "visitlondon"]
    tags += [w.replace(" ", "").lower() for w in raw[:10] if len(w.strip()) > 2]
    tags_str = ",".join(tags[:20])

    # Medium import URL
    slug = tour.get("slug", "")
    medium_url = f"https://londoncsv.vercel.app/tour/{slug}/medium" if slug else link

    caption = f"<b>{title}</b>"
    caption += f"<br/><br/>{description}"
    caption += f"<br/><br/><a href=\"{link}\">👉 Book this tour on Viator</a>"

    data = {
        "type": "photo",
        "caption": caption[:2000],
        "tags": tags_str,
        "link": link,
    }
    if image_url:
        data["source"] = image_url

    try:
        resp = requests.post(API_URL, data=data, auth=_auth(), timeout=15)
        if resp.status_code in (200, 201):
            post_id = resp.json().get("response", {}).get("id", "")
            return {"id": post_id, "url": f"https://explore-londontours.tumblr.com/post/{post_id}"}
        return {"error": resp.text[:300]}
    except Exception as e:
        return {"error": str(e)}

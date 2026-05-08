import logging
import os
from datetime import datetime, timezone, timedelta
from xml.etree.ElementTree import Element, SubElement, tostring
import anthropic
import httpx

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
from requests_oauthlib import OAuth1Session
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import init_db, list_tours, get_latest_tours, get_tour_by_slug, save_article, get_setting, save_setting

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
init_db(settings.db_path)

app = FastAPI(title=settings.site_title)

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: str = Query("", alias="q"),
    page: int = Query(1, ge=1),
):
    per_page = 24
    rows, total = list_tours(settings.db_path, query=q, page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "tours": rows,
        "query": q,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "site_title": settings.site_title,
    })


@app.get("/tour/{slug}", response_class=HTMLResponse)
async def tour_detail(request: Request, slug: str):
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return HTMLResponse("Tour not found", status_code=404)
    return templates.TemplateResponse("tour.html", {
        "request": request,
        "t": tour,
        "site_title": settings.site_title,
    })


@app.get("/tour/{slug}/article", response_class=HTMLResponse)
async def tour_article(request: Request, slug: str):
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return HTMLResponse("Tour not found", status_code=404)
    existing = (dict(tour).get("article_text") or "").strip()
    if existing:
        return templates.TemplateResponse("article.html", {
            "request": request, "t": tour,
            "article_html": existing,
            "tour_url": f"{settings.site_url}/tour/{tour['slug']}",
            "affiliate_link": tour["link"],
            "site_title": settings.site_title,
        })
    return templates.TemplateResponse("article_loading.html", {
        "request": request, "t": tour, "site_title": settings.site_title,
    })


@app.get("/tour/{slug}/medium", response_class=HTMLResponse)
async def tour_medium(request: Request, slug: str):
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return HTMLResponse("Tour not found", status_code=404)
    existing = dict(tour).get("article_text")
    if not existing:
        return HTMLResponse("Article not yet generated. Visit /tour/{}/article first.".format(slug), status_code=404)
    return templates.TemplateResponse("article_medium.html", {
        "request": request,
        "t": tour,
        "article_html": existing,
    })


@app.post("/api/clear-article/{slug}")
async def clear_article(slug: str):
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return JSONResponse({"error": "not found"}, status_code=404)
    save_article(settings.db_path, slug, "")
    return JSONResponse({"status": "cleared"})


@app.post("/api/generate-article/{slug}")
async def generate_article(slug: str):
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return JSONResponse({"error": "not found"}, status_code=404)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "ANTHROPIC_API_KEY not set"}, status_code=500)
    def shorten(url: str) -> str:
        token = os.environ.get("BITLY_TOKEN", "").strip()
        if not token:
            return url
        try:
            r = httpx.post(
                "https://api-ssl.bitly.com/v4/shorten",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"long_url": url},
                timeout=10,
            )
            if r.status_code in (200, 201):
                return r.json().get("link", url)
        except Exception:
            pass
        return url

    try:
        affiliate_link = shorten(tour["link"])
        kw = tour.get("keywords", "") or ""
        STOP = {"a","an","the","and","or","but","in","on","at","to","for","of","with","from","by","as","is","it","this","that","was","are","be","been","has","have","had","not","its"}
        raw_words = [w.strip() for w in kw.split(",") if w.strip()] if kw else []
        good = [w.replace(" ","") for w in raw_words if w.strip().lower() not in STOP and len(w.strip()) > 2]
        tags = " ".join(f"#{w.title()}" for w in good if w)[:200]
        if not tags:
            tags = "#London #Travel #Tours #UK #VisitLondon #TravelUK #LondonTours #Viator #TravelGuide #UKTravel"
        prompt = f"""Write a highly detailed, SEO-optimised travel article about this London tour.

Tour Title: {tour['title']}
Description: {tour['description']}
Keywords: {kw}

Requirements:
- 900-1100 words total
- Catchy SEO-optimised <h1> title (not just the tour name, include keywords like "London", "2024", "best", etc.)
- Engaging hook intro paragraph that grabs attention
- At least 5 <h2> subheadings covering: overview, highlights, what to expect, tips for visitors, why book this tour
- Naturally weave in SEO keywords throughout (London tours, things to do in London, best London experiences, etc.)
- Include specific details about what visitors will see and experience
- Mention ideal visitor types (families, couples, solo travellers, history buffs, etc.)
- Practical tips section (what to wear, when to arrive, what to bring)
- Use <strong> tags to bold key phrases and keywords
- Write in HTML using ONLY <h1> <h2> <p> <strong> <ul> <li> tags
- Do NOT include <html> <head> <body> tags
- Conversational but authoritative tone"""

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-opus-4-7", "max_tokens": 1800, "messages": [{"role": "user", "content": prompt}]},
            timeout=55,
        )
        if resp.status_code != 200:
            return JSONResponse({"error": resp.text}, status_code=500)
        article_html = resp.json()["content"][0]["text"]
        article_html += f"""
<hr/>
<h2>Book This Tour Today</h2>
<p>Don't miss out! <strong><a href="{affiliate_link}" target="_blank" rel="nofollow noopener">Book {tour['title']} on Viator →</a></strong></p>
<p>Secure your spot now — spaces fill up fast!</p>
<p class="hashtags">{tags}</p>"""
        save_article(settings.db_path, slug, article_html)
        return JSONResponse({"status": "done"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/feed.xml")
async def rss_feed():
    day = datetime.now(timezone.utc).timetuple().tm_yday
    daily_offset = (day - 1) * 10
    tours = get_latest_tours(settings.db_path, limit=10, offset=daily_offset)

    rss = Element("rss", version="2.0")
    rss.set("xmlns:media", "http://search.yahoo.com/mrss/")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = settings.site_title
    SubElement(channel, "link").text = settings.site_url
    SubElement(channel, "description").text = "The best London tours and experiences"
    SubElement(channel, "language").text = "en-us"
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for idx, t in enumerate(tours):
        item = SubElement(channel, "item")
        tour_url = f"{settings.site_url}/tour/{t['slug']}"
        unique_guid = f"{tour_url}?d={today}"
        SubElement(item, "title").text = t["title"]
        SubElement(item, "link").text = tour_url
        SubElement(item, "guid", isPermaLink="false").text = unique_guid
        SubElement(item, "description").text = (
            f"{t['description']}<br/>"
            f'<a href="{t["link"]}">Book on Viator →</a>'
        )

        if t["image_url"]:
            enc = SubElement(item, "enclosure")
            enc.set("url", t["image_url"])
            enc.set("type", "image/png")
            enc.set("length", "0")
            media = SubElement(item, "media:content")
            media.set("url", t["image_url"])
            media.set("medium", "image")

        dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0) + timedelta(hours=idx * 2)
        SubElement(item, "pubDate").text = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")

    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(rss, encoding="unicode")
    return Response(content=xml_str, media_type="application/rss+xml")


@app.get("/pinterest-76b6f.html", response_class=HTMLResponse)
async def pinterest_verify():
    path = os.path.join(os.path.dirname(BASE_DIR), "pinterest-76b6f.html")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


def _tumblr_keys():
    ck = os.environ.get("TUMBLR_CONSUMER_KEY", "").strip()
    cs = os.environ.get("TUMBLR_CONSUMER_SECRET", "").strip()
    return ck, cs


@app.get("/tumblr/auth")
async def tumblr_auth():
    try:
        ck, cs = _tumblr_keys()
        if not ck or not cs:
            return JSONResponse({"error": "missing keys", "ck": bool(ck), "cs": bool(cs)})
        callback = f"{settings.site_url}/tumblr/callback"
        oauth = OAuth1Session(ck, cs, callback_uri=callback)
        r = oauth.fetch_request_token("https://www.tumblr.com/oauth/request_token")
        save_setting(settings.db_path, "tumblr_req_token", r["oauth_token"])
        save_setting(settings.db_path, "tumblr_req_secret", r["oauth_token_secret"])
        auth_url = oauth.authorization_url("https://www.tumblr.com/oauth/authorize")
        return RedirectResponse(auth_url)
    except Exception as e:
        import traceback
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, status_code=500)


@app.get("/tumblr/callback", response_class=HTMLResponse)
async def tumblr_callback(oauth_token: str = "", oauth_verifier: str = ""):
    ck, cs = _tumblr_keys()
    req_secret = get_setting(settings.db_path, "tumblr_req_secret")
    oauth = OAuth1Session(ck, cs,
        resource_owner_key=oauth_token,
        resource_owner_secret=req_secret,
        verifier=oauth_verifier)
    tokens = oauth.fetch_access_token("https://www.tumblr.com/oauth/access_token")
    save_setting(settings.db_path, "tumblr_oauth_token", tokens["oauth_token"])
    save_setting(settings.db_path, "tumblr_oauth_secret", tokens["oauth_token_secret"])
    # Get primary blog name
    info = oauth.get("https://api.tumblr.com/v2/user/info").json()
    blogs = info.get("response", {}).get("user", {}).get("blogs", [])
    blog_name = next((b["name"] for b in blogs if b.get("primary")), blogs[0]["name"] if blogs else "")
    save_setting(settings.db_path, "tumblr_blog_name", blog_name)
    return HTMLResponse(f"""
    <h2>✅ Tumblr connected!</h2>
    <p>Blog: <strong>{blog_name}.tumblr.com</strong></p>
    <p>You can close this tab and go back to your articles.</p>
    """)


@app.post("/api/post-tumblr/{slug}")
async def post_tumblr(slug: str):
    ck, cs = _tumblr_keys()
    token = get_setting(settings.db_path, "tumblr_oauth_token")
    secret = get_setting(settings.db_path, "tumblr_oauth_secret")
    blog = get_setting(settings.db_path, "tumblr_blog_name")
    if not all([token, secret, blog]):
        return JSONResponse({"error": "not_connected"}, status_code=400)
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return JSONResponse({"error": "not found"}, status_code=404)
    article_html = (dict(tour).get("article_text") or "").strip()
    if not article_html:
        return JSONResponse({"error": "no_article"}, status_code=400)
    # Extract title from H1
    import re
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", article_html, re.IGNORECASE | re.DOTALL)
    title = re.sub(r"<[^>]+>", "", h1.group(1)).strip() if h1 else tour["title"]
    # Extract tags from hashtags paragraph
    tags_match = re.search(r'class="hashtags">(.*?)</p>', article_html, re.DOTALL)
    tags_text = tags_match.group(1) if tags_match else ""
    tags = ",".join(t.lstrip("#") for t in tags_text.split() if t.startswith("#"))
    oauth = OAuth1Session(ck, cs, token, secret)
    resp = oauth.post(
        f"https://api.tumblr.com/v2/blog/{blog}/post",
        json={"type": "text", "title": title, "body": article_html, "tags": tags, "state": "published"},
    )
    if resp.status_code in (200, 201):
        post_id = resp.json().get("response", {}).get("id", "")
        return JSONResponse({"status": "posted", "url": f"https://{blog}.tumblr.com/post/{post_id}"})
    return JSONResponse({"error": resp.text}, status_code=500)


@app.get("/api/test-bitly")
async def test_bitly():
    token = os.environ.get("BITLY_TOKEN", "").strip()
    if not token:
        return JSONResponse({"error": "no token"})
    try:
        r = httpx.post(
            "https://api-ssl.bitly.com/v4/shorten",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"long_url": "https://www.viator.com/tours/London/test"},
            timeout=10,
        )
        return JSONResponse({"status": r.status_code, "body": r.json()})
    except Exception as e:
        return JSONResponse({"error": str(e)})


@app.get("/api/status")
async def status():
    _, total = list_tours(settings.db_path, per_page=1)
    return JSONResponse({"total_tours": total})

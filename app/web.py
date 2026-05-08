import logging
import os
from datetime import datetime, timezone, timedelta
from xml.etree.ElementTree import Element, SubElement, tostring
import anthropic
import httpx

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
import secrets
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
            json={"model": "claude-haiku-4-5", "max_tokens": 1800, "messages": [{"role": "user", "content": prompt}]},
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


TUMBLR_CALLBACK = "https://londoncsv.vercel.app/tumblr/callback"


def _tumblr_keys():
    ck = os.environ.get("TUMBLR_CONSUMER_KEY", "").strip()
    cs = os.environ.get("TUMBLR_CONSUMER_SECRET", "").strip()
    return ck, cs


@app.get("/tumblr/auth")
async def tumblr_auth():
    ck, _ = _tumblr_keys()
    state = secrets.token_urlsafe(16)
    save_setting(settings.db_path, "tumblr_state", state)
    url = (
        f"https://www.tumblr.com/oauth2/authorize"
        f"?client_id={ck}"
        f"&response_type=code"
        f"&scope=basic+write"
        f"&redirect_uri={TUMBLR_CALLBACK}"
        f"&state={state}"
    )
    return RedirectResponse(url)


@app.get("/tumblr/callback", response_class=HTMLResponse)
async def tumblr_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(f"<h2>❌ Tumblr denied: {error}</h2>")
    saved_state = get_setting(settings.db_path, "tumblr_state") or ""
    if state != saved_state:
        return HTMLResponse("<h2>❌ State mismatch — try again</h2>", status_code=400)
    ck, cs = _tumblr_keys()
    try:
        r = httpx.post("https://api.tumblr.com/v2/oauth2/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": ck,
            "client_secret": cs,
            "redirect_uri": TUMBLR_CALLBACK,
        }, timeout=15)
        if r.status_code != 200:
            return HTMLResponse(f"<h2>❌ Token error</h2><pre>{r.text}</pre>", status_code=500)
        tokens = r.json()
        access_token = tokens["access_token"]
        save_setting(settings.db_path, "tumblr_access_token", access_token)
        # Get blog info
        info = httpx.get("https://api.tumblr.com/v2/user/info",
            headers={"Authorization": f"Bearer {access_token}"}, timeout=10).json()
        blogs = info.get("response", {}).get("user", {}).get("blogs", [])
        communities = info.get("response", {}).get("user", {}).get("communities", [])
        blog_name = next((b["name"] for b in blogs if b.get("primary")), blogs[0]["name"] if blogs else "")
        save_setting(settings.db_path, "tumblr_blog_name", blog_name)
        # Save community UUIDs for later use
        for c in communities:
            save_setting(settings.db_path, f"tumblr_community_{c['name']}", c["uuid"])
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:500px;margin:4rem auto;padding:1rem">
        <h2>✅ Tumblr connected!</h2>
        <p>Blog: <strong>{blog_name}.tumblr.com</strong></p>
        <p><a href="/">← Back to tours</a></p>
        </body></html>""")
    except Exception as e:
        return HTMLResponse(f"<h2>❌ Error</h2><pre>{e}</pre>", status_code=500)


@app.get("/api/tumblr-blogs")
async def tumblr_blogs():
    token = get_setting(settings.db_path, "tumblr_access_token")
    if not token:
        return JSONResponse({"error": "not connected"})
    r = httpx.get("https://api.tumblr.com/v2/user/info",
        headers={"Authorization": f"Bearer {token}"}, timeout=10)
    return JSONResponse(r.json())


@app.get("/tumblr/set-blog", response_class=HTMLResponse)
async def tumblr_set_blog(name: str = ""):
    if name:
        save_setting(settings.db_path, "tumblr_blog_name", name.strip())
        return HTMLResponse(f"<h2>✅ Blog set to <strong>{name}</strong></h2><p><a href='/'>← Back</a></p>")
    current = get_setting(settings.db_path, "tumblr_blog_name") or ""
    return HTMLResponse(f"""
    <html><body style="font-family:sans-serif;max-width:500px;margin:4rem auto;padding:1rem">
    <h2>Set Tumblr Blog</h2>
    <p>Current: <strong>{current}</strong></p>
    <form method="get">
      <input name="name" value="{current}" style="padding:0.5rem;width:100%;font-size:1rem;margin-bottom:1rem"/>
      <button type="submit" style="background:#35465c;color:#fff;padding:0.6rem 1.2rem;border:none;border-radius:6px;font-size:1rem;cursor:pointer">Save</button>
    </form>
    </body></html>""")


@app.post("/api/post-tumblr/{slug}")
async def post_tumblr(slug: str):
    import re
    token = get_setting(settings.db_path, "tumblr_access_token")
    blog = get_setting(settings.db_path, "tumblr_blog_name")
    if not token or not blog:
        return JSONResponse({"error": "not_connected"}, status_code=400)
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return JSONResponse({"error": "not found"}, status_code=404)
    article_html = (dict(tour).get("article_text") or "").strip()
    if not article_html:
        return JSONResponse({"error": "no_article"}, status_code=400)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", article_html, re.IGNORECASE | re.DOTALL)
    title = re.sub(r"<[^>]+>", "", h1.group(1)).strip() if h1 else tour["title"]
    tags_match = re.search(r'class="hashtags">(.*?)</p>', article_html, re.DOTALL)
    tags_text = tags_match.group(1) if tags_match else ""
    tags = [t.lstrip("#") for t in tags_text.split() if t.startswith("#")]
    community_uuid = get_setting(settings.db_path, f"tumblr_community_{blog}")
    is_community = bool(community_uuid)

    # Try multiple approaches for communities
    attempts = []
    if is_community:
        encoded_uuid = community_uuid.replace(":", "%3A")
        attempts = [
            (f"https://api.tumblr.com/v2/blog/{encoded_uuid}/post", {"type": "text", "title": title, "body": article_html, "tags": tags}),
            (f"https://api.tumblr.com/v2/blog/{blog}/post", {"type": "text", "title": title, "body": article_html, "tags": tags}),
            (f"https://api.tumblr.com/v2/blog/{encoded_uuid}/posts", {"content": [{"type": "text", "text": f"<h1>{title}</h1>\n{article_html}"}], "tags": tags}),
        ]
    else:
        attempts = [(f"https://api.tumblr.com/v2/blog/{blog}/post", {"type": "text", "title": title, "body": article_html, "tags": tags, "state": "published"})]

    last_resp = None
    for endpoint, payload in attempts:
        resp = httpx.post(endpoint, headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=20)
        logger.info(f"Tumblr {endpoint}: {resp.status_code} {resp.text[:200]}")
        last_resp = resp
        if resp.status_code in (200, 201):
            post_id = resp.json().get("response", {}).get("id", "")
            url = f"https://www.tumblr.com/communities/{blog}" if is_community else f"https://{blog}.tumblr.com/post/{post_id}"
            return JSONResponse({"status": "posted", "url": url})

    return JSONResponse({"error": last_resp.text if last_resp else "no attempts"}, status_code=500)


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

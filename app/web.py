import logging
import os
from datetime import datetime, timezone, timedelta
from xml.etree.ElementTree import Element, SubElement, tostring
import anthropic
import httpx

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import init_db, list_tours, get_latest_tours, get_tour_by_slug, save_article

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
    existing = dict(tour).get("article_text")
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


@app.post("/api/generate-article/{slug}")
async def generate_article(slug: str):
    tour = get_tour_by_slug(settings.db_path, slug)
    if not tour:
        return JSONResponse({"error": "not found"}, status_code=404)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "ANTHROPIC_API_KEY not set"}, status_code=500)
    try:
        affiliate_link = tour["link"]
        kw = tour.get("keywords", "") or tour["title"]
        tags = " ".join(f"#{w.strip().replace(' ','').title()}" for w in kw.split(",") if w.strip())[:200]
        if not tags:
            tags = "#London #Travel #Tours #UK #VisitLondon #TravelUK #LondonTours #Viator #TravelGuide #UKTravel"
        prompt = f"""Write a detailed SEO-optimised travel article about this London tour.
Title: {tour['title']}
Description: {tour['description']}
Requirements: 500-600 words, H2 subheadings, engaging intro, tips, written in HTML using only <h1><h2><p><strong> tags, no html/head/body tags, catchy SEO h1 title."""

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-opus-4-7", "max_tokens": 800, "messages": [{"role": "user", "content": prompt}]},
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


@app.get("/api/status")
async def status():
    _, total = list_tours(settings.db_path, per_page=1)
    return JSONResponse({"total_tours": total})

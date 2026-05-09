"""
Daily sync — generates article for next unpublished tour, posts to WordPress.com.
Run via GitHub Actions.
"""
import os
import re
import sys
import httpx

env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from app.config import get_settings
from app.db import init_db, get_next_unpublished, save_article, get_tour_by_slug, mark_tumblr_posted
from app.wordpress_post import post_article

STOP = {"a","an","the","and","or","but","in","on","at","to","for","of","with","from","by","as","is","it","this","that","was","are","be","been","has","have","had","not","its"}

BATCH = int(os.environ.get("WP_BATCH", "3"))  # articles per run


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


def generate_article(tour: dict, api_key: str) -> str:
    affiliate_link = shorten(tour["link"])
    kw = tour.get("keywords", "") or ""
    raw_words = [w.strip() for w in kw.split(",") if w.strip()] if kw else []
    good = [w.replace(" ", "") for w in raw_words if w.strip().lower() not in STOP and len(w.strip()) > 2]
    tags_str = " ".join(f"#{w.title()}" for w in good if w)[:200]
    if not tags_str:
        tags_str = "#London #Travel #Tours #UK #VisitLondon #TravelUK #LondonTours #Viator #TravelGuide #UKTravel"

    prompt = f"""Write a highly detailed, SEO-optimised travel article about this London tour.

Tour Title: {tour['title']}
Description: {tour['description']}
Keywords: {kw}

Requirements:
- 900-1100 words total
- Catchy SEO-optimised <h1> title (not just the tour name, include keywords like "London", "2025", "best", etc.)
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
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Claude error: {resp.text[:200]}")
    article_html = resp.json()["content"][0]["text"]
    article_html += f"""
<hr/>
<h2>Book This Tour Today</h2>
<p>Don't miss out! <strong><a href="{affiliate_link}" target="_blank" rel="nofollow noopener">Book {tour['title']} on Viator →</a></strong></p>
<p>Secure your spot now — spaces fill up fast!</p>
<p class="hashtags">{tags_str}</p>"""
    return article_html


def main():
    settings = get_settings()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    wp_token = os.environ.get("WP_ACCESS_TOKEN", "")

    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set"); sys.exit(1)
    if not wp_token:
        print("ERROR: WP_ACCESS_TOKEN not set"); sys.exit(1)

    init_db(settings.db_path)

    posted = 0
    for i in range(BATCH):
        tour = get_next_unpublished(settings.db_path)
        if not tour:
            print("All tours already published to WordPress!"); break

        slug = tour["slug"]
        print(f"\n[{i+1}/{BATCH}] Processing: {tour['title'][:60]}")

        # Generate article if missing
        article_html = (dict(tour).get("article_text") or "").strip()
        if not article_html:
            print(f"  Generating article...")
            try:
                article_html = generate_article(dict(tour), api_key)
                save_article(settings.db_path, slug, article_html)
                print(f"  Article saved ({len(article_html)} chars)")
            except Exception as e:
                print(f"  FAILED to generate: {e}"); continue

        # Post to WordPress
        print(f"  Posting to WordPress.com...")
        result = post_article(wp_token, dict(tour), article_html)
        if "url" in result:
            mark_tumblr_posted(settings.db_path, slug)  # reuse posted flag
            posted += 1
            print(f"  ✅ Published: {result['url']}")
        else:
            print(f"  ❌ Failed: {result.get('error', '?')[:200]}")

    print(f"\nDone. Posted {posted}/{BATCH} articles to WordPress.")


if __name__ == "__main__":
    main()

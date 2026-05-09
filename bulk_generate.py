"""
Bulk generate articles for all tours with Bitly links.
Run locally: python bulk_generate.py
"""
import os, re, time, httpx

env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

DATABASE_URL = "postgresql://neondb_owner:npg_Nq8ZoKMlD1nt@ep-green-sound-angzcs1z-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BITLY_TOKEN = "269c53e1b2eb6dcb2035d4d6ecfac4f2105ce35a"

os.environ["DATABASE_URL"] = DATABASE_URL

from app.db import _get_conn, save_article

STOP = {"a","an","the","and","or","but","in","on","at","to","for","of","with","from","by","as","is","it","this","that","was","are","be","been","has","have","had","not","its"}


def shorten(url):
    try:
        r = httpx.post("https://api-ssl.bitly.com/v4/shorten",
            headers={"Authorization": f"Bearer {BITLY_TOKEN}", "Content-Type": "application/json"},
            json={"long_url": url}, timeout=8)
        if r.status_code in (200, 201):
            return r.json().get("link", url)
    except Exception:
        pass
    return url


def generate(tour):
    affiliate_link = shorten(tour["link"])
    kw = tour.get("keywords", "") or ""
    raw = [w.strip() for w in kw.split(",") if w.strip()]
    good = [w.replace(" ","") for w in raw if w.strip().lower() not in STOP and len(w.strip()) > 2]
    tags_str = " ".join(f"#{w.title()}" for w in good if w)[:200] or "#London #Travel #Tours #UK #VisitLondon"

    prompt = f"""Write a highly detailed, SEO-optimised travel article about this London tour.
Tour Title: {tour['title']}
Description: {tour['description']}
Keywords: {kw}
Requirements:
- 900-1100 words total
- Catchy SEO-optimised <h1> title
- Engaging hook intro paragraph
- At least 5 <h2> subheadings: overview, highlights, what to expect, tips, why book
- Weave in SEO keywords naturally
- Use <strong> for key phrases
- HTML only: <h1> <h2> <p> <strong> <ul> <li> tags
- No <html><head><body> tags"""

    resp = httpx.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5", "max_tokens": 1800, "messages": [{"role": "user", "content": prompt}]},
        timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(resp.text[:200])
    html = resp.json()["content"][0]["text"]
    html += f"""
<hr/>
<h2>Book This Tour Today</h2>
<p>Don't miss out! <strong><a href="{affiliate_link}" target="_blank" rel="nofollow noopener">Book {tour['title']} on Viator →</a></strong></p>
<p>Secure your spot now — spaces fill up fast!</p>
<p class="hashtags">{tags_str}</p>"""
    return html


def main():
    with _get_conn(DATABASE_URL) as conn:
        cur = conn.cursor()
        cur.execute("SELECT slug, title, description, link, keywords FROM tours WHERE article_text IS NULL ORDER BY id")
        tours = [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]

    print(f"{len(tours)} tours to generate")
    ok = 0
    for i, tour in enumerate(tours):
        try:
            html = generate(tour)
            save_article(DATABASE_URL, tour["slug"], html)
            ok += 1
            print(f"[{i+1}/{len(tours)}] ✅ {tour['title'][:50]}")
        except Exception as e:
            print(f"[{i+1}/{len(tours)}] ❌ {e}")
        time.sleep(0.5)  # rate limit

    print(f"\nDone. {ok}/{len(tours)} generated.")


if __name__ == "__main__":
    main()

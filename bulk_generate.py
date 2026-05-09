"""
Bulk generate articles for all tours using Groq (free).
Run via GitHub Actions or locally.
"""
import os, time, httpx, psycopg2

DATABASE_URL = "postgresql://neondb_owner:npg_Nq8ZoKMlD1nt@ep-green-sound-angzcs1z-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

STOP = {"a","an","the","and","or","but","in","on","at","to","for","of","with","from","by","as","is","it","this","that","was","are","be","been","has","have","had","not","its"}


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_column():
    """One-time migration — add article_text column if missing."""
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE tours ADD COLUMN IF NOT EXISTS article_text TEXT")
    conn.close()


def save_article(slug, html):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE tours SET article_text=%s WHERE slug=%s", (html, slug))
    conn.commit()
    conn.close()


def generate(tour):
    kw = tour.get("keywords", "") or ""
    raw = [w.strip() for w in kw.split(",") if w.strip()]
    good = [w.replace(" ", "") for w in raw if w.strip().lower() not in STOP and len(w.strip()) > 2]
    tags_str = " ".join(f"#{w.title()}" for w in good if w)[:200] or "#London #Travel #Tours #UK #VisitLondon"
    affiliate_link = tour["link"]  # already TinyURL

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

    resp = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "llama-3.3-70b-versatile",
            "max_tokens": 1800,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(resp.text[:300])
    html = resp.json()["choices"][0]["message"]["content"]
    html += f"""
<hr/>
<h2>Book This Tour Today</h2>
<p>Don't miss out! <strong><a href="{affiliate_link}" target="_blank" rel="nofollow noopener">Book {tour['title']} on Viator</a></strong></p>
<p>Secure your spot now — spaces fill up fast!</p>
<p class="hashtags">{tags_str}</p>"""
    return html


def main():
    ensure_column()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT slug, title, description, link, keywords
        FROM tours WHERE article_text IS NULL OR article_text = ''
        ORDER BY id
    """)
    cols = [d[0] for d in cur.description]
    tours = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()

    print(f"{len(tours)} tours to generate")
    ok = 0
    for i, tour in enumerate(tours):
        try:
            html = generate(tour)
            save_article(tour["slug"], html)
            ok += 1
            print(f"[{i+1}/{len(tours)}] OK {tour['title'][:50]}")
        except Exception as e:
            print(f"[{i+1}/{len(tours)}] FAIL {e}")
        time.sleep(0.3)

    print(f"\nDone. {ok}/{len(tours)} generated.")


if __name__ == "__main__":
    main()

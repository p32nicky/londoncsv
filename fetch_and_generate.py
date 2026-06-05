"""Fetch new Viator London tours + generate articles — runs weekly in GitHub Actions."""
import os, re, time, httpx, psycopg2

VIATOR_KEY = os.environ["VIATOR_KEY"]
GROQ_KEY   = os.environ["GROQ_KEY"]
VIATOR_URL = "https://api.viator.com/partner/search/freetext"
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"

VIATOR_HEADERS = {
    "exp-api-key": VIATOR_KEY,
    "Accept": "application/json;version=2.0",
    "Accept-Language": "en-US",
    "Content-Type": "application/json",
}

SEARCH_TERMS = [
    "London tours", "London day trips", "London attractions tickets",
    "London walking tours", "London boat tours", "London food tours",
    "Stonehenge from London", "Windsor Castle tours", "Harry Potter London",
    "London Eye tickets", "Tower of London", "Buckingham Palace tours",
    "Oxford day trips from London", "Cotswolds from London",
]

def get_conn():
    url = os.environ["DATABASE_URL"]
    at = url.rfind("@"); ui = url[url.index("://")+3:at]; hi = url[at+1:]
    ci = ui.index(":"); user, pw = ui[:ci], ui[ci+1:]
    hp, db = hi.split("?")[0].rsplit("/", 1)
    host, port = hp.rsplit(":", 1) if ":" in hp else (hp, "5432")
    return psycopg2.connect(host=host, port=int(port), dbname=db,
                            user=user, password=pw, sslmode="require")

def make_slug(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:70]

def fetch_viator(term, start=1, count=50):
    body = {
        "searchTerm": term, "currency": "GBP",
        "searchTypes": [{"searchType": "PRODUCTS", "pagination": {"start": start, "count": count}}],
        "productSorting": {"sort": "REVIEW_AVG_RATING"},
    }
    r = httpx.post(VIATOR_URL, headers=VIATOR_HEADERS, json=body, timeout=30)
    if r.status_code != 200:
        print(f"  Viator {r.status_code}")
        return []
    data = r.json()
    products = data.get("products") or {}
    return products.get("results", [])

def generate_article(title, description, link):
    btn = (f'<p style="margin:20px 0"><a href="{link}" target="_blank" rel="nofollow noopener" '
           f'style="display:inline-block;background:#8B1A1A;color:#fff;padding:14px 28px;'
           f'border-radius:8px;text-decoration:none;font-weight:bold">Book on Viator &rarr;</a></p>')
    prompt = f"""Write a detailed travel article about this London tour.
Title: {title}
Description: {description}
Structure (HTML — h1 h2 p strong ul li only):
1. <h1> SEO title, 2. Hook para, 3. <h2>What to Expect</h2>,
4. <h2>Highlights</h2> bullets, 5. <h2>The Experience</h2>,
6. <h2>Pros and Cons</h2>, 7. <h2>Who Is This For?</h2>,
8. <h2>Practical Tips</h2>, 9. <h2>Our Verdict</h2>
800-1000 words. No booking CTAs. No wrapper tags."""

    for attempt in range(4):
        try:
            r = httpx.post(GROQ_URL,
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={"model": "llama-3.1-8b-instant", "max_tokens": 1800,
                      "messages": [{"role": "user", "content": prompt}],
                      "stop": ["<|", "[INST]", "```"]},
                timeout=60)
            if r.status_code == 429:
                wait = 15 * (attempt + 1)
                print(f"    rate limit - wait {wait}s")
                time.sleep(wait)
                continue
            if r.status_code == 200:
                html = r.json()["choices"][0]["message"]["content"].strip()
                html = re.sub(r"<\|.*?\|>", "", html)
                html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
                html = re.sub(r"(</p>)", r"\1" + btn, html, count=1)
                html += f"\n{btn}"
                html += "\n<p><small>Affiliate link - we may earn a commission.</small></p>"
                return html
        except Exception as e:
            print(f"    err: {e}")
    return f"<p>{description}</p>{btn}"

def main():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT slug FROM tours")
    existing = {r[0] for r in cur.fetchall()}
    print(f"Existing: {len(existing)} tours")
    conn.close()

    total_new = 0
    for term in SEARCH_TERMS:
        print(f"\n=== {term} ===")
        products = fetch_viator(term, 1, 50)
        if len(products) == 50:
            products += fetch_viator(term, 51, 50)
        print(f"  Got {len(products)}")

        for p in products:
            title = p.get("title", "").strip()
            desc  = (p.get("description") or p.get("shortDescription") or "").strip()[:500]
            link  = p.get("productUrl", "")
            if not title or not link:
                continue
            if "?" in link:
                link += "&target_lander=NONE"
            else:
                link += "?target_lander=NONE"

            imgs = p.get("images", [])
            img = ""
            if imgs:
                for v in imgs[0].get("variants", []):
                    if v.get("width", 0) >= 400:
                        img = v.get("url", ""); break
                if not img and imgs[0].get("variants"):
                    img = imgs[0]["variants"][0].get("url", "")

            slug = make_slug(title)
            if slug in existing:
                continue

            print(f"  NEW: {title[:55]}")
            article = generate_article(title, desc, link)

            conn = get_conn()
            try:
                conn.cursor().execute("""
                    INSERT INTO tours (slug, title, description, link, image_url, article_text)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (slug) DO NOTHING
                """, (slug, title, desc, link, img, article))
                conn.commit()
                existing.add(slug)
                total_new += 1
            except Exception as e:
                print(f"    DB err: {e}")
                conn.rollback()
            finally:
                conn.close()
            time.sleep(0.5)

    print(f"\nDone. Added {total_new} new tours.")

if __name__ == "__main__":
    main()

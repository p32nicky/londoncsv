"""
Alternating Substack poster — Viator (DB) and Headout (sitemap), 1 post per run.
State stored in Supabase — runs in GitHub Actions hourly.
"""
import os, re, time, json, psycopg2, httpx
import requests as std_requests
from html import unescape
from urllib.parse import unquote
from datetime import datetime, timezone
from curl_cffi import requests

# ── Config (from env vars / GitHub secrets) ───────────────────────────────────
PUBLICATION   = "nickmdavies.substack.com"
API_BASE      = f"https://{PUBLICATION}/api/v1"
GROQ_KEY      = os.environ["GROQ_KEY"]
HEADOUT_AKEY  = os.environ["HEADOUT_AKEY"]
DATABASE_URL  = os.environ["DATABASE_URL"]
SESSION_COOKIE= os.environ["SUBSTACK_SID"]
CF_CLEARANCE  = os.environ.get("CF_CLEARANCE", "")
SUBSTACK_LLI  = os.environ.get("SUBSTACK_LLI", "")

SCRAPE_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SKIP_CATS  = {"jet-skiing","nightlife","train-tickets","airport-transfers",
              "cruise-port-transfers","helicopter","hard-rock-cafe","escape-rooms",
              "transfer","transfers"}


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    if DATABASE_URL:
        # Parse safely (password may contain special chars)
        url = DATABASE_URL
        at = url.rfind("@")
        userinfo = url[url.index("://")+3:at]
        hostinfo = url[at+1:]
        colon = userinfo.index(":")
        user, password = userinfo[:colon], userinfo[colon+1:]
        hostpart = hostinfo.split("?")[0]
        hp, dbname = hostpart.rsplit("/", 1)
        host, port = (hp.rsplit(":", 1) if ":" in hp else (hp, "5432"))
        return psycopg2.connect(host=host, port=int(port), dbname=dbname,
                                user=user, password=password, sslmode="require")
    return psycopg2.connect(
        host="aws-1-us-east-1.pooler.supabase.com", port=6543,
        dbname="postgres", user="postgres.ijmhnhzydouqcifvrpss",
        password="P32nicky!!??", sslmode="require")


def get_setting(key, default=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key, value):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO settings (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (key, value))
    conn.commit()
    conn.close()


def is_headout_posted(url):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM headout_posted WHERE url=%s", (url,))
    result = cur.fetchone() is not None
    conn.close()
    return result


def mark_headout_posted(url):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO headout_posted (url, posted_at) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (url, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def reset_headout_posted():
    conn = get_db()
    conn.cursor().execute("DELETE FROM headout_posted")
    conn.commit()
    conn.close()


# ── Groq article ──────────────────────────────────────────────────────────────

def groq_article(title, description):
    prompt = f"""Write a short engaging travel article (350-450 words) about this London experience.

Title: {title}
Description: {description}

Write 3-4 paragraphs: what it is, highlights, who it suits, why book.
Tone: enthusiastic honest travel writer. No booking CTAs. Plain text only."""

    for attempt in range(3):
        try:
            r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
                json={"model": "llama-3.1-8b-instant", "max_tokens": 600,
                      "messages": [{"role": "user", "content": prompt}],
                      "stop": ["<|", "[INST]"]},
                timeout=30)
            if r.status_code == 429:
                time.sleep(15)
                continue
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            pass
    return description


def build_doc(title, description, link, source):
    brand = "Headout" if source == "headout" else "Viator"
    article = groq_article(title, description)

    nodes = []
    for para in article.split("\n\n"):
        para = para.strip()
        if para:
            nodes.append({"type": "paragraph",
                          "content": [{"type": "text", "text": para}]})

    nodes.append({"type": "paragraph", "content": [{
        "type": "text",
        "text": f"Book on {brand} ->",
        "marks": [{"type": "link", "attrs": {"href": link, "target": "_blank"}}]
    }]})
    nodes.append({"type": "paragraph", "content": [{
        "type": "text", "marks": [{"type": "italic"}],
        "text": "Affiliate link - we may earn a small commission at no extra cost to you."
    }]})
    return json.dumps({"type": "doc", "content": nodes})


# ── Substack ──────────────────────────────────────────────────────────────────

def get_session():
    s = requests.Session(impersonate="chrome120")
    # Do NOT send cf_clearance - it's IP-bound to the user's browser.
    # curl_cffi browser impersonation handles Cloudflare via TLS fingerprint.
    s.cookies.update({"substack.sid": unquote(SESSION_COOKIE),
                      "substack.lli": SUBSTACK_LLI})
    if CF_CLEARANCE:
        s.cookies.set("cf_clearance", CF_CLEARANCE)
    s.headers.update({"Referer": f"https://{PUBLICATION}/publish/post",
                      "Origin":  f"https://{PUBLICATION}"})
    # First hit homepage to let Cloudflare set cookies, then get CSRF
    s.get(f"https://{PUBLICATION}", timeout=15)
    r = s.get(f"https://{PUBLICATION}/publish/post", timeout=15)
    print(f"Publish page status: {r.status_code}")
    csrf = re.search(r'"csrf_token"\s*:\s*"([^"]+)"', r.text)
    if csrf:
        s.headers["X-CSRF-Token"] = csrf.group(1)
        print("CSRF: found")
    else:
        print(f"CSRF: not found ({r.status_code})")
        print(f"Snippet: {r.text[:300]}")
    return s


def get_user_id(session):
    r = session.get(f"https://{PUBLICATION}/api/v1/publication", timeout=10)
    d = r.json() if r.status_code == 200 else {}
    uid = d.get("author_id") or d.get("user_id")
    if not uid:
        r2 = session.get(f"https://{PUBLICATION}/api/v1/user", timeout=10)
        uid = r2.json().get("id") if r2.status_code == 200 else None
    return uid


def publish(session, user_id, title, description, link, image_url, source):
    doc = build_doc(title, description, link, source)
    payload = {
        "draft_title":    title,
        "draft_subtitle": description[:200],
        "draft_body":     doc,
        "audience":       "everyone",
        "section_chosen": False,
        "draft_bylines":  [{"id": user_id, "is_guest": False}] if user_id else [],
        "cover_image":    image_url or None,
    }
    resp = session.post(f"{API_BASE}/drafts", json=payload, timeout=20)
    if resp.status_code not in (200, 201):
        return {"error": f"{resp.status_code}: {resp.text[:200]}"}
    draft_id = resp.json().get("id")
    if not draft_id:
        return {"error": "No draft ID"}
    pub = session.post(f"{API_BASE}/drafts/{draft_id}/publish",
                       json={"send_email": False}, timeout=20)
    if pub.status_code in (200, 201):
        return {"url": pub.json().get("url", "")}
    return {"error": f"Publish {pub.status_code}: {pub.text[:200]}"}


# ── Viator ────────────────────────────────────────────────────────────────────

def post_viator(session, user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""SELECT slug, title, description, link, image_url
                   FROM tours WHERE substack_posted_at IS NULL
                   AND link IS NOT NULL AND link != ''
                   ORDER BY id LIMIT 1""")
    row = cur.fetchone()
    if not row:
        cur.execute("UPDATE tours SET substack_posted_at=NULL")
        conn.commit()
        cur.execute("""SELECT slug, title, description, link, image_url
                       FROM tours WHERE link IS NOT NULL ORDER BY id LIMIT 1""")
        row = cur.fetchone()
    conn.close()
    if not row:
        return False

    slug, title, desc, link, img = row
    print(f"[VIATOR] {title[:60]}")
    result = publish(session, user_id, title or "", desc or "", link, img or "", "viator")

    if "url" in result:
        conn2 = get_db()
        conn2.cursor().execute("UPDATE tours SET substack_posted_at=%s WHERE slug=%s",
                               (datetime.now(timezone.utc).isoformat(), slug))
        conn2.commit()
        conn2.close()
        print(f"OK: {result['url']}")
        return True
    print(f"FAIL: {result['error']}")
    return False


# ── Headout ───────────────────────────────────────────────────────────────────

def get_headout_urls():
    r = std_requests.get("https://www.headout.com/products-sitemap.xml",
                         headers=SCRAPE_HDR, timeout=20)
    all_urls = re.findall(r'<loc>(https://www\.headout\.com/[^<]+)</loc>', r.text)
    out = []
    for url in all_urls:
        if "-e-" not in url:
            continue
        cat = url.replace("https://www.headout.com/","").split("/")[0]
        if any(s in cat for s in SKIP_CATS):
            continue
        if ("london" in url.lower() or
            any(k in cat for k in ["stonehenge","cotswold","windsor","oxford",
                                   "bath","cambridge","canterbury","dover"])):
            out.append(url)
    return out


def post_headout(session, user_id):
    all_urls = get_headout_urls()
    remaining = [u for u in all_urls if not is_headout_posted(u)]
    if not remaining:
        reset_headout_posted()
        remaining = all_urls

    for url in remaining:
        try:
            r = std_requests.get(url, headers=SCRAPE_HDR, timeout=15)
            title = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
            desc  = re.search(r'<meta property="og:description" content="([^"]+)"', r.text)
            img   = re.search(r'(https://cdn-imgix\.headout\.com/[^\s"\']+\.(?:jpg|jpeg|png|webp))', r.text)
            price = re.search(r'"price":\s*"?(\d+\.?\d*)"?', r.text)
            if not title:
                mark_headout_posted(url)
                continue
            # Skip free/zero-price listings
            price_val = float(price.group(1)) if price else 0
            if price_val == 0:
                print(f"  SKIP (price=0): {url.split('headout.com')[1][:60]}")
                mark_headout_posted(url)
                continue
            link = url.rstrip("/") + f"/?refId={HEADOUT_AKEY}"
            t = unescape(title.group(1)).strip()
            d = unescape(desc.group(1)).strip()[:400] if desc else ""
            i = img.group(1) if img else ""
        except Exception as e:
            print(f"Scrape err: {e}")
            mark_headout_posted(url)
            continue

        print(f"[HEADOUT] {t[:60]}")
        result = publish(session, user_id, t, d, link, i, "headout")
        mark_headout_posted(url)

        if "url" in result:
            print(f"OK: {result['url']}")
            return True
        print(f"FAIL: {result['error']}")
        return False

    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    last = get_setting("substack_last_source", "headout")
    source = "viator" if last == "headout" else "headout"
    print(f"Last: {last} -> Now: {source}")

    session = get_session()
    user_id = get_user_id(session)
    print(f"User: {user_id}")

    ok = post_viator(session, user_id) if source == "viator" else post_headout(session, user_id)

    if ok:
        set_setting("substack_last_source", source)


if __name__ == "__main__":
    main()

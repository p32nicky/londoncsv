"""
Substack poster — Viator tours from the DB, 1 post per run.
State stored in Supabase — runs locally via Windows task (CF blocks GitHub IPs).
"""
import os, re, time, json, psycopg2, httpx
from html import unescape
from urllib.parse import unquote
from datetime import datetime, timezone
from curl_cffi import requests

# ── Config (from env vars / GitHub secrets) ───────────────────────────────────
PUBLICATION   = "nickmdavies.substack.com"
API_BASE      = f"https://{PUBLICATION}/api/v1"
GROQ_KEY      = os.environ["GROQ_KEY"]
DATABASE_URL  = os.environ["DATABASE_URL"]
SESSION_COOKIE= os.environ["SUBSTACK_SID"]
CF_CLEARANCE  = os.environ.get("CF_CLEARANCE", "")
SUBSTACK_LLI  = os.environ.get("SUBSTACK_LLI", "")


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


def build_doc(title, description, link, source, cover_cdn=None):
    brand = "Trip.com" if source == "tripcom" else "Viator"
    article = groq_article(title, description)

    nodes = []

    # Embed the (Substack-hosted) image at the top of the body so it always
    # shows, even if the cover_image thumbnail fails to render.
    if cover_cdn:
        nodes.append({"type": "captionedImage", "content": [{
            "type": "image2",
            "attrs": {"src": cover_cdn, "fullscreen": False,
                      "imageSize": "normal", "type": "image/jpeg",
                      "alignment": "center", "belowTheFold": False}
        }]})

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


def upload_image(session, image_url):
    """Substack won't host an arbitrary external URL for cover_image — it must
    be uploaded to Substack's CDN first. POST the external URL to /api/v1/image
    and return the Substack-hosted URL (or None on failure)."""
    if not image_url:
        return None
    try:
        r = session.post(f"{API_BASE}/image", json={"image": image_url}, timeout=20)
        if r.status_code in (200, 201):
            return r.json().get("url")
        print(f"  image upload failed: {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"  image upload error: {e}")
    return None


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
    # Upload the image to Substack's CDN first; raw external URLs don't stick
    # as cover_image. Reuse the same hosted URL in the body.
    cover_cdn = upload_image(session, image_url)
    doc = build_doc(title, description, link, source, cover_cdn=cover_cdn)
    payload = {
        "draft_title":    title,
        "draft_subtitle": description[:200],
        "draft_body":     doc,
        "audience":       "everyone",
        "section_chosen": False,
        "draft_bylines":  [{"id": user_id, "is_guest": False}] if user_id else [],
        "cover_image":    cover_cdn or image_url or None,
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
    # Prioritize food/experience tours: case-insensitive title search
    cur.execute("""SELECT slug, title, description, link, image_url
                   FROM tours WHERE substack_posted_at IS NULL
                   AND link IS NOT NULL AND link != ''
                   ORDER BY CASE
                     WHEN title ILIKE '%food%' OR title ILIKE '%eat%' OR title ILIKE '%cook%' OR title ILIKE '%wine%' OR title ILIKE '%cheese%' THEN 0
                     WHEN title ILIKE '%photo%' OR title ILIKE '%exclusive%' OR title ILIKE '%vip%' OR title ILIKE '%opera%' OR title ILIKE '%concert%' THEN 1
                     ELSE 2
                   END, id LIMIT 1""")
    row = cur.fetchone()
    if not row:
        cur.execute("UPDATE tours SET substack_posted_at=NULL")
        conn.commit()
        cur.execute("""SELECT slug, title, description, link, image_url
                       FROM tours WHERE link IS NOT NULL
                       ORDER BY CASE
                         WHEN title ILIKE '%food%' OR title ILIKE '%eat%' OR title ILIKE '%cook%' OR title ILIKE '%wine%' OR title ILIKE '%cheese%' THEN 0
                         WHEN title ILIKE '%photo%' OR title ILIKE '%exclusive%' OR title ILIKE '%vip%' OR title ILIKE '%opera%' OR title ILIKE '%concert%' THEN 1
                         ELSE 2
                       END, id LIMIT 1""")
        row = cur.fetchone()
    conn.close()
    if not row:
        return False

    slug, title, desc, link, img = row
    # Force direct-to-tour landing (skip Viator's "similar options" concierge page)
    if link and "target_lander=NONE" not in link:
        link += ("&" if "?" in link else "?") + "target_lander=NONE"
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


# ── Main ──────────────────────────────────────────────────────────────────────

def post_tripcom(session, user_id):
    import csv
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tripcom_london.csv")
    if not os.path.exists(path):
        print("[TRIPCOM] no tripcom_london.csv"); return False
    tours = [r for r in csv.DictReader(open(path, encoding="utf-8"))
             if r.get("url") and r.get("title")]
    if not tours:
        return False
    idx = int(get_setting("tripcom_london_idx", "0")) % len(tours)
    t = tours[idx]
    set_setting("tripcom_london_idx", str(idx + 1))
    title, link, image = t["title"], t["url"], t.get("image_url", "")
    print(f"[TRIPCOM] {title[:60]}")
    result = publish(session, user_id, title, title, link, image, "tripcom")
    if "url" in result:
        print(f"OK: {result['url']}"); return True
    print(f"FAIL: {result.get('error')}"); return False


def main():
    session = get_session()
    user_id = get_user_id(session)
    print(f"User: {user_id}")

    # Alternate Viator <-> Trip.com each run.
    last = get_setting("london_substack_last_source", "viator")
    source = "tripcom" if last == "viator" else "viator"
    print(f"Last: {last} -> Source: {source}")
    ok = post_tripcom(session, user_id) if source == "tripcom" else post_viator(session, user_id)
    if ok:
        set_setting("london_substack_last_source", source)


if __name__ == "__main__":
    main()

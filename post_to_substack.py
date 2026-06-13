"""
Post London tours to Substack - run locally from your machine.
Usage: python post_to_substack.py
"""
import time
import psycopg2
import re
from curl_cffi import requests
from datetime import datetime, timezone

DATABASE_URL = "postgresql://postgres:P32nicky!!??@db.ijmhnhzydouqcifvrpss.supabase.co:5432/postgres"
PUBLICATION = "nickmdavies.substack.com"
API_BASE = f"https://{PUBLICATION}/api/v1"
BATCH = 5  # posts per run

# Cookies from your browser (refresh if they stop working)
SESSION_COOKIE   = "s%3ABSjbrFFTSiy_16g5YOp-B9JVmxJGskW_.nHYInsJrWnSSikp2byM087OlM%2F8DOxxo6Q701GFwnio"
CF_CLEARANCE     = "xTbkSGDPVUkBJBWl3XqQ.OKTXzKyZTiJKYw4qRsPsyw-1778712009-1.2.1.1-KTVOH5HhN_pV02slNqdpiCtx2kyR9EBFAV4VnSXtKruw6BeFHu721uQjMW7..vkckE87MYqqTXgEbjxIghTmFY4TwzrkkvDS3x1TtVjuHQM6MFqfb1HMe2VCA3PhK_G3QYcbKe4ZqgClD5X.mMleQMt9atjaB_ZCr..SGF1xKSVAuRU9GxFvcpWh04nQ5uv6PCuN5kqfk.bddzX.q5VRYooIkGYV3JXm9_pzjXg.gMjnvO.zIgj63ABpBKL8.MSgOrUB2AnHdYpvXPLAtUceO8k0ecHcp6cii7X_g_WltsH2mPNZPM2Gunl.LsyhX8x9P0kCq_RTHaJHD67Tc0RE.Q"
CF_BM            = ""
SUBSTACK_LLI     = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjczNDI5MTAwLCJpYXQiOjE3Nzg3MTIzNjcsImV4cCI6MTc4MTMwNDM2NywiYXVkIjoibGlrZWx5LWxvZ2dlZC1pbiJ9.iqPkwUIhSZFFy0Yj1eLMxWIzPBZMb708jXPZ325Xx7Y"


def get_session():
    from urllib.parse import unquote
    import re as _re
    s = requests.Session(impersonate="chrome120")
    s.cookies.update({
        "substack.sid": unquote(SESSION_COOKIE),
        "cf_clearance": CF_CLEARANCE,
        "__cf_bm": CF_BM,
        "substack.lli": SUBSTACK_LLI,
    })
    s.headers.update({
        "Referer": f"https://{PUBLICATION}/publish/post",
        "Origin": f"https://{PUBLICATION}",
    })
    # Fetch CSRF token
    r = s.get(f"https://{PUBLICATION}/publish/post", timeout=15)
    csrf = _re.search(r'"csrf_token"\s*:\s*"([^"]+)"', r.text)
    if csrf:
        s.headers["X-CSRF-Token"] = csrf.group(1)
        print(f"  CSRF: found")
    else:
        print(f"  CSRF: not found (status={r.status_code})")
        print(f"  Page snippet: {r.text[:200]}")
    return s


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def ensure_column():
    conn = get_conn()
    conn.autocommit = True
    conn.cursor().execute("ALTER TABLE tours ADD COLUMN IF NOT EXISTS substack_posted_at TEXT")
    conn.close()


def get_next_unposted(n):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT slug, title, description, link, keywords, image_url, article_text
        FROM tours WHERE substack_posted_at IS NULL ORDER BY id LIMIT %s
    """, (n,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()
    return rows


def mark_posted(slug):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE tours SET substack_posted_at=%s WHERE slug=%s",
                (datetime.now(timezone.utc).isoformat(), slug))
    conn.commit()
    conn.close()


def html_to_substack(html_str):
    """Convert HTML to Substack's ProseMirror doc format (JSON string)."""
    import json, html as html_mod
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.nodes = []
            self._current = []
            self._marks = []
            self._in_li = False
            self._list_items = []
            self._in_list = False

        def _text_node(self, text):
            if not text: return None
            node = {"type": "text", "text": text}
            if self._marks:
                node["marks"] = list(self._marks)
            return node

        def _flush(self):
            """Flush any accumulated text as a paragraph before resetting."""
            content = [n for n in self._current if n]
            if content:
                self.nodes.append({"type": "paragraph", "content": content})
            self._current = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag in ("h1","h2","h3","h4"):
                self._flush()
            elif tag == "p":
                self._flush()
            elif tag == "ul":
                self._in_list = True
                self._list_items = []
            elif tag == "li":
                self._current = []
                self._in_li = True
            elif tag == "strong" or tag == "b":
                self._marks.append({"type": "bold"})
            elif tag == "a":
                href = attrs.get("href", "")
                self._marks.append({"type": "link", "attrs": {"href": href}})

        def handle_endtag(self, tag):
            if tag in ("h1","h2","h3","h4"):
                level = int(tag[1])
                content = [n for n in self._current if n]
                if content:
                    self.nodes.append({"type": "heading", "attrs": {"level": level}, "content": content})
                self._current = []
            elif tag == "p":
                content = [n for n in self._current if n]
                if content:
                    self.nodes.append({"type": "paragraph", "content": content})
                self._current = []
            elif tag == "li":
                content = [n for n in self._current if n]
                if content:
                    self._list_items.append({"type": "listItem", "content": [{"type": "paragraph", "content": content}]})
                self._current = []
                self._in_li = False
            elif tag == "ul":
                if self._list_items:
                    self.nodes.append({"type": "bulletList", "content": self._list_items})
                self._list_items = []
                self._in_list = False
            elif tag in ("strong","b"):
                self._marks = [m for m in self._marks if m["type"] != "bold"]
            elif tag == "a":
                self._marks = [m for m in self._marks if m["type"] != "link"]

        def handle_data(self, data):
            data = html_mod.unescape(data)
            if data.strip():
                node = self._text_node(data)
                if node:
                    self._current.append(node)

    p = Parser()
    p.feed(html_str)
    p._flush()  # flush any trailing text
    doc = {"type": "doc", "content": p.nodes or [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]}
    return json.dumps(doc)


def upload_image(session, image_url):
    """Substack no longer proxies arbitrary external image URLs — upload to
    Substack's CDN first. Returns the Substack-hosted URL, or None."""
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


def post_tour(tour, session, user_id=None):
    title = tour["title"]
    link = tour["link"]
    description = re.sub(r"<[^>]+>", "", tour.get("description", ""))[:300]
    image_url = tour.get("image_url", "")
    cover_cdn = upload_image(session, image_url)
    article_html = tour.get("article_text", "") or f"<p>{description}</p>"
    article_html += f'<p><a href="{link}">ðŸ‘‰ Book this tour on Viator</a></p>'

    body_doc = html_to_substack(article_html)

    draft_payload = {
        "draft_title": title,
        "draft_subtitle": description,
        "draft_body": body_doc,
        "audience": "everyone",
        "section_chosen": False,
        "draft_bylines": [{"id": user_id, "is_guest": False}] if user_id else [],
        "cover_image": cover_cdn or image_url or None,
    }

    resp = session.post(f"{API_BASE}/drafts", json=draft_payload, timeout=20)
    print(f"  Draft status: {resp.status_code}")
    print(f"  Draft response: {resp.text[:300]}")
    if resp.status_code not in (200, 201):
        return {"error": f"{resp.status_code}: {resp.text[:200]}"}

    draft_id = resp.json().get("id")
    if not draft_id:
        return {"error": "No draft ID"}

    pub = session.post(f"{API_BASE}/drafts/{draft_id}/publish",
                       json={"send_email": False}, timeout=20)
    if pub.status_code in (200, 201):
        url = pub.json().get("url", f"https://{PUBLICATION}/p/{draft_id}")
        return {"url": url}
    return {"error": f"Publish {pub.status_code}: {pub.text[:200]}"}


def main():
    ensure_column()
    session = get_session()

    # Test auth + get user ID
    test = session.get(f"https://{PUBLICATION}/api/v1/publication", timeout=10)
    print(f"Auth check: {test.status_code} â€” {test.text[:150]}")
    pub_data = test.json() if test.status_code == 200 else {}
    user_id = pub_data.get("author_id") or pub_data.get("user_id")
    if not user_id:
        # Try user endpoint
        u = session.get(f"https://{PUBLICATION}/api/v1/user", timeout=10)
        user_id = u.json().get("id") if u.status_code == 200 else None
    print(f"User ID: {user_id}")

    tours = get_next_unposted(BATCH)
    if not tours:
        print("All tours posted â€” restarting from beginning...")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE tours SET substack_posted_at=NULL")
        conn.commit()
        conn.close()
        tours = get_next_unposted(BATCH)
    if not tours:
        print("No tours found!"); return

    print(f"Posting {len(tours)} tours to Substack...")
    posted = 0
    for i, tour in enumerate(tours):
        print(f"\n[{i+1}/{len(tours)}] {tour['title'][:60]}")
        result = post_tour(tour, session, user_id)
        if "url" in result:
            mark_posted(tour["slug"])
            posted += 1
            print(f"  OK: {result['url']}")
        else:
            print(f"  FAIL: {result['error']}")
        time.sleep(3)

    remaining = get_next_unposted(999)
    print(f"\nDone. Posted {posted}/{len(tours)}. {len(remaining)} tours remaining.")
    print("Run again to post next batch.")


if __name__ == "__main__":
    main()


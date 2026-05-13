"""
Post London tours to Substack - run locally from your machine.
Usage: python post_to_substack.py
"""
import time
import psycopg2
import re
from curl_cffi import requests
from datetime import datetime, timezone

DATABASE_URL = "postgresql://neondb_owner:npg_Nq8ZoKMlD1nt@ep-green-sound-angzcs1z-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require"
PUBLICATION = "nickmdavies.substack.com"
API_BASE = f"https://{PUBLICATION}/api/v1"
BATCH = 5  # posts per run

# Cookies from your browser (refresh if they stop working)
SESSION_COOKIE   = "s%3A6U8sY0X79qmz7bF6SqbPQxrUstG_QvvX.S0cXmMQL4NrdAtrGYf%2FtOiOWFWP5FVPL2acb%2FXmQdbo"
CF_CLEARANCE     = "CPxB5oOFK1.VcayNc7_XfVaIncdxjNQepsMHNxvM8Qc-1778635367-1.2.1.1-LGXWMDt3pA21f2Lp.z3fqS56MmsjH7VHUk9Qt2K5tZmZpC.DcuVAkot3wPqSbQbxeb.XZOmbTSo0r3AhEhxgiO91JOjefCov2el88d6LFSEALMKFK84rSLIXdL9oVdEpSF.GAh917Del1QlvFxV_u6BSz94eOJwvpXq94UX9DwfaCxwvNaibdDh7hbheUhqaTBOrR1wj.aSBRvQZxSA5l.eNkGPaKKTRYxyq7fmHgJu.uSIX0tw8cFaXzPGBmVqlfzs_HciMHDyqL3QzQqOLRgQQDICFflbyFsw4RuUcCGNeo_zW7OA7bId1rUrg8fP2dUJsJdubqPiMwjg4UNAQHg"
CF_BM            = "cTyK09kI5TM_rpWdn1X60uxujTh8paKeo47Rke_OsaA-1778635367.5626311-1.0.1.1-gS59nlkLKt8iXG30y628Rk35eMufCKhgS24Jf5monjYUVd_392RsWMoSNPgV851YdfKt6d4gEwurQKQ1KtCbZrlGdGDPxi.RRfkOKwIYgJqgMQlQnSiZvWcw8dpI0nS7"
SUBSTACK_LLI     = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjUwNzQyNjc3MCwiaWF0IjoxNzc4NjM1MzY5LCJleHAiOjE3ODEyMjczNjksImF1ZCI6Imxpa2VseS1sb2dnZWQtaW4ifQ.vYGg0dgrM2AHeLS7K9KHT9dlnSp04g2RFDHXm7Glc_M"


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

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag in ("h1","h2","h3","h4"):
                self._current = []
            elif tag == "p":
                self._current = []
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
    doc = {"type": "doc", "content": p.nodes or [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]}
    return json.dumps(doc)


def post_tour(tour, session, user_id=None):
    title = tour["title"]
    link = tour["link"]
    description = re.sub(r"<[^>]+>", "", tour.get("description", ""))[:300]
    image_url = tour.get("image_url", "")
    article_html = tour.get("article_text", "") or f"<p>{description}</p>"
    article_html += f'<p><a href="{link}">👉 Book this tour on Viator</a></p>'

    body_doc = html_to_substack(article_html)

    draft_payload = {
        "draft_title": title,
        "draft_subtitle": description,
        "draft_body": body_doc,
        "audience": "everyone",
        "section_chosen": False,
        "draft_bylines": [{"id": user_id, "is_guest": False}] if user_id else [],
        "cover_image": image_url or None,
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
    print(f"Auth check: {test.status_code} — {test.text[:150]}")
    pub_data = test.json() if test.status_code == 200 else {}
    user_id = pub_data.get("author_id") or pub_data.get("user_id")
    if not user_id:
        # Try user endpoint
        u = session.get(f"https://{PUBLICATION}/api/v1/user", timeout=10)
        user_id = u.json().get("id") if u.status_code == 200 else None
    print(f"User ID: {user_id}")

    tours = get_next_unposted(BATCH)
    if not tours:
        print("All tours posted — restarting from beginning...")
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

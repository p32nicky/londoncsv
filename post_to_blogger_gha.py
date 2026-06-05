"""Post London tours to Blogger — runs in GitHub Actions (no token file needed)."""
import os, re, time, psycopg2
from datetime import datetime, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BLOG_ID = "5469251663845962632"
BATCH   = 10

def get_creds():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["BLOGGER_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["BLOGGER_CLIENT_ID"],
        client_secret=os.environ["BLOGGER_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/blogger"],
    )
    creds.refresh(Request())
    return creds

def get_conn():
    url = os.environ["DATABASE_URL"]
    at = url.rfind("@"); ui = url[url.index("://")+3:at]; hi = url[at+1:]
    ci = ui.index(":"); user, pw = ui[:ci], ui[ci+1:]
    hp, db = hi.split("?")[0].rsplit("/", 1)
    host, port = hp.rsplit(":", 1) if ":" in hp else (hp, "5432")
    return psycopg2.connect(host=host, port=int(port), dbname=db,
                            user=user, password=pw, sslmode="require")

def get_next_unposted(n):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""SELECT slug, title, description, link, keywords, image_url, article_text
                   FROM tours WHERE blogger_posted_at IS NULL
                   AND link IS NOT NULL AND link != ''
                   ORDER BY id LIMIT %s""", (n,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows

def mark_posted(slug):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE tours SET blogger_posted_at=%s WHERE slug=%s",
                (datetime.now(timezone.utc).isoformat(), slug))
    conn.commit()
    conn.close()

def build_post(tour):
    title = tour["title"]
    link  = tour["link"]
    desc  = re.sub(r"<[^>]+>", "", tour.get("description",""))[:300]
    img   = tour.get("image_url","")
    art   = tour.get("article_text","") or f"<p>{desc}</p>"
    kw    = tour.get("keywords","") or ""

    art = re.sub(r"^\s*<h[12][^>]*>.*?</h[12]>\s*", "", art, count=1,
                 flags=re.IGNORECASE|re.DOTALL)

    content = ""
    if img:
        content += f'<div style="text-align:center;margin-bottom:20px"><img src="{img}" alt="{title}" style="max-width:100%;border-radius:8px"/></div>\n'
    content += art
    content += f'\n<hr/>\n<p><a href="{link}" style="background:#8B1A1A;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:bold">Book this tour on Viator</a></p>'
    content += '\n<p><small>Affiliate link - we may earn a commission at no extra cost to you.</small></p>'

    labels = [k.strip() for k in kw.split(",") if k.strip()][:5] or ["London","Tours"]
    return {"title": title, "content": content, "labels": labels}

def main():
    print("Authenticating with Google...")
    service = build("blogger", "v3", credentials=get_creds())

    tours = get_next_unposted(BATCH)
    if not tours:
        print("All posted - resetting...")
        conn = get_conn()
        conn.cursor().execute("UPDATE tours SET blogger_posted_at=NULL")
        conn.commit()
        conn.close()
        tours = get_next_unposted(BATCH)
    if not tours:
        print("No tours found"); return

    print(f"Posting {len(tours)} tours...")
    posted = 0
    for i, tour in enumerate(tours):
        print(f"[{i+1}/{len(tours)}] {tour['title'][:60]}")
        try:
            body = build_post(tour)
            service.posts().insert(blogId=BLOG_ID, body=body, isDraft=False).execute()
            mark_posted(tour["slug"])
            posted += 1
            print("  OK")
        except Exception as e:
            print(f"  FAIL: {e}")
        time.sleep(5)

    print(f"\nDone. Posted {posted}/{len(tours)}.")

if __name__ == "__main__":
    main()

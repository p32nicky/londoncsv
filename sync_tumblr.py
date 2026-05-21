"""
Sync tours to Tumblr — posts articles with images.
Run via GitHub Actions.
"""
import os, sys, time
import psycopg2

DATABASE_URL = "postgresql://neondb_owner:npg_Nq8ZoKMlD1nt@ep-green-sound-angzcs1z-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require"
BATCH = int(os.environ.get("TUMBLR_BATCH", "5"))

os.environ["DATABASE_URL"] = DATABASE_URL

from app.tumblr_post import post_tour


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def get_next_unposted(n=1):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT slug, title, description, link, keywords, image_url, article_text
        FROM tours
        WHERE tumblr_posted_at IS NULL
        ORDER BY id
        LIMIT %s
    """, (n,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()
    return rows


def mark_posted(slug):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE tours SET tumblr_posted_at=%s WHERE slug=%s", (now, slug))
    conn.commit()
    conn.close()


def main():
    tours = get_next_unposted(BATCH)
    if not tours:
        print("All tours posted — resetting for next cycle...")
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE tours SET tumblr_posted_at=NULL")
        conn.commit()
        conn.close()
        tours = get_next_unposted(BATCH)
    if not tours:
        print("No tours found!"); return

    print(f"Posting {len(tours)} tours to Tumblr...")
    posted = 0
    for i, tour in enumerate(tours):
        print(f"\n[{i+1}/{len(tours)}] {tour['title'][:60]}")
        result = post_tour(tour)
        if "id" in result:
            mark_posted(tour["slug"])
            posted += 1
            print(f"  OK: {result.get('url', '')}")
        else:
            print(f"  FAIL: {result.get('error', '?')[:200]}")
        time.sleep(2)

    print(f"\nDone. Posted {posted}/{len(tours)} to Tumblr.")


if __name__ == "__main__":
    main()

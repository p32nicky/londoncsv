"""Shorten all tour affiliate links with Bitly and update DB."""
import time
import httpx
import psycopg2

DATABASE_URL = "postgresql://neondb_owner:npg_Nq8ZoKMlD1nt@ep-green-sound-angzcs1z-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require"
BITLY_TOKEN = "269c53e1b2eb6dcb2035d4d6ecfac4f2105ce35a"


def shorten(url: str) -> str:
    r = httpx.get(
        "https://tinyurl.com/api-create.php",
        params={"url": url},
        timeout=10,
    )
    if r.status_code == 200 and r.text.startswith("http"):
        return r.text.strip()
    raise RuntimeError(f"TinyURL {r.status_code}: {r.text[:200]}")


conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

# Fetch all tours that don't already have a bit.ly link
cur.execute("SELECT id, slug, link FROM tours WHERE link NOT LIKE '%bit.ly%'")
tours = cur.fetchall()
print(f"{len(tours)} tours to shorten")

ok = 0
for i, (tour_id, slug, link) in enumerate(tours):
    try:
        short = shorten(link)
        cur.execute("UPDATE tours SET link=%s WHERE id=%s", (short, tour_id))
        ok += 1
        print(f"[{i+1}/{len(tours)}] OK {slug[:40]} → {short}")
    except Exception as e:
        print(f"[{i+1}/{len(tours)}] FAIL {slug[:40]}: {e}")
    time.sleep(0.3)  # ~3 req/sec, well within Bitly limits

conn.commit()
conn.close()
print(f"\nDone. {ok}/{len(tours)} links shortened.")

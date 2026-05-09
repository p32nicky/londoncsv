"""Fix Medium import button on all published WordPress posts using correct /tour/{slug}/medium URL."""
import os, sys, re
import httpx
import psycopg2

WP_ACCESS_TOKEN = os.environ.get("WP_ACCESS_TOKEN", "")
DATABASE_URL = "postgresql://neondb_owner:npg_Nq8ZoKMlD1nt@ep-green-sound-angzcs1z-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require"

if not WP_ACCESS_TOKEN:
    print("ERROR: WP_ACCESS_TOKEN not set"); sys.exit(1)

from app.wordpress_post import API_BASE, get_all_posts, update_post, _medium_button

# Build title -> slug map from DB
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()
cur.execute("SELECT slug, title FROM tours")
slug_map = {title.lower(): slug for slug, title in cur.fetchall()}
conn.close()

print("Fetching all published posts...")
posts = get_all_posts(WP_ACCESS_TOKEN)
print(f"{len(posts)} posts found")

BUTTON_PATTERN = r'\n?<p><strong><a href="[^"]*">Import this article to Medium[^<]*</a></strong></p>'

ok = 0
for i, post in enumerate(posts):
    wp_url = post.get("URL", "")
    post_id = post.get("ID", "")
    content = post.get("content", "")
    title = post.get("title", "").lower()

    if not wp_url or not post_id:
        continue

    # Find slug from DB by title match
    slug = slug_map.get(title, "")

    button = _medium_button(slug)

    # Replace any existing button or append
    if re.search(BUTTON_PATTERN, content):
        updated = re.sub(BUTTON_PATTERN, button, content)
    else:
        updated = content + button

    result = update_post(WP_ACCESS_TOKEN, post_id, updated)
    if result.get("ok"):
        ok += 1
        print(f"[{i+1}/{len(posts)}] OK slug={slug or '?'} {wp_url[:50]}")
    else:
        print(f"[{i+1}/{len(posts)}] FAIL {result.get('error','?')[:100]}")

print(f"\nDone. Updated {ok}/{len(posts)} posts.")

"""Add Medium import button to all already-published WordPress posts."""
import os, sys
import httpx

WP_ACCESS_TOKEN = os.environ.get("WP_ACCESS_TOKEN", "")
if not WP_ACCESS_TOKEN:
    print("ERROR: WP_ACCESS_TOKEN not set"); sys.exit(1)

from app.wordpress_post import API_BASE, get_all_posts, update_post, _medium_button

print("Fetching all published posts...")
posts = get_all_posts(WP_ACCESS_TOKEN)
print(f"{len(posts)} posts found")

ok = 0
for i, post in enumerate(posts):
    wp_url = post.get("URL", "")
    post_id = post.get("ID", "")
    content = post.get("content", "")

    if not wp_url or not post_id:
        continue

    # Skip if already has medium button
    if "medium.com/p/import" in content:
        continue

    updated = content + _medium_button(wp_url)
    result = update_post(WP_ACCESS_TOKEN, post_id, updated)
    if result.get("ok"):
        ok += 1
        print(f"[{i+1}/{len(posts)}] OK {wp_url[:60]}")
    else:
        print(f"[{i+1}/{len(posts)}] FAIL {result.get('error','?')[:100]}")

print(f"\nDone. Updated {ok}/{len(posts)} posts.")

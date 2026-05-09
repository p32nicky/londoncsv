"""Debug WP update — test on one post and print full API response."""
import os, sys, httpx

WP_TOKEN = os.environ.get("WP_ACCESS_TOKEN", "")
if not WP_TOKEN:
    print("ERROR: WP_ACCESS_TOKEN not set"); sys.exit(1)

SITE = "londontours74.wordpress.com"
API_BASE = f"https://public-api.wordpress.com/rest/v1.1/sites/{SITE}"

# Get one post
resp = httpx.get(f"{API_BASE}/posts?number=1&fields=ID,URL,content",
    headers={"Authorization": f"Bearer {WP_TOKEN}"}, timeout=15)
post = resp.json()["posts"][0]
post_id = post["ID"]
wp_url = post["URL"]
content = post["content"]
print(f"Post {post_id}: {wp_url}")
print(f"Current content ends with: ...{content[-100:]}")
print()

# Try update
button = f'\n<p><strong><a href="https://medium.com/p/import?url={wp_url}">Import this article to Medium</a></strong></p>'
new_content = content + button

update_resp = httpx.post(
    f"{API_BASE}/posts/{post_id}",
    headers={"Authorization": f"Bearer {WP_TOKEN}", "Content-Type": "application/json"},
    json={"content": new_content},
    timeout=20,
)
print(f"Update status: {update_resp.status_code}")
print(f"Response: {update_resp.text[:500]}")

# Verify
verify = httpx.get(f"{API_BASE}/posts/{post_id}?fields=content",
    headers={"Authorization": f"Bearer {WP_TOKEN}"}, timeout=15)
updated_content = verify.json().get("content", "")
print(f"\nHas button after update: {'medium.com/p/import' in updated_content}")
print(f"Ends with: ...{updated_content[-200:]}")

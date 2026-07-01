"""
Daily Tumblr poster for the Churchill War Rooms Tours site.

Posts ONE photo post per day to explore-londontours.tumblr.com — the hero image,
a rotating caption, tags, and a clickable link to churchillwarroomstours.com.
Dedup keyed on the date so a re-run the same day never double-posts.

Run once a day from GitHub Actions.
"""
import os, sys, io, json, time, requests
from datetime import datetime, timezone
from requests_oauthlib import OAuth1

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

BLOG = "explore-londontours.tumblr.com"
API_URL = f"https://api.tumblr.com/v2/blog/{BLOG}/post"

CONSUMER_KEY    = os.environ.get("TUMBLR_CONSUMER_KEY")    or "TxHYOvd4AVFPBTiKy3AAAbpr9ztCJdFLa8fzTvSiJ9TV3vR1zx"
CONSUMER_SECRET = os.environ.get("TUMBLR_CONSUMER_SECRET") or "p9rAvpJVp9CakR1XwN08jtXs797HHJumYO4MSRKdCqV14Kuh2x"
TOKEN           = os.environ.get("TUMBLR_TOKEN")           or "M18FBknEUfj5MtSzARSTy5FEoJLUReb2UxkccqyUqJ9CyJUkkj"
TOKEN_SECRET    = os.environ.get("TUMBLR_TOKEN_SECRET")    or "XVscoOV1zTOx4xrJfL88ZzBksJPOrt06prA46ZhNi7yo8kMrvW"

SITE = "https://churchillwarroomstours.com/?cm=tumblr"
# Headout CDN hero image — reachable now, independent of the site's DNS.
IMAGE = ("https://cdn-imgix.headout.com/media/images/"
         "47cb3542205faa13760fc7b256ac89c7-23804-london-westminster-walking-tour"
         "---churchill-s-war-rooms-entrance-01.jpg?w=1200&fm=jpg&q=75")

# Rotating captions — one chosen by day-of-year so consecutive days differ.
CAPTIONS = [
    "The <b>Churchill War Rooms</b> — the secret underground bunker beneath Whitehall where Churchill ran the Second World War, sealed and left exactly as it was in 1945.",
    "Step inside the bunker that ran WW2. The <b>Churchill War Rooms</b> in London: the Map Room, the Cabinet Room, Churchill's bedroom and the Churchill Museum.",
    "One of London's most powerful history days out — the <b>Churchill War Rooms</b>, the underground nerve-centre of the war, frozen in time since 1945.",
    "Underground London: the <b>Churchill War Rooms</b>. The pins are still in the maps, the phones still on the desks — a WWII bunker preserved exactly as it was.",
    "Where Churchill and his War Cabinet directed the fight against Hitler — the <b>Churchill War Rooms</b>, three streets under Westminster. A must-see in London.",
    "The <b>Churchill War Rooms</b>: a secret WWII bunker, an award-winning Churchill Museum, and one of the best history experiences in London.",
    "Visiting London? Don't miss the <b>Churchill War Rooms</b> — the wartime bunker where Britain's darkest hours were fought from underground.",
    "Inside Churchill's secret war bunker — the <b>Churchill War Rooms</b> in Westminster. Full visitor guide, opening hours and tickets.",
]

TAGS = ("london,londontours,churchill war rooms,churchill,ww2,wwii,history,"
        "visitlondon,thingstodoinlondon,travel,uk,imperial war museum,museum")

HERE = os.path.dirname(os.path.abspath(__file__))
POSTED_LOG = os.path.join(HERE, "tumblr_churchill_posted.json")


def load_posted():
    if not os.path.exists(POSTED_LOG):
        return {}
    try:
        return json.load(open(POSTED_LOG, encoding="utf-8-sig"))
    except Exception:
        return {}


def save_posted(d):
    with open(POSTED_LOG, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def post(caption):
    auth = OAuth1(CONSUMER_KEY, CONSUMER_SECRET, TOKEN, TOKEN_SECRET)
    body = caption + f'<br/><br/><a href="{SITE}">Visitor guide &amp; tickets →</a>'
    data = {"type": "photo", "caption": body[:2000], "tags": TAGS,
            "link": SITE, "source": IMAGE}
    # Retry transient 429s (the London poster shares this Tumblr app).
    for attempt in range(3):
        r = requests.post(API_URL, data=data, auth=auth, timeout=20)
        if r.status_code in (200, 201):
            pid = r.json().get("response", {}).get("id", "")
            return {"id": pid, "url": f"https://{BLOG}/post/{pid}"}
        if r.status_code == 429 and attempt < 2:
            wait = 25 * (attempt + 1)
            print(f"  429 rate limit — waiting {wait}s (attempt {attempt+1}/3)")
            time.sleep(wait)
            continue
        if r.status_code == 429:
            return {"ratelimited": True}
        return {"error": r.text[:300]}
    return {"ratelimited": True}


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    posted = load_posted()
    if today in posted:
        print(f"[skip] already posted today ({today})")
        return
    caption = CAPTIONS[datetime.now(timezone.utc).timetuple().tm_yday % len(CAPTIONS)]
    res = post(caption)
    if "id" in res:
        posted[today] = {"url": res["url"], "at": datetime.now(timezone.utc).isoformat()}
        save_posted(posted)
        print(f"[OK] {res['url']}")
    elif res.get("ratelimited"):
        # Tumblr app throttle (London poster shares it). Not our fault — skip
        # gracefully so no failure email; tomorrow's run tries again.
        print("[skip] Tumblr rate-limited this run — will try again next day")
    else:
        print(f"[ERR] {res.get('error', '?')}")
        sys.exit(1)


if __name__ == "__main__":
    main()

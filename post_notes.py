"""Post one AI-written Substack Note on each of the 3 accounts (London, Paris, Rome).
Runs 3x daily via GitHub Actions. Cookies + Groq key come from env vars."""
import os, sys, time, random
from urllib.parse import unquote
import httpx
from curl_cffi import requests

sys.stdout.reconfigure(encoding="utf-8")

GROQ_KEY = os.environ["GROQ_KEY"]

ACCOUNTS = [
    {
        "name": "London",
        "cookie": os.environ["SUBSTACK_SID"],
        "city": "London",
        "topics": [
            "a hidden or underrated London experience most tourists miss",
            "a classic London attraction and the insider trick to doing it better",
            "London food scene - markets, pubs, food tours",
            "day trips from London (Windsor, Bath, Stonehenge, Oxford)",
            "London at night - ghost walks, theatre, rooftop bars",
            "free or cheap things to do in London",
            "London in the rain - what to do when weather turns",
            "Harry Potter and film locations around London",
        ],
    },
    {
        "name": "Paris",
        "cookie": os.environ["PARIS_SID"],
        "city": "Paris",
        "topics": [
            "a hidden Paris gem most tourists walk straight past",
            "the Eiffel Tower or Louvre and how to skip the crowds",
            "Paris food - cheese, wine, bakeries, market streets",
            "day trips from Paris (Versailles, Giverny, Champagne)",
            "Montmartre and the artist quarter",
            "Paris on a budget",
            "Seine river experiences",
            "Paris neighborhoods locals actually hang out in",
        ],
    },
    {
        "name": "Rome",
        "cookie": os.environ["ROME_SID"],
        "city": "Rome",
        "topics": [
            "the Colosseum underground and gladiator's gate",
            "Vatican tips - early entry, dress codes, crowds",
            "Roman food - trattorias in Trastevere, carbonara debates",
            "day trips from Rome (Pompeii, Amalfi, Tivoli)",
            "hidden Rome - lesser-known ruins and churches",
            "Rome in summer heat - how to survive it",
            "the best aperitivo spots and evening passeggiata",
            "throwing coins in fountains and other Rome rituals",
        ],
    },
]


def write_note(city, topic):
    prompt = f"""Write a short Substack Note (a casual social post, 60-120 words) by a travel writer who runs a {city} tours newsletter.

Topic: {topic}

Rules:
- Sound completely human and casual - like texting a friend who asked for travel tips
- First person, conversational, a little opinionated
- One small specific detail or personal observation that makes it feel real
- End with a genuine question to readers OR a soft nudge to check the newsletter archive for write-ups (vary it)
- NO hashtags, NO emojis, NO links, NO marketing language, NO "hey everyone"
- 2-3 short paragraphs separated by blank lines
- Output the note text only"""
    for attempt in range(4):
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            json={"model": "llama-3.3-70b-versatile", "max_tokens": 400,
                  "temperature": 1.0,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60)
        if r.status_code == 429:
            time.sleep(20)
            continue
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip().strip('"')
    return None


def post_note(cookie, text):
    s = requests.Session(impersonate="chrome120")
    s.cookies.update({"substack.sid": unquote(cookie)})
    s.headers.update({"Referer": "https://substack.com/notes",
                      "Origin": "https://substack.com"})
    paragraphs = [{"type": "paragraph", "content": [{"type": "text", "text": p}]}
                  for p in text.split("\n\n") if p.strip()]
    body = {
        "bodyJson": {"type": "doc", "attrs": {"schemaVersion": "v1"},
                     "content": paragraphs},
        "tabId": "for-you",
        "surface": "feed",
        "replyMinimumRole": "everyone",
    }
    r = s.post("https://substack.com/api/v1/comment/feed", json=body, timeout=20)
    return r.status_code in (200, 201), f"{r.status_code}: {r.text[:150]}"


for acc in ACCOUNTS:
    topic = random.choice(acc["topics"])
    note = write_note(acc["city"], topic)
    if not note:
        print(f"[{acc['name']}] generation failed")
        continue
    ok, detail = post_note(acc["cookie"], note)
    print(f"[{acc['name']}] topic: {topic}")
    print(f"[{acc['name']}] {'OK' if ok else 'FAIL ' + detail}")
    print(f"  > {note[:100]}...")
    time.sleep(5)

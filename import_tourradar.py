"""
Import TourRadar CSV into the London tours database.
Usage: .venv/Scripts/python import_tourradar.py [path/to/csv]
"""
import csv
import os
import re
import sys
from datetime import datetime, timezone

# Load .env
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from app.config import get_settings
from app.db import init_db, upsert_tours


def slugify(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:80]


def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else \
        r"C:\Users\nickd\pintrestbot\tourradar_england_ireland.csv"

    if not os.path.exists(csv_path):
        print(f"CSV not found: {csv_path}")
        sys.exit(1)

    settings = get_settings()
    print("Initialising DB...")
    init_db(settings.db_path)

    items = []
    now = datetime.now(timezone.utc).isoformat()

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get("name", "").strip()
            if not title:
                continue
            slug = slugify(title)
            operator = row.get("operator", "").strip()
            price = row.get("price", "").strip()
            duration = row.get("duration", "").strip()
            destinations = row.get("destinations", "").strip()
            rating = row.get("rating", "").strip()
            reviews = row.get("reviews", "").strip()
            saving = row.get("saving", "").strip()

            parts = [f"{title} — {operator}" if operator else title]
            if duration: parts.append(f"{duration}")
            if price: parts.append(f"From {price}")
            if saving: parts.append(saving)
            if destinations: parts.append(f"Destinations: {destinations}")
            if rating and reviews: parts.append(f"Rated {rating} ({reviews})")
            description = ". ".join(parts) + "."

            keywords = f"london, england, ireland, tour, {operator.lower()}, {duration}"

            items.append({
                "title": title,
                "slug": slug,
                "image_url": "",
                "description": description,
                "link": row.get("affiliate_url", "").strip(),
                "keywords": keywords,
                "publish_date": now,
                "first_seen_at": now,
            })

    print(f"Parsed {len(items)} tours from CSV")
    inserted = upsert_tours(settings.db_path, items)
    print(f"Inserted {inserted} new tours. Done.")


if __name__ == "__main__":
    main()

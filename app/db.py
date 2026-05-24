import os
import sqlite3
from contextlib import contextmanager
from typing import Optional

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = bool(_DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras


def _parse_pg_url(url):
    """Parse postgresql URL safely — handles passwords with special chars like ? and !"""
    import logging
    logger = logging.getLogger(__name__)
    url = url.strip()
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            url = url[len(scheme):]
            break
    # Split userinfo from hostinfo at LAST @ (password might contain @)
    at_idx = url.rfind("@")
    if at_idx == -1:
        logger.error("No @ found in DATABASE_URL")
        return {}
    userinfo = url[:at_idx]
    hostinfo = url[at_idx + 1:]
    # user:password — first colon separates them
    colon_idx = userinfo.find(":")
    if colon_idx == -1:
        user, password = userinfo, ""
    else:
        user = userinfo[:colon_idx]
        password = userinfo[colon_idx + 1:]
    # Strip pgbouncer/channel_binding from hostinfo query params
    if "?" in hostinfo:
        hostpart, params = hostinfo.split("?", 1)
        params = "&".join(
            p for p in params.split("&")
            if not p.startswith("pgbouncer") and not p.startswith("channel_binding")
        )
    else:
        hostpart, params = hostinfo, ""
    # host:port/dbname
    if "/" in hostpart:
        hostport, dbname = hostpart.rsplit("/", 1)
    else:
        hostport, dbname = hostpart, "postgres"
    if ":" in hostport:
        host, port_str = hostport.rsplit(":", 1)
        port = int(port_str)
    else:
        host, port = hostport, 5432
    kwargs = dict(host=host, port=port, dbname=dbname, user=user, password=password,
                  sslmode="require", connect_timeout=10)
    logger.info(f"DB connecting to: {host}:{port}/{dbname} as {user}")
    return kwargs


@contextmanager
def _pg_conn():
    import logging
    logger = logging.getLogger(__name__)
    kwargs = _parse_pg_url(_DATABASE_URL)
    try:
        conn = psycopg2.connect(cursor_factory=psycopg2.extras.RealDictCursor, **kwargs)
    except Exception as e:
        logger.error(f"DB connection FAILED: {e}")
        raise
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _sqlite_conn(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _get_conn(db_path: str):
    return _pg_conn() if USE_POSTGRES else _sqlite_conn(db_path)


_CREATE_SQLITE = """
CREATE TABLE IF NOT EXISTS tours (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    image_url TEXT,
    description TEXT,
    link TEXT NOT NULL,
    keywords TEXT,
    publish_date TEXT,
    first_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_slug ON tours(slug);
"""

_CREATE_PG = """
CREATE TABLE IF NOT EXISTS tours (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    image_url TEXT,
    description TEXT,
    link TEXT NOT NULL,
    keywords TEXT,
    publish_date TEXT,
    first_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_slug ON tours(slug);
"""


def init_db(db_path: str) -> None:
    if not USE_POSTGRES:
        try:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        except OSError:
            pass
    with _get_conn(db_path) as conn:
        sql = _CREATE_PG if USE_POSTGRES else _CREATE_SQLITE
        for stmt in sql.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    if USE_POSTGRES:
                        conn.cursor().execute(stmt)
                    else:
                        conn.execute(stmt)
                except Exception:
                    pass


def upsert_tours(db_path: str, items: list[dict]) -> int:
    inserted = 0
    ph = "%s" if USE_POSTGRES else "?"
    with _get_conn(db_path) as conn:
        for item in items:
            if USE_POSTGRES:
                cur = conn.cursor()
                cur.execute(f"""
                    INSERT INTO tours (title, slug, image_url, description, link, keywords, publish_date, first_seen_at)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})
                    ON CONFLICT (slug) DO UPDATE SET
                        image_url = EXCLUDED.image_url,
                        description = EXCLUDED.description
                    RETURNING (xmax = 0) AS is_new
                """, (item["title"], item["slug"], item["image_url"], item["description"],
                      item["link"], item["keywords"], item["publish_date"], item["first_seen_at"]))
                row = cur.fetchone()
                if row and row["is_new"]:
                    inserted += 1
            else:
                ex = conn.execute("SELECT id FROM tours WHERE slug=?", (item["slug"],)).fetchone()
                if not ex:
                    conn.execute("""
                        INSERT INTO tours (title, slug, image_url, description, link, keywords, publish_date, first_seen_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (item["title"], item["slug"], item["image_url"], item["description"],
                          item["link"], item["keywords"], item["publish_date"], item["first_seen_at"]))
                    inserted += 1
    return inserted


def _rows(conn, sql, params=()):
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()
    return conn.execute(sql, params).fetchall()


def _one(conn, sql, params=()):
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()
    return conn.execute(sql, params).fetchone()


def list_tours(db_path: str, query: str = "", page: int = 1, per_page: int = 24):
    ph = "%s" if USE_POSTGRES else "?"
    like_op = "ILIKE" if USE_POSTGRES else "LIKE"
    offset = (page - 1) * per_page
    with _get_conn(db_path) as conn:
        if query:
            like = f"%{query}%"
            rows = _rows(conn,
                f"SELECT * FROM tours WHERE title {like_op} {ph} ORDER BY id DESC LIMIT {ph} OFFSET {ph}",
                (like, per_page, offset))
            total = (_one(conn, f"SELECT COUNT(*) as count FROM tours WHERE title {like_op} {ph}", (like,)) or {}).get("count", 0) \
                if USE_POSTGRES else (_one(conn, f"SELECT COUNT(*) FROM tours WHERE title {like_op} {ph}", (like,)) or [0])[0]
        else:
            rows = _rows(conn,
                f"SELECT * FROM tours ORDER BY id DESC LIMIT {ph} OFFSET {ph}",
                (per_page, offset))
            total = (_one(conn, "SELECT COUNT(*) as count FROM tours") or {}).get("count", 0) \
                if USE_POSTGRES else (_one(conn, "SELECT COUNT(*) FROM tours") or [0])[0]
    return rows, total


def get_latest_tours(db_path: str, limit: int = 10, offset: int = 0):
    ph = "%s" if USE_POSTGRES else "?"
    with _get_conn(db_path) as conn:
        total = (_one(conn, "SELECT COUNT(*) as count FROM tours") or {}).get("count", 0) \
            if USE_POSTGRES else (_one(conn, "SELECT COUNT(*) FROM tours") or [0])[0]
        safe_offset = offset % total if total else 0
        return _rows(conn, f"SELECT * FROM tours ORDER BY id LIMIT {ph} OFFSET {ph}", (limit, safe_offset))


def save_article(db_path: str, slug: str, html: str) -> None:
    ph = "%s" if USE_POSTGRES else "?"
    with _get_conn(db_path) as conn:
        if USE_POSTGRES:
            conn.cursor().execute(
                f"ALTER TABLE tours ADD COLUMN IF NOT EXISTS article_text TEXT"
            )
            conn.cursor().execute(
                f"UPDATE tours SET article_text={ph} WHERE slug={ph}", (html, slug)
            )
        else:
            try:
                conn.execute("ALTER TABLE tours ADD COLUMN article_text TEXT")
            except Exception:
                pass
            conn.execute("UPDATE tours SET article_text=? WHERE slug=?", (html, slug))


def get_next_unpublished(db_path: str):
    """Get next tour that has no article or hasn't been posted to Tumblr."""
    with _get_conn(db_path) as conn:
        # Ensure column exists
        if USE_POSTGRES:
            conn.cursor().execute("ALTER TABLE tours ADD COLUMN IF NOT EXISTS tumblr_posted_at TEXT")
        else:
            try:
                conn.execute("ALTER TABLE tours ADD COLUMN tumblr_posted_at TEXT")
            except Exception:
                pass
        return _one(conn,
            "SELECT * FROM tours WHERE article_text IS NOT NULL AND article_text != '' AND tumblr_posted_at IS NULL ORDER BY id LIMIT 1"
        )


def mark_tumblr_posted(db_path: str, slug: str) -> None:
    ph = "%s" if USE_POSTGRES else "?"
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn(db_path) as conn:
        if USE_POSTGRES:
            conn.cursor().execute("ALTER TABLE tours ADD COLUMN IF NOT EXISTS tumblr_posted_at TEXT")
            conn.cursor().execute(f"UPDATE tours SET tumblr_posted_at={ph} WHERE slug={ph}", (now, slug))
        else:
            try:
                conn.execute("ALTER TABLE tours ADD COLUMN tumblr_posted_at TEXT")
            except Exception:
                pass
            conn.execute("UPDATE tours SET tumblr_posted_at=? WHERE slug=?", (now, slug))


def get_setting(db_path: str, key: str) -> Optional[str]:
    ph = "%s" if USE_POSTGRES else "?"
    with _get_conn(db_path) as conn:
        if USE_POSTGRES:
            conn.cursor().execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        else:
            conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        row = _one(conn, f"SELECT value FROM settings WHERE key={ph}", (key,))
        return row["value"] if row else None


def save_setting(db_path: str, key: str, value: str) -> None:
    ph = "%s" if USE_POSTGRES else "?"
    with _get_conn(db_path) as conn:
        if USE_POSTGRES:
            conn.cursor().execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.cursor().execute(
                f"INSERT INTO settings (key,value) VALUES ({ph},{ph}) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (key, value)
            )
        else:
            conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))


def get_tour_by_slug(db_path: str, slug: str) -> Optional[dict]:
    ph = "%s" if USE_POSTGRES else "?"
    with _get_conn(db_path) as conn:
        return _one(conn, f"SELECT * FROM tours WHERE slug={ph}", (slug,))

"""Pull every configured RSS/Atom source into the articles table."""
import calendar
import html
import logging
import re
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from . import db

log = logging.getLogger(__name__)

USER_AGENT = "MorningBrief/1.0 (+personal RSS aggregator)"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(text: str) -> str:
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", text or ""))).strip()


def _published(entry) -> datetime | None:
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc) if stamp else None


def fetch_all(conn, cfg: dict) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg["memory"]["max_article_age_hours"])
    added = 0
    for feed in cfg["feeds"]:
        try:
            resp = requests.get(feed["url"], headers={"User-Agent": USER_AGENT}, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Could not fetch %s: %s", feed["name"], exc)
            continue
        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            log.warning("Could not parse %s: %s", feed["name"], parsed.get("bozo_exception"))
            continue
        new = 0
        for entry in parsed.entries:
            link, title = entry.get("link"), clean(entry.get("title", ""))
            if not link or not title:
                continue
            published = _published(entry)
            if published and published < cutoff:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO articles (url, title, summary, source, weight, published, fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    link,
                    title,
                    clean(entry.get("summary", ""))[:1500],
                    feed["name"],
                    feed.get("weight", 1.0),
                    (published or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
                    db.now(),
                ),
            )
            new += cur.rowcount
        log.info("%-30s %3d new / %3d in feed", feed["name"], new, len(parsed.entries))
        added += new
    conn.commit()
    return added

"""Writes the two subscribable feeds into public/: combined.xml (sources) and podcast.xml (episodes)."""
import logging
from datetime import datetime, timezone

from feedgen.feed import FeedGenerator

log = logging.getLogger(__name__)


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def write_combined_feed(conn, cfg: dict, limit: int = 300) -> None:
    show, public = cfg["show"], cfg["paths"]["public_dir"]
    fg = FeedGenerator()
    fg.title(f"{show['title']} — Sources")
    fg.link(href=f"{show['base_url']}/combined.xml", rel="self")
    fg.description("All articles from my chosen news sources, merged into one feed.")
    fg.language("en")

    rows = conn.execute("SELECT * FROM articles ORDER BY published DESC LIMIT ?", (limit,)).fetchall()
    for row in rows:
        fe = fg.add_entry(order="append")
        fe.id(row["url"])
        fe.guid(row["url"], permalink=True)
        fe.title(f"{row['title']} ({row['source']})")
        fe.link(href=row["url"])
        fe.description(row["summary"] or row["title"])
        fe.pubDate(_dt(row["published"]))
    public.mkdir(parents=True, exist_ok=True)
    fg.rss_file(str(public / "combined.xml"), pretty=True)
    log.info("Wrote combined.xml (%d items)", len(rows))


def write_podcast_feed(conn, cfg: dict) -> None:
    show, public = cfg["show"], cfg["paths"]["public_dir"]
    base = show["base_url"].rstrip("/")
    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.title(show["title"])
    fg.link(href=f"{base}/podcast.xml", rel="self")
    fg.link(href=base, rel="alternate")
    fg.description(show["description"])
    fg.author(name=show["author"])
    fg.language("en")
    fg.podcast.itunes_author(show["author"])
    fg.podcast.itunes_category("News", "Daily News")
    fg.podcast.itunes_explicit("no")
    fg.podcast.itunes_block(True)  # keep it out of public directories
    if (public / "cover.jpg").exists():
        fg.image(url=f"{base}/cover.jpg", title=show["title"], link=base)
        fg.podcast.itunes_image(f"{base}/cover.jpg")

    rows = conn.execute("SELECT * FROM episodes ORDER BY date DESC").fetchall()
    for row in rows:
        url = f"{base}/episodes/{row['filename']}"
        fe = fg.add_entry(order="append")
        fe.id(url)
        fe.guid(url, permalink=False)
        fe.title(row["title"])
        fe.description(row["show_notes"] or row["title"])
        fe.enclosure(url, str(row["bytes"]), "audio/mpeg")
        fe.pubDate(_dt(row["created_at"]))
        fe.podcast.itunes_duration(row["duration"])
    public.mkdir(parents=True, exist_ok=True)
    fg.rss_file(str(public / "podcast.xml"), pretty=True)
    log.info("Wrote podcast.xml (%d episodes)", len(rows))


def prune_episodes(conn, cfg: dict) -> None:
    keep = cfg["paths"]["keep_episodes"]
    old = conn.execute("SELECT id, filename FROM episodes ORDER BY date DESC LIMIT -1 OFFSET ?", (keep,)).fetchall()
    for row in old:
        (cfg["paths"]["public_dir"] / "episodes" / row["filename"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM episodes WHERE id = ?", (row["id"],))
    conn.commit()

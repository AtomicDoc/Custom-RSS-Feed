"""Decides what goes into today's episode, and what counts as a real follow-up."""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .llm import Ollama

log = logging.getLogger(__name__)

EDITOR_SYSTEM = (
    "You are the editor of a personal morning news podcast. Given candidate stories and the listener's "
    "interests, choose the stories worth covering, most important first. Prefer significant, "
    "widely-reported news and the listener's interests; drop trivia, ads, listicles and duplicates. "
    'Reply with JSON: {"story_ids": [ids in running order]}'
)

FOLLOWUP_SYSTEM = (
    "You compare what a listener has already been told about a news story with new reporting. "
    "Decide whether the new reporting contains genuinely new developments (new facts, decisions, "
    "numbers, reactions or outcomes), not just a rehash. "
    'Reply with JSON: {"has_new_developments": true|false, "what_changed": "one or two sentences"}'
)


@dataclass
class Item:
    story: dict
    articles: list
    kind: str                      # "new" | "followup"
    what_changed: str = ""
    sources: list = field(default_factory=list)


def _candidates(conn, cfg) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=cfg["memory"]["max_article_age_hours"])).isoformat()
    rows = conn.execute(
        """
        SELECT s.*, COUNT(DISTINCT a.source) AS n_sources, SUM(a.weight) AS score,
               GROUP_CONCAT(DISTINCT a.source) AS sources
        FROM stories s JOIN articles a ON a.story_id = s.id
        WHERE a.aired = 0 AND a.fetched_at >= ?
        GROUP BY s.id
        ORDER BY score DESC, n_sources DESC
        LIMIT 60
        """,
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def _unaired_articles(conn, story_id: int) -> list:
    return conn.execute(
        "SELECT * FROM articles WHERE story_id = ? AND aired = 0 ORDER BY published", (story_id,)
    ).fetchall()


def plan_episode(conn, llm: Ollama, cfg: dict) -> list[Item]:
    show = cfg["show"]
    candidates = _candidates(conn, cfg)
    if not candidates:
        return []

    # Source names give the editor context a bare headline lacks (e.g. a cryptic Steam patch-note title).
    listing = "\n".join(
        f"[{c['id']}] {c['headline']} (from: {c['sources'].replace(',', ', ')}"
        f"{'; previously covered' if c['times_aired'] else ''})"
        for c in candidates
    )
    picked = llm.chat_json(EDITOR_SYSTEM, f"LISTENER INTERESTS: {show['interests']}\n\nCANDIDATES:\n{listing}")
    by_id = {c["id"]: c for c in candidates}
    order = [int(i) for i in picked.get("story_ids", []) if str(i).isdigit() and int(i) in by_id]
    # Fall back to weighted-score ranking if the model returned nothing usable.
    order = list(dict.fromkeys(order)) or [c["id"] for c in candidates]

    items: list[Item] = []
    for story_id in order:
        if len(items) >= show["max_stories"]:
            break
        story, articles = by_id[story_id], _unaired_articles(conn, story_id)
        if not story["times_aired"]:
            items.append(Item(story, articles, "new"))
            continue
        reporting = "\n".join(f"- {a['title']}: {(a['summary'] or '')[:400]}" for a in articles)
        verdict = llm.chat_json(
            FOLLOWUP_SYSTEM,
            f"ALREADY TOLD TO LISTENER:\n{story['summary']}\n\nNEW REPORTING:\n{reporting}",
        )
        if verdict.get("has_new_developments"):
            items.append(Item(story, articles, "followup", verdict.get("what_changed", "")))
        else:
            # Nothing new: retire these articles so the story doesn't resurface tomorrow as a repeat.
            conn.execute("UPDATE articles SET aired = 1 WHERE story_id = ? AND aired = 0", (story_id,))
            log.info("Skipped rehash of story %d: %s", story_id, story["headline"])
    conn.commit()
    log.info("Planned %d stories (%d follow-ups)", len(items), sum(i.kind == "followup" for i in items))
    return items

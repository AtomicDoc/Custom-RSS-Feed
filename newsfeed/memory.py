"""Story memory: groups articles about the same event into stories, across days.

Each article is embedded and compared to recent stories. Close matches join the
existing story; borderline matches are settled by the LLM; everything else
starts a new story. Because stories persist for `story_window_days`, tomorrow's
coverage of today's event lands on the same story and becomes a follow-up
instead of a repeat.
"""
import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from . import db
from .llm import Ollama

log = logging.getLogger(__name__)

SAME_STORY_SYSTEM = (
    "You decide whether a news article covers the same underlying news story as an existing story "
    "(same event or a direct development of it), not merely the same topic. "
    'Reply with JSON: {"same_story": true|false}'
)


class _Index:
    """In-memory matrix of recent story embeddings, kept in sync as stories change."""

    def __init__(self, rows):
        self.ids = [r["id"] for r in rows]
        self.counts = [r["n_articles"] for r in rows]
        self.info = {r["id"]: (r["headline"], r["summary"] or "") for r in rows}
        self.vecs = [db.from_blob(r["embedding"]).copy() for r in rows]

    def best(self, vec):
        if not self.vecs:
            return None, 0.0
        sims = np.stack(self.vecs) @ vec
        i = int(np.argmax(sims))
        return i, float(sims[i])

    def add(self, story_id, vec, headline):
        self.ids.append(story_id)
        self.counts.append(1)
        self.vecs.append(vec)
        self.info[story_id] = (headline, "")

    def merge(self, i, vec):
        n = self.counts[i]
        mean = (self.vecs[i] * n + vec) / (n + 1)
        self.vecs[i] = mean / (np.linalg.norm(mean) or 1.0)
        self.counts[i] = n + 1
        return self.vecs[i]


def _is_same_story(llm: Ollama, headline: str, summary: str, article) -> bool:
    prompt = (
        f"EXISTING STORY: {headline}\n{summary}\n\n"
        f"NEW ARTICLE: {article['title']}\n{article['summary'] or ''}"
    )
    return bool(llm.chat_json(SAME_STORY_SYSTEM, prompt).get("same_story"))


def cluster_new_articles(conn, llm: Ollama, cfg: dict) -> None:
    mem = cfg["memory"]
    articles = conn.execute("SELECT * FROM articles WHERE story_id IS NULL ORDER BY published").fetchall()
    if not articles:
        return

    since = (datetime.now(timezone.utc) - timedelta(days=mem["story_window_days"])).isoformat()
    index = _Index(conn.execute(
        "SELECT id, headline, summary, embedding, n_articles FROM stories"
        " WHERE last_updated >= ? AND embedding IS NOT NULL", (since,)
    ).fetchall())

    created = joined = 0
    for start in range(0, len(articles), 32):
        batch = articles[start:start + 32]
        vecs = llm.embed([f"{a['title']}. {(a['summary'] or '')[:600]}" for a in batch])
        for article, vec in zip(batch, vecs):
            conn.execute("UPDATE articles SET embedding = ? WHERE id = ?", (db.to_blob(vec), article["id"]))
            i, sim = index.best(vec)
            match = i is not None and (
                sim >= mem["same_story_auto"]
                or (sim >= mem["same_story_maybe"] and _is_same_story(llm, *index.info[index.ids[i]], article))
            )
            if match:
                story_id = index.ids[i]
                merged = index.merge(i, vec)
                conn.execute(
                    "UPDATE stories SET embedding = ?, n_articles = n_articles + 1, last_updated = ? WHERE id = ?",
                    (db.to_blob(merged), db.now(), story_id),
                )
                joined += 1
            else:
                cur = conn.execute(
                    "INSERT INTO stories (headline, embedding, n_articles, first_seen, last_updated)"
                    " VALUES (?, ?, 1, ?, ?)",
                    (article["title"], db.to_blob(vec), db.now(), db.now()),
                )
                story_id = cur.lastrowid
                index.add(story_id, vec, article["title"])
                created += 1
            conn.execute("UPDATE articles SET story_id = ? WHERE id = ?", (story_id, article["id"]))
        conn.commit()
    log.info("Clustered %d articles: %d new stories, %d joined existing", len(articles), created, joined)

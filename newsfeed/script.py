"""Turns the episode plan into a spoken script, then records what the listener now knows."""
import logging
import re
from datetime import date

from . import db
from .llm import Ollama
from .planner import Item

log = logging.getLogger(__name__)

WORDS_PER_MINUTE = 150

SPOKEN_RULES = (
    "Write plain spoken English for text-to-speech: no markdown, bullet points, headings, emoji, "
    "URLs, stage directions or sound-effect cues. Spell out abbreviations the first time if a listener "
    "might not know them. Attribute claims to their sources naturally (\"according to the BBC\"). "
    "Never invent facts beyond the reporting provided."
)


def _host_system(cfg: dict) -> str:
    show = cfg["show"]
    return (
        f"You are {show['host_name']}, host of \"{show['title']}\", a warm, sharp, conversational "
        f"daily news podcast for one listener. {SPOKEN_RULES}"
    )


def _reporting(item: Item) -> str:
    return "\n".join(
        f"- [{a['source']}] {a['title']}: {(a['summary'] or '')[:700]}" for a in item.articles[:8]
    )


def _segment(llm: Ollama, cfg: dict, item: Item, words: int, position: str) -> str:
    if item.kind == "followup":
        task = (
            f"Write a follow-up segment of about {words} words on a story the listener has heard before. "
            "Start with a one or two sentence recap of what they already know (signal it's an update, e.g. "
            "\"Picking up a story we've been following...\"), then focus on what's new.\n\n"
            f"WHAT THE LISTENER ALREADY KNOWS:\n{item.story['summary']}\n\n"
            f"WHAT CHANGED: {item.what_changed}\n\n"
        )
    else:
        task = (
            f"Write a news segment of about {words} words on this story: what happened, why it matters, "
            "and what to watch next.\n\n"
        )
    task += f"REPORTING:\n{_reporting(item)}\n\nThis segment is {position}. Begin with a brief spoken transition."
    return llm.chat(_host_system(cfg), task)


def write_script(llm: Ollama, cfg: dict, items: list[Item], today: date) -> list[str]:
    """Returns the episode as a list of spoken segments (intro, stories..., outro)."""
    show = cfg["show"]
    story_words = max(120, int(show["target_minutes"] * WORDS_PER_MINUTE * 0.85 / len(items)))
    greeting = f"Greet {show['listener_name']} by name. " if show["listener_name"] else ""
    rundown = "\n".join(
        f"- {i.story['headline']}{' (update on an ongoing story)' if i.kind == 'followup' else ''}" for i in items
    )

    segments = [llm.chat(_host_system(cfg), (
        f"Write the 40-70 word opening for today's episode, {today:%A, %B} {today.day}. "
        f"{greeting}Introduce yourself and the show, then tease the top two or three stories in one breath.\n\n"
        f"TODAY'S RUNDOWN:\n{rundown}"
    ))]
    for n, item in enumerate(items):
        position = "the lead story" if n == 0 else ("the final story" if n == len(items) - 1 else f"story {n + 1}")
        segments.append(_segment(llm, cfg, item, story_words, position))
        log.info("Wrote segment %d/%d: %s", n + 1, len(items), item.story["headline"])
    segments.append(llm.chat(_host_system(cfg), (
        "Write a 30-50 word sign-off for today's episode. Briefly mention one thing to keep an eye on today "
        f"from these stories, then say goodbye until tomorrow.\n\nTODAY'S RUNDOWN:\n{rundown}"
    )))
    return [_strip_markup(s) for s in segments if s.strip()]


def _strip_markup(text: str) -> str:
    text = re.sub(r"^\s*[#>*-]+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\[(?:music|sound|sfx|pause|intro|outro)[^\]]*\]", "", text, flags=re.IGNORECASE)
    return text.strip()


def episode_title(llm: Ollama, items: list[Item], today: date) -> str:
    headline = llm.chat(
        "You write short podcast episode titles. Reply with the title only, max 8 words, no quotes.",
        "Stories:\n" + "\n".join(i.story["headline"] for i in items[:4]),
        temperature=0.4,
    ).strip().strip('"')
    return f"{today:%b} {today.day}: {headline}"


def show_notes(items: list[Item]) -> str:
    lines = []
    for item in items:
        tag = " (update)" if item.kind == "followup" else ""
        links = ", ".join(f'<a href="{a["url"]}">{a["source"]}</a>' for a in item.articles[:4])
        lines.append(f"<li>{item.story['headline']}{tag} — {links}</li>")
    return "<ul>" + "".join(lines) + "</ul>"


MEMORY_SYSTEM = (
    "You maintain a running memory of what a podcast listener has been told about a news story. "
    "Merge the previous memory with today's segment into an updated summary of at most 5 sentences, "
    "keeping key facts, names, numbers and dates (use absolute dates). Plain text only. "
    'Reply with JSON: {"headline": "short neutral headline", "summary": "..."}'
)


def remember_aired(conn, llm: Ollama, items: list[Item], segments: list[str], today: date) -> None:
    """After an episode is produced, update each story's memory and mark its articles as aired."""
    for item, segment in zip(items, segments[1:-1]):
        result = llm.chat_json(MEMORY_SYSTEM, (
            f"DATE AIRED: {today.isoformat()}\n\n"
            f"PREVIOUS MEMORY:\n{item.story['summary'] or '(none — first time covered)'}\n\n"
            f"TODAY'S SEGMENT:\n{segment}"
        ))
        conn.execute(
            "UPDATE stories SET headline = ?, summary = ?, last_aired = ?, times_aired = times_aired + 1"
            " WHERE id = ?",
            (
                result.get("headline") or item.story["headline"],
                result.get("summary") or segment[:1200],
                db.now(),
                item.story["id"],
            ),
        )
        conn.executemany("UPDATE articles SET aired = 1 WHERE id = ?", [(a["id"],) for a in item.articles])
    conn.commit()

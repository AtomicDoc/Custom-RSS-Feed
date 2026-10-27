"""Turns the episode plan into a spoken script, then records what the listener now knows."""
import logging
import re
from datetime import date

from . import db
from .llm import Ollama
from .planner import Item

log = logging.getLogger(__name__)

WORDS_PER_MINUTE = 150

STYLE_GUIDE = """\
How you sound:
- Like a smart friend who follows the news, talking over coffee: relaxed, curious, a little personality. Never stiff.
- Use contractions (it's, they're, didn't). Mix short sentences with longer ones. Start some sentences with \
"And" or "But" if that's how you'd say it.
- Talk to the listener as "you". Brief, human reactions are good ("which is a lot of money", "that's the part \
I'd keep an eye on"), but never take political sides.
- Say things the way you'd say them out loud: plain words, rounded numbers ("about two billion dollars", \
not "$2.04 billion"), and only the details that matter.
- Mention sources lightly, not in every sentence ("NPR's reporting this morning...", "according to Reuters").
- Tell each story as a story (what happened, why it matters, what's next), not a list of facts.

Never use these news-anchor cliches: "in a significant development", "it remains to be seen", \
"only time will tell", "in other news", "let's dive in", "stay tuned", "breaking news", "this comes as", \
"amid", "landscape", "navigate", "a testament to", "that's all for now", "without further ado".

Tone example (a different story; copy the feel, not the words):
TOO STIFF: "In a significant development, the Federal Reserve announced on Wednesday that it would maintain \
interest rates at their current level, citing ongoing inflationary pressures."
NATURAL: "The Fed held rates steady again yesterday. Not a huge surprise. Inflation's still running a little \
hot, and they've been pretty clear they won't cut until that changes. So if you were hoping for cheaper \
mortgage rates this fall, don't hold your breath."

Format: plain spoken text only, for text-to-speech. No markdown, lists, headings, emoji, URLs, stage directions \
or sound cues. Use commas and periods to pace the speech. Never invent facts beyond the reporting you're given.\
"""


def _host_system(cfg: dict) -> str:
    show = cfg["show"]
    listener = f", {show['listener_name']}" if show["listener_name"] else ""
    return (
        f"You are {show['host_name']}, host of \"{show['title']}\", a morning news podcast made for one "
        f"listener{listener}.\n\n{STYLE_GUIDE}"
    )


def _reporting(item: Item) -> str:
    return "\n".join(
        f"- [{a['source']}] {a['title']}: {(a['summary'] or '')[:700]}" for a in item.articles[:8]
    )


def _tail(text: str, sentences: int = 2) -> str:
    return " ".join(re.split(r"(?<=[.!?])\s+", text.strip())[-sentences:])


def _segment(llm: Ollama, cfg: dict, item: Item, words: int, position: str, previous: str) -> str:
    if item.kind == "followup":
        task = (
            f"Write a follow-up segment of about {words} words on a story the listener has heard about before. "
            "Remind them where things stood in a sentence or two, the way you'd catch up a friend, then spend "
            "most of the time on what's new.\n\n"
            f"WHAT THE LISTENER ALREADY KNOWS:\n{item.story['summary']}\n\n"
            f"WHAT CHANGED: {item.what_changed}\n\n"
        )
    else:
        task = f"Write a segment of about {words} words on this story: what happened, why it matters, what's next.\n\n"
    task += (
        f"REPORTING:\n{_reporting(item)}\n\n"
        f"This is {position}. The previous part of the show ended with: \"{previous}\"\n"
        "Open with a short, natural bridge from that into this story. Vary it; don't use \"next up\", "
        "\"moving on\", \"turning to\" or \"switching gears\". End on the story itself, not a sign-off. "
        "Reply with the segment only."
    )
    return llm.chat(_host_system(cfg), task)


POLISH_SYSTEM = (
    "You are a podcast script editor. Rewrite the segment so it sounds like a real person talking, not someone "
    "reading copy: contractions, natural rhythm, plain words, no news-anchor cliches. Keep every fact, name and "
    "number, and keep about the same length. Reply with only the rewritten segment."
)
PREAMBLE_RE = re.compile(r"^\s*(here(?:'s| is)[^\n]*?:|sure[^\n]*?:)\s*", re.IGNORECASE)


def _polish(llm: Ollama, cfg: dict, text: str) -> str:
    """Second pass: smaller models write noticeably more natural speech when asked to edit their own draft."""
    edited = PREAMBLE_RE.sub("", llm.chat(f"{POLISH_SYSTEM}\n\n{STYLE_GUIDE}", text)).strip()
    # Guard against the editor collapsing or truncating the segment.
    return edited if len(edited) > 0.6 * len(text) else text


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
        f"{greeting}Say who you are and what show this is in a relaxed way, then tease the top two or three "
        f"stories like you're genuinely interested in them. Reply with the opening only.\n\n"
        f"TODAY'S RUNDOWN:\n{rundown}"
    ))]
    for n, item in enumerate(items):
        position = "the lead story" if n == 0 else ("the last story" if n == len(items) - 1 else f"story {n + 1}")
        segment = _segment(llm, cfg, item, story_words, position, _tail(segments[-1]))
        if show.get("polish_pass", True):
            segment = _polish(llm, cfg, segment)
        segments.append(segment)
        log.info("Wrote segment %d/%d: %s", n + 1, len(items), item.story["headline"])
    segments.append(llm.chat(_host_system(cfg), (
        "Write a 30-50 word sign-off for today's episode. Mention one thing worth keeping an eye on today "
        "from these stories, then say bye until tomorrow, casually, the way you'd end a phone call with a "
        f"friend. Reply with the sign-off only.\n\nTODAY'S RUNDOWN:\n{rundown}"
    )))
    # Keep empty segments as placeholders so segments[1:-1] stays aligned with items in remember_aired().
    return [_strip_markup(s) for s in segments]


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

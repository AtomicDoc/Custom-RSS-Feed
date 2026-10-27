"""CLI entry point: python -m newsfeed {fetch|run|feeds}"""
import argparse
import logging
import sys
import tempfile
from datetime import date
from pathlib import Path

from . import audio, config, db, feeds, fetch, memory, planner, script
from .llm import Ollama, OllamaError

log = logging.getLogger("newsfeed")


def cmd_fetch(conn, cfg, llm, args):
    added = fetch.fetch_all(conn, cfg)
    log.info("Fetched %d new articles", added)
    memory.cluster_new_articles(conn, llm, cfg)
    feeds.write_combined_feed(conn, cfg)


def cmd_run(conn, cfg, llm, args):
    today = date.today()
    if conn.execute("SELECT 1 FROM episodes WHERE date = ?", (today.isoformat(),)).fetchone() and not args.force:
        log.info("Episode for %s already exists (use --force to rebuild)", today)
        return

    cmd_fetch(conn, cfg, llm, args)
    items = planner.plan_episode(conn, llm, cfg)
    if not items:
        log.warning("Nothing new to report today; no episode produced")
        return

    segments = script.write_script(llm, cfg, items, today)
    title = script.episode_title(llm, items, today)
    text = "\n\n".join(segments)
    if args.script_only:
        print(f"# {title}\n\n{text}")
        return

    episodes_dir = cfg["paths"]["public_dir"] / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{today.isoformat()}.mp3"
    mp3 = episodes_dir / filename
    tts = cfg["tts"]
    llm.unload()   # the LLM and the TTS model don't both fit in 8GB of VRAM
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "speech.wav"
        audio.synthesize(segments, tts, wav)
        duration = audio.encode_mp3(wav, mp3, tts.get("intro_audio", ""), tts.get("outro_audio", ""))

    conn.execute(
        "INSERT OR REPLACE INTO episodes (date, title, script, show_notes, filename, bytes, duration, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (today.isoformat(), title, text, script.show_notes(items), filename, mp3.stat().st_size, duration, db.now()),
    )
    conn.commit()
    # Only commit stories to memory once the episode actually exists, so a failed run doesn't skip news.
    script.remember_aired(conn, llm, items, segments, today)
    feeds.prune_episodes(conn, cfg)
    feeds.write_podcast_feed(conn, cfg)
    log.info("Episode ready: %s (%d:%02d)", title, duration // 60, duration % 60)


def cmd_feeds(conn, cfg, llm, args):
    feeds.write_combined_feed(conn, cfg)
    feeds.write_podcast_feed(conn, cfg)


def cmd_voices(conn, cfg, llm, args):
    llm.unload()
    out_dir = cfg["paths"]["public_dir"] / "voice-tests"
    made = audio.voice_tests(cfg["tts"], out_dir)
    base = cfg["show"]["base_url"].rstrip("/")
    print("\nListen and compare:")
    for mp3 in made:
        print(f"  {base}/voice-tests/{mp3.name}")


def main():
    parser = argparse.ArgumentParser(prog="newsfeed", description="Personal RSS + AI morning news podcast")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="pull sources, update story memory and combined.xml")
    run = sub.add_parser("run", help="fetch + build today's podcast episode")
    run.add_argument("--force", action="store_true", help="rebuild even if today's episode exists")
    run.add_argument("--script-only", action="store_true", help="print the script only; no audio, and covered stories stay unaired")
    sub.add_parser("feeds", help="regenerate both feeds from the database")
    sub.add_parser("voices", help="render a sample paragraph in each candidate voice to public/voice-tests/")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = config.load()
    conn = db.connect(cfg["paths"]["data_dir"])
    llm = Ollama(cfg["ollama"])
    try:
        if args.command in ("fetch", "run"):
            llm.check()
        commands = {"fetch": cmd_fetch, "run": cmd_run, "feeds": cmd_feeds, "voices": cmd_voices}
        commands[args.command](conn, cfg, llm, args)
    except OllamaError as exc:
        log.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

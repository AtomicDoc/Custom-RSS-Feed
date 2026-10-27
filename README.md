# Morning Brief — personal RSS feed + local AI news podcast

Two feeds, generated entirely on your own machine:

| Feed | URL | What it is |
|---|---|---|
| **Sources** | `<base_url>/combined.xml` | Every article from the sources in `config/feeds.yaml`, merged into one RSS feed. Subscribe in any RSS reader. |
| **Podcast** | `<base_url>/podcast.xml` | A daily episode written by a local LLM (Ollama) and voiced by local TTS (Kokoro). Subscribe in any podcast app. |

The podcast has a **story memory**: it won't repeat stories you've already heard. When an old story has
real new developments, the host gives a short recap and then covers what changed.

## How it works

```
feeds.yaml ─► fetch ─► embed (nomic-embed-text) ─► match to recent stories ─► SQLite story memory
                                                        │  ≥0.86 similar: same story
                                                        │  0.72–0.86: LLM decides
                                                        │  <0.72: new story
                                                        ▼
            editor LLM picks & orders today's stories (your interests from settings.yaml)
                                                        │
             new story ──► full segment         previously aired ──► "anything genuinely new?"
                                                        ├─ no  → skipped, never repeated
                                                        └─ yes → recap + what changed
                                                        ▼
           script (one LLM call per segment) ─► Kokoro TTS (GPU) ─► ffmpeg MP3 ─► podcast.xml
                                                        │
           after airing, each story's "what the listener knows" summary is updated for tomorrow
```

## Setup (Windows 11)

Tested target: 32GB RAM, RTX 4060 Laptop (8GB VRAM), i7-13700HX. The default `llama3.1:8b` fits entirely
in VRAM, and Kokoro runs on CUDA, so a ~12-minute episode builds in a few minutes.

1. **Clone the repo**
   ```powershell
   git clone https://github.com/AtomicDoc/Custom-RSS-Feed.git
   cd Custom-RSS-Feed
   ```
2. **Install everything** (Ollama, ffmpeg, Caddy, Python 3.12 via winget, a venv with CUDA PyTorch, and the models):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\deploy\setup.ps1
   ```
   It finishes by printing `CUDA available for TTS: True`. If it prints `False`, update your NVIDIA driver.
   Optional: install [espeak-ng](https://github.com/espeak-ng/espeak-ng/releases) (MSI) to help Kokoro pronounce unusual words.
3. **Edit your config**: `config/feeds.yaml` (sources) and `config/settings.yaml` (show name, your
   interests, episode length, voice).
4. **Try it**
   ```powershell
   .\.venv\Scripts\python -m newsfeed run --script-only   # print a script, no audio
   .\.venv\Scripts\python -m newsfeed run --force         # full episode -> public\episodes\
   ```
   `--script-only` doesn't mark the covered stories as aired, so you can repeat it while tuning prompts.
5. **Schedule it** (Administrator PowerShell):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\deploy\register-tasks.ps1 -EpisodeTime 05:30
   ```
   This creates three tasks: the daily episode (wakes the PC if needed), a fetch every 3 hours so overnight news
   isn't missed, and a Caddy web server serving `public\` on port 8080. Logs go to `data\logs\`.
6. **Keep the laptop awake.** Plug it in, then in an Administrator PowerShell:
   ```powershell
   powercfg /change standby-timeout-ac 0                                        # never sleep on AC
   powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0             # lid close does nothing
   powercfg /setactive SCHEME_CURRENT
   ```

## Getting the podcast on your phone

Podcast apps such as Overcast, Pocket Casts and Apple Podcasts fetch feeds from their own servers, so the feed needs a
public HTTPS URL. The easiest free option with a stable URL is **Tailscale Funnel**:

1. Install Tailscale (`winget install Tailscale.Tailscale`) and sign in.
2. `tailscale funnel --bg 8080` (the first time, it gives you a link to enable Funnel for your tailnet).
3. It prints a URL like `https://my-laptop.tail1234.ts.net`. Put that in `settings.yaml` under `show.base_url`,
   then run `.\.venv\Scripts\python -m newsfeed feeds` to rewrite the feeds with that URL.
4. In your podcast app, choose "add by URL" and enter `https://my-laptop.tail1234.ts.net/podcast.xml`.

If you own a domain on Cloudflare, a named **Cloudflare Tunnel** (`cloudflared`) to `localhost:8080` works too.
The feed sets `itunes:block`, so it won't be listed in public podcast directories, but anyone who has the URL
can open it.

Optional: drop a square `cover.jpg` (1400–3000px) into `public\` for episode artwork.

## Tuning

| Want | Change in `settings.yaml` |
|---|---|
| Better writing | `ollama.chat_model: gemma3:12b` or `qwen3:14b` (spills partly into system RAM: slower, but fine at 5am) |
| Different voice | `tts.kokoro_voice`: `af_heart`, `am_michael`, `bf_emma`, `bm_george`, … |
| Fewer "same story" merges | raise `memory.same_story_auto` / `same_story_maybe` |
| Catch more follow-ups | lower them, or raise `memory.story_window_days` |
| Intro music | `tts.intro_audio: C:\path\to\jingle.mp3` |

## Commands

```
python -m newsfeed fetch                 # pull sources, update story memory + combined.xml
python -m newsfeed run [--force]         # fetch + build today's episode + podcast.xml
python -m newsfeed run --script-only     # preview today's script
python -m newsfeed feeds                 # regenerate both feeds (e.g. after changing base_url)
```

## Layout

```
config/          feeds.yaml (sources), settings.yaml (everything else)
newsfeed/        fetch → memory → planner → script → audio → feeds
deploy/          Windows setup, run wrapper, Task Scheduler registration
data/            SQLite story memory + logs (git-ignored)
public/          served over HTTP: combined.xml, podcast.xml, episodes/ (git-ignored)
```

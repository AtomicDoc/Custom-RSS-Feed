# Morning Brief — personal RSS feed + local AI news podcast

Two feeds, generated entirely on your own machine:

| Feed | URL | What it is |
|---|---|---|
| **Sources** | `<base_url>/combined.xml` | Every article from the sources in `config/feeds.yaml`, merged into one RSS feed. Subscribe in any RSS reader. |
| **Podcast** | `<base_url>/podcast.xml` | A daily episode written by a local LLM (Ollama) and voiced by local TTS (Chatterbox or Kokoro). Subscribe in any podcast app. |

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
           script (draft + polish per segment) ─► Chatterbox TTS (GPU) ─► ffmpeg MP3 ─► podcast.xml
                                                        │
           after airing, each story's "what the listener knows" summary is updated for tomorrow
```

## Setup (Windows 11)

Tested target: 32GB RAM, RTX 4060 Laptop (8GB VRAM), i7-13700HX. The default `llama3.1:8b` fits entirely
in VRAM, and Kokoro runs on CUDA, so a ~12-minute episode builds in a few minutes.
The defaults now favor quality over speed: `gemma3:12b` (slightly overflows into system RAM) plus
Chatterbox TTS, so expect roughly 20-40 minutes per 20-minute episode. That's fine for a 5:30am job.
Ollama's model is unloaded before TTS starts, so the two never compete for the 8GB of VRAM.

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
public HTTPS URL. With your own domain, use a **Cloudflare Tunnel**: free, no port forwarding, and it runs as a
Windows service so it survives reboots.

1. **Put the domain on Cloudflare** (skip if it already is): add it at dash.cloudflare.com (Free plan) and change
   the nameservers at your registrar to the two Cloudflare gives you.
2. **Create the tunnel:** Cloudflare dashboard → *Zero Trust* → *Networks* → *Tunnels* → *Create a tunnel* →
   *Cloudflared*, name it `morning-brief`.
3. **Install the connector:** pick *Windows*, then run the command it shows in an Administrator PowerShell
   (`setup.ps1` has already installed `cloudflared`):
   ```powershell
   cloudflared.exe service install <your-token>
   ```
4. **Route a hostname:** on the tunnel's *Public Hostname* tab, add subdomain `podcast` on your domain,
   with service type **HTTP** and URL **`localhost:8080`** (the Caddy server from `register-tasks.ps1`).
5. **Point the feeds at it:** set `show.base_url: "https://podcast.yourdomain.com"` in `settings.yaml`, then run
   `.\.venv\Scripts\python -m newsfeed feeds` to rewrite the feeds with that URL.
6. **Subscribe:** in your podcast app, choose "add by URL" and enter `https://podcast.yourdomain.com/podcast.xml`.
   The source feed is at `/combined.xml` for your RSS reader.

Cloudflare gotchas:
- Don't put **Cloudflare Access** (a login page) in front of this hostname, and keep **Bot Fight Mode** off.
  Podcast apps fetch feeds from their servers, and both would block them.
- Cloudflare caches `.mp3` files. If you rebuild an episode with `run --force`, purge the cache for that URL
  (*Caching* → *Purge Cache* → *Custom Purge*), or the app will keep the old audio.

The feed sets `itunes:block`, so it won't be listed in public podcast directories, but anyone who has the URL
can open it.

Optional: drop a square `cover.jpg` (1400–3000px) into `public\` for episode artwork.

## Tuning

| Want | Change in `settings.yaml` |
|---|---|
| Faster writing | `ollama.chat_model: llama3.1:8b` (fits fully in VRAM; plainer prose), or `show.polish_pass: false` |
| Different writing model | `qwen3:14b` / `qwen3:30b-a3b`, with `ollama.think: false` |
| Different voice | run `python -m newsfeed voices` and compare, then set `tts.engine` + voice |
| A specific voice | `tts.chatterbox_voice: C:\path\to\clip.wav` (10-20s clean speech you have rights to) |
| Livelier / calmer delivery | `tts.chatterbox_exaggeration` (0.35 calm … 0.7 lively) |
| Fewer "same story" merges | raise `memory.same_story_auto` / `same_story_maybe` |
| Catch more follow-ups | lower them, or raise `memory.story_window_days` |
| Intro music | `tts.intro_audio: C:\path\to\jingle.mp3` |

## Commands

```
python -m newsfeed fetch                 # pull sources, update story memory + combined.xml
python -m newsfeed run [--force]         # fetch + build today's episode + podcast.xml
python -m newsfeed run --script-only     # preview today's script
python -m newsfeed feeds                 # regenerate both feeds (e.g. after changing base_url)
python -m newsfeed voices               # sample every voice -> <base_url>/voice-tests/*.mp3
```

## Layout

```
config/          feeds.yaml (sources), settings.yaml (everything else)
newsfeed/        fetch → memory → planner → script → audio → feeds
deploy/          Windows setup, run wrapper, Task Scheduler registration
data/            SQLite story memory + logs (git-ignored)
public/          served over HTTP: combined.xml, podcast.xml, episodes/ (git-ignored)
```

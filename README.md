# Adeola

A voice and text assistant for Windows 11 that runs on your own machine. She opens apps,
websites and your projects, controls music and volume, remembers things for you, answers from
your Obsidian notes, runs multi-step requests, and shows what she's doing in a browser HUD.
Speech recognition (Whisper), her voice (Kokoro) and the default brain (Ollama `qwen3:4b`) all
run locally. A cloud brain (Gemini, Groq, OpenRouter or Claude) is optional and off until you
switch to it.

## Setup (PowerShell)

```powershell
cd $HOME\Projects\adeola          # wherever you unzipped this
py -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -r requirements.txt
```

Ollama must be running with `qwen3:4b` pulled (`ollama pull qwen3:4b`). For the quick and coder
brains (see [Brains](#brains-local-cloud-and-privacy)), also pull `qwen3:1.7b` and `qwen2.5-coder:3b`.

## Run

```powershell
python main.py -v
```

`-v` shows which path handled each command and how long it took. Try:

- `open brave` · `adeola, launch vs code` · `open notepad and calculator`
- `open github.com` · `open youtube`
- `search for jetpack compose navigation`
- `what time is it` · `lock my pc`
- `I want to write some code` (handled by the model)
- `reload apps` after installing something new · `exit` to quit

## Voice

```powershell
python -m tests.mic_check          # check your mic and Whisper first
python main.py --voice -v
```

Two ways to talk, both on with `--voice`:

- **Wake word:** say "Adeola, open Brave". Until you've trained her name
  ([docs/wake_word.md](docs/wake_word.md)), the test phrase is "hey jarvis".
- **Push-to-talk:** hold **Right Ctrl**, speak, release.
- **Mid-sentence pause window:** after `END_SILENCE_SECONDS` (0.6 s) of quiet, Adeola waits an extra `FINISH_SECONDS` (0.35 s) before ending capture. If you keep speaking after a brief breath or pause, she resumes listening seamlessly instead of cutting you off. If she cuts you off mid-thought, raise these in `config.py`.
- **Voice barge-in:** while Adeola is speaking, wake-word detection stays active at a higher threshold (`BARGE_IN_THRESHOLD = 0.75`). Saying her wake word ("Adeola, stop" or "Adeola, wait") cuts off playback immediately and captures your new command.

A high beep means she's listening, a low one means she stopped. Typing still works at the same time.
`python -m tests.wake_check` shows live wake-word scores for tuning `WAKE_THRESHOLD`.

- Speech recognition is Whisper `base.en`, running on the CPU so the GPU stays free for the model.
  The first run downloads it (about 145 MB).
- Mishearing words? Set `STT_MODEL = "small.en"` in `config.py` (more accurate, slower).
- Change the key with `PUSH_TO_TALK_KEY`, e.g. `"alt_r"` or `"f9"`.
- Wrong microphone? `python -m tests.mic_check --list`, then try `--device <number>`.

## Adeola's voice

With `--voice` (or `--speak` for typed commands), Adeola answers out loud using Kokoro on the
CPU. Her voice is a blend of Kokoro voices set by `VOICE_BLEND` in `config.py`, so it's yours alone.
The model downloads into `models/` on first run (about 205 MB). Press the talk key to interrupt her.

```powershell
python -m tests.voice_lab     # audition voices and blends, then type 'save'
```

If she says a word wrong, add a phonetic spelling to `SPOKEN_FORMS`, e.g. `{"Adeola": "Ahdeh-ohla"}`.

## Music and volume

Adeola controls whatever is playing through Windows' own media controls (the same ones your
keyboard's play/pause key uses), so it works for Spotify Web in Brave, the Spotify app, YouTube
and most players:

`pause` · `resume` · `next song` · `previous track` · `what's playing?` · `turn it up` ·
`turn it down` · `mute` · `unmute` · `set volume to 30`

"Pause" goes to whatever is actually playing, and "play" to whatever was paused.

## Response speed

Her voice used to start 0.5–2 seconds after the action, while each sentence was being generated.
Now each sentence's audio is cached (`data/voice_cache/`, safe to delete), so a repeated reply
plays at once. While she's idle after startup, she generates her most likely replies in advance:
your most-opened apps, preferred apps, projects and common answers. Long replies are spoken clause
by clause, so speech starts after the first few words. With `-v` she prints
`[voice started after N ms]` for each reply. Sentences up to 170 characters are spoken whole with an 80 ms pause between sentences (only sentences exceeding 170 characters split at `, ; :` clause boundaries), while the next chunk is prefetched during playback to avoid mid-sentence cuts. To keep Ollama warm in GPU memory during idle stretches, set `KEEP_WARM_SECONDS` in `config.py` (e.g. `600` to ping every 10 idle minutes; `0` = off by default).

## Memory and projects

Adeola keeps a small memory in `data/adeola.db`, a single file on this PC (never uploaded, ignored
by git). Things you can say or type:

| You say | What happens |
|---|---|
| `remember that my exam is on Friday` · `note to self: ...` · `log this: ...` · `don't let me forget ...` | Saves it |
| `what do you remember` · `what am I working on?` · `do you remember my exam` | Reads it back |
| `forget my exam` · `forget that` | Deletes one memory |
| `forget everything`, then `yes, forget everything` | Erases all of them (asks first) |
| `open my Quail project` · `let's work on Quail` | Opens the folder in Android Studio or VS Code |
| `list my projects` | Lists the projects she knows |
| `what was I doing earlier?` | Apps, projects, sites and searches from the last day |

**Projects** are found automatically in the folders listed in `PROJECT_ROOTS` (`config.py`):
`~/AndroidStudioProjects`, `~/Projects`, `~/source/repos`, `~/Documents/GitHub` and
`~/Documents/Projects`. Folders with Gradle files open in Android Studio, others in VS Code. For a
folder somewhere else, type `add project NAME at C:\path\to\folder` once. After creating a new
project folder, type `reload`.

**Keep or skip.** If you mention something that may matter later without asking her to remember
it ("my dentist appointment is on Monday, any tips for staying calm?"), she answers and then asks
"Want me to remember that...? Say keep or skip." Nothing is stored unless you say keep, and a skip
isn't asked again that session. Turn it off with `OFFER_TO_REMEMBER = False` in `config.py`.

**Fading tips.** Now and then, when she opens an app, she teaches you a shortcut for it ("Tip:
Ctrl+L jumps straight to the address bar"). Tips come on the 1st, 3rd, 6th, 10th... open, each is
retired after being taught twice, and when you've learned them all she stops. The idea is that she
makes you faster, not more dependent. Say "no more tips" to switch them off, or "turn tips on".
Add your own in `app/tools/tips.py`.

When a question goes to the model, Adeola also passes along the few memories that relate to it,
so "should I study tonight?" can take your exam date into account.

## Answers from your notes

Adeola indexes the markdown files in your Obsidian vault (`NOTES_DIRS` in `config.py`, defaulting to
`~/Documents/qabw` or the `ADEOLA_NOTES` environment variable). The index is strictly **read-only**:
Adeola never writes, moves or deletes anything in your vault, and skips `.obsidian`, `.trash`,
`.git`, `Templates` and files over `NOTES_MAX_BYTES` (200 KB).

| You say | What happens |
|---|---|
| `what do my notes say about the finish window` | Pulls the top matching excerpts and answers from them |
| `search my notes for zebras` · `check my vault for X` | Searches your notes (never opens a browser); says `"Your notes don't mention X."` if nothing matches |
| Any question that clearly matches a note | Attaches the top excerpts (`NOTES_IN_PROMPT = 3`) so the model can ground its answer |
| `reload` | Re-scans apps, projects and notes |

When a reply uses your notes, Adeola prints the note titles underneath:

```text
   (from your notes: Finish Window, Operating Guide)
```

Sources are printed only — never spoken out loud. Saying `"remember that ..."` or `"note to self: ..."` also saves a dated markdown file in `data/captures/` that is indexed immediately.

## Brains: local, cloud and privacy

**Local shelf** (`BRAINS` in `config.py`): `quick` (`qwen3:1.7b`), `smart` (`qwen3:4b`, the default)
and `coder` (`qwen2.5-coder:3b`). She says "One moment." while a new one loads.

- `which brain are you on` · `switch to your quick brain` · `use your coder brain` · `go local`
- **Auto brain** is **off by default**. On a 6 GB GPU the smart and coder models don't fit together, so
  every automatic switch is a full reload: about 5.3 s to coder and 5.8 s back, against about 0.8 s for a
  normal turn. `python -m tools.preflight` times it on your machine. Turn it on with
  `ADEOLA_AUTO_BRAIN=1`. Then coding questions move her to `coder` and longer "why / explain /
  compare" questions move her to `smart`. She never drops back down to `quick` automatically, and waits
  `AUTO_SWITCH_COOLDOWN_SECONDS` (60) between automatic switches. Naming a brain pins it;
  `use auto brain` unpins.

**Cloud brains** (`CLOUD_BRAINS` in `config.py`): `gemini`, `groq`, `openrouter`, `claude`. Put the key
in a `.env` file in the project folder (it's gitignored), for example `GEMINI_API_KEY=...`. The others
use `GROQ_API_KEY`, `OPENROUTER_API_KEY` and `ANTHROPIC_API_KEY`. Then say `switch to gemini` or
`go cloud` (which uses `ADEOLA_CLOUD_BRAIN`, default `gemini`). `go local` switches back. Without a key
she stays local and tells you which variable is missing.

**What a cloud brain sees.** By default, only your current question and your recent turns with that
cloud brain. Your
saved memory, your vault notes, and the output of private tools (`recall`, `recent_activity`,
`forget`, `search_notes`) stay on this PC. When one of those tools runs, she answers from its output
locally instead of sending it to the cloud. To share more, set either of these before starting:

- `ADEOLA_CLOUD_SHARES_MEMORY=1`: remembered facts and memory tool output
- `ADEOLA_CLOUD_SHARES_NOTES=1`: vault excerpts and note search output

While a cloud brain is active, the HUD shows a `CLOUD · <PROVIDER>` badge, and `are you local` /
`what needs the internet` say so honestly. A cloud request that fails tells you which provider
failed and suggests `go local`.

**Voices** (`VOICES` in `config.py`): `which voice are you using` · `use your calm voice` ·
`switch to your warm voice`.

## Multi-step requests (agent mode)

Start a request with `agent:`, `research:` or `deep:` and she plans the tools it needs, runs them,
reads the results and answers once every part is covered (at most 4 steps):

- `agent: what's the date, what projects do I have, and when is my exam?`
- `agent: open notepad and tell me the time`

The same rules apply as for single commands: only the tools in `registry.py` can run, a repeated
identical call is dropped, and on a cloud brain private tool output stays local.

## Skills

A skill is a saved routine in `skills/<name>/SKILL.md`: front matter (`name`, `description`,
`triggers`) plus the prompt to run. Two come with the project: `morning-brief` and `project-status`.
There are three ways to run one:

- Say or type one of its triggers exactly: `start my day`, `morning brief`, `project status`.
  Triggers are checked before the instant commands, so don't reuse a phrase she already
  understands (a test checks the shipped ones).
- Ask for it by name: `run the morning brief skill`. An unknown name gets the list of skills.
- Use the HUD's **Skills & Actions** buttons, or `POST /api/skills/run` with `{"name": "..."}`.

Under the buttons, the HUD lists requests you've sent to the model more than once. Those are good
candidates for a new skill. After adding or editing a `SKILL.md`, type `reload`.

Skill output is saved as markdown in `data/outputs/`. Your vault stays read-only unless you set
`ADEOLA_VAULT_OUT` to a folder (for example inside the vault) to write there instead.

## HUD, Android & Tailscale (`--serve`)

Run Adeola's local HTTP server alongside the terminal or voice loop:

```powershell
python main.py --serve              # text + HTTP server on port 8765
python main.py --voice --serve      # PC voice, plus the HUD and phone companion
python main.py --serve --port 9000  # custom port
```

- **Visual HUD:** open `http://127.0.0.1:8765/hud` on the PC. By default it shows just her presence,
  a plasma orb on black that reacts as she wakes, listens, thinks and speaks. The **Analysis** button
  slides in a dashboard with her pipeline stages, latencies, route mix, conversation and traces, plus
  a text box, brain pills and the Skills & Actions buttons. It updates live over server-sent events
  (`/api/events`). On the PC it's the face of the voice loop: she still listens on the PC mic and
  speaks on the PC speakers.
- **Phone companion (PWA):** Adeola prints a Tailscale link at startup, like
  `http://100.x.y.z:8765/?token=...`. Open it once in Chrome or Brave on Android (it saves the token)
  and tap **Add to Home screen**. It has a text composer, **Hold to Talk** (your phone records, Whisper
  transcribes on the PC, and Kokoro's reply plays on the phone), quick-action chips and brain pills.

**Who can connect.** Access is checked per request, by where it comes from:

| From | Allowed? |
|---|---|
| This PC (`127.0.0.1` / `::1`) | Yes, no token needed. A wrong token is still refused |
| Tailscale devices (`100.64.0.0/10`) | Only with the token |
| Anything else (home Wi-Fi, café Wi-Fi, …) | Refused (403), even with the token, unless `ADEOLA_ALLOW_LAN=1` |

The token is `ADEOLA_TOKEN` if you set it. Otherwise one is generated on first `--serve` and kept in
`data/server_token`, so the phone's saved copy keeps working. Delete that file to issue a new one;
you'll then need to open the new link on the phone. Send it as `?token=...`,
`Authorization: Bearer <token>` or `X-Adeola-Token: <token>`.

- **JSON API endpoints:**
  - `GET /api/status` — active brain, model, voice, whether it's a cloud brain (`is_cloud`), and indexed counts (`apps`, `projects`, `notes`, `facts`)
  - `GET /api/telemetry` — everything the HUD dashboard shows: status, latencies, route mix, recent traces
  - `GET /api/events` — server-sent events stream of live state and finished turns
  - `GET /api/skills` · `POST /api/skills/run` — list skills (and suggestions); run one by `{"name": "..."}`
  - `POST /api/chat` — `{"text": "...", "speak_on_pc": false, "return_audio": false}` → reply, route source, tool calls, note sources, and optional base64 WAV audio
  - `POST /api/voice` — `{"audio_wav": "<base64>", "speak_on_pc": false, "return_audio": true}` (or raw `audio/wav` body) → Whisper transcript + routed reply + optional base64 WAV audio
  - `GET /api/notes?q=<query>` — top matching Obsidian vault note titles, folders, and excerpts
  - `POST /api/remember` — `{"text": "..."}` → saves a memory fact and markdown capture

## Test

```powershell
python -m unittest -v                          # all unit tests, no model or network needed
python -m tests.eval_commands                  # score qwen3:4b on 35 commands (dry run)
python -m tests.eval_commands --model qwen2.5:3b
python -m tests.eval_commands --llm-only       # model alone, no fast path
python -m tests.eval_commands --mode tools     # native tool calling instead of JSON
```

The evals never open anything. They only report what would have run.

**Eval harness** (local vs cloud, three tiers). T1 and T2 are single commands. T3 is multi-step
`agent:` goals, and a T3 pass needs every listed tool called and a final reply. A turn that errored
or couldn't reach the model always counts as a failure.

```powershell
python -m tests.eval_harness --save                       # smart brain -> data/evals/smart.json
python -m tests.eval_harness --brain gemini --delay 2 --save
python -m tests.eval_harness --tier 3                     # multi-step only
python -m tests.eval_harness --brain mock-gemini          # offline check of the harness itself
python -m tests.eval_harness --compare data/evals/a.json data/evals/b.json   # side-by-side table
```

Only compare runs from the same sitting. GPU and Ollama state drift enough between sessions to
move latency by 100+ ms and flip borderline cases.

### Preflight

```powershell
python -m tools.preflight
```

Runs a live end-to-end check across the whole stack:

1. **Ollama + model** (`qwen3:4b` is pulled and reachable)
2. **Brain, tool choice** (`"open notepad"` routes to `open_app`)
3. **Brain, chat** (short conversational reply — timed as `think`)
4. **Voice** (Kokoro TTS synthesizes the first clause of the test sentence — timed as `speak`)
5. **The loop** (resamples the synthesized speech 24 kHz → 16 kHz, transcribes it with Whisper — timed as `hear` — and verifies fuzzy match ≥ 85%)
6. **Memory round-trip** (`add_fact` → `search` → `delete_fact` on `data/adeola.db`)
7. **Notes** (scans `NOTES_DIRS` and verifies a note can be retrieved by title)
8. **Wake word** (checks whether the custom `"Adeola"` model is installed or still on the `"hey jarvis"` fallback)
9. **Microphone** (checks for an active input device)
10. **Offline** (verifies zero external network connections were made during checks 2–5)

At the end it prints the **latency budget** line:

```text
LATENCY BUDGET: hear 0.42 s · think 1.10 s · speak 0.35 s · total 1.87 s
```

- `hear`: Whisper transcription time for the spoken test sentence
- `think`: Ollama LLM reply time
- `speak`: Kokoro synthesis time to first audio chunk
- `total`: sum of the measured stages (end-to-end voice turn latency)

Rule: **"Done" means preflight passed** (exit code `0`, zero `FAIL` checks).

## How it works

```
"Adeola, open Brave"
   → clean()          strip wake word and politeness → "open brave"
   → special routes   keep/skip answer, notes questions, agent: goals
   → fast path        regex + Start menu lookup → open_app("brave")            ~0 ms
   → (if unsure)      the active brain answers in a fixed JSON shape → open_app("Spotify")
                      local qwen3:4b ~1 s · read-only tools get a second step to phrase the answer
   → Toolbox.run()    only the tools in registry.py can ever execute
   → trace + HUD      every turn is logged to SQLite and pushed to the HUD
```

| File | Job |
|---|---|
| `app/config.py` | Brains, cloud providers, aliases, privacy and server settings; loads `.env` |
| `app/tools/applications.py` | Indexes Start menu apps (incl. Store apps) and fuzzy-matches names |
| `app/tools/registry.py` | The approved tools and their schemas for the model |
| `app/brain/router.py` | Turn handling: special routes, fast path, model turn, traces and HUD events |
| `app/brain/switching.py` | Brain shelf, auto brain, cloud switching, and the "am I local" answers |
| `app/brain/model.py` | Ollama calls: thinking off, temperature 0, model kept loaded, truncation retry |
| `app/brain/remote.py` | Cloud brains over plain HTTP (OpenAI-compatible and Anthropic), with the privacy filter |
| `app/brain/harness.py` | Agent mode: bounded plan → tools → observe → answer loop |
| `app/brain/policy.py` | Auto brain: which local brain a request needs, with a cooldown |
| `app/brain/traces.py` | Trace summaries and friction reports (`trace summary`, `what did you mishear`) |
| `app/memory/store.py` | Local SQLite memory for facts, preferences, counters, activity and traces |
| `app/memory/notes.py` | Read-only markdown index and excerpt search over your Obsidian vault |
| `app/skills/loader.py` | Finds `SKILL.md` skills and saves their output |
| `app/events.py` | In-process event bus that feeds the HUD |
| `app/server.py` | Zero-dependency HTTP server: HUD, phone companion, JSON API, access rules |
| `app/ui/hud.html` | The HUD page (plain HTML/JS/WebGL, no build step, nothing loaded from the internet) |
| `tools/preflight.py` | Live end-to-end check with hear/think/speak latency budget and offline verification |

## Customising

- **App opens the wrong thing or isn't found:** add an alias in `APP_ALIASES` (`config.py`).
- **New tool:** add a method to `Toolbox`, register it in `_funcs`, and add a schema to `SCHEMAS`.
  Anything destructive (deleting files, sending messages) should ask for confirmation first.

## What's next

Open work is tracked in [tasks/todo.md](tasks/todo.md). To train the "Adeola" wake word, see
[docs/wake_word.md](docs/wake_word.md).

# Adeola

A local text assistant for Windows 11 that opens apps and websites, searches the web,
tells the time and locks the PC. Runs entirely on your machine with Ollama.

## Setup (PowerShell)

```powershell
cd $HOME\Projects\adeola          # wherever you unzipped this
py -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -r requirements.txt
```

Ollama must be running with `qwen3:4b` pulled (`ollama pull qwen3:4b`).

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
`[voice started after N ms]` for each reply.

## Memory and projects

Adeola keeps a small memory in `data/adeola.db`, a single file on this PC (never uploaded, ignored
by git). Things you can say or type:

| You say | What happens |
|---|---|
| `remember that my exam is on Friday` | Saves it |
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

## Test

```powershell
python -m unittest -v                          # fast-path logic, no model needed
python -m tests.eval_commands                  # score qwen3:4b on 20 commands (dry run)
python -m tests.eval_commands --model qwen2.5:3b
python -m tests.eval_commands --llm-only       # model alone, no fast path
python -m tests.eval_commands --mode tools     # native tool calling instead of JSON
```

The eval never opens anything, it only reports what would have run.

## How it works

```
"Adeola, open Brave"
   → clean()        strip wake word and politeness → "open brave"
   → fast path      regex + Start menu lookup      → open_app("brave")   ~0 ms
   → (if unsure)    qwen3:4b answers in a fixed JSON shape → open_app("Spotify")  ~1–3 s
   → Toolbox.run()  only the 5 tools in registry.py can ever execute
```

| File | Job |
|---|---|
| `app/config.py` | Model, aliases (`"vs code"` → Visual Studio Code), site shortcuts |
| `app/tools/applications.py` | Indexes Start menu apps (incl. Store apps) and fuzzy-matches names |
| `app/tools/registry.py` | The approved tools and their schemas for the model |
| `app/brain/router.py` | Fast path, then model fallback |
| `app/brain/model.py` | Ollama calls: thinking off, temperature 0, model kept loaded |

## Customising

- **App opens the wrong thing or isn't found:** add an alias in `APP_ALIASES` (`config.py`).
- **New tool:** add a method to `Toolbox`, register it in `_funcs`, and add a schema to `SCHEMAS`.
  Anything destructive (deleting files, sending messages) should ask for confirmation first.

## Next phases

2. Voice: ~~push-to-talk with faster-whisper~~, ~~spoken replies with a blended Kokoro voice~~ (done), ~~wake word~~ (done; train "Adeola" with [docs/wake_word.md](docs/wake_word.md))
3. ~~Memory: SQLite for preferences and projects~~ (done)
4. Android: FastAPI server reachable over Tailscale

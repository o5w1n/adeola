# Adeola — Phase 1

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

2. Voice: push-to-talk hotkey, faster-whisper on CPU, Piper TTS, then a custom "Adeola" wake word (trained with openWakeWord)
3. Memory: SQLite for preferences and projects
4. Android: FastAPI server reachable over Tailscale

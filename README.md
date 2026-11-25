# lca — control your PC and browser with a local LLM

`lca` is a small terminal agent that runs a **local LLM through [Ollama](https://ollama.com)** and lets it
use your computer: run PowerShell commands, read and write files, and drive a **real web browser**
(search, open pages, click, type). No cloud, no API key, nothing leaves your machine.

It is designed for modest hardware. It was built and tested on a **4 GB RTX 3050 laptop GPU with 16 GB RAM**.

```
lca [qwen3:4b-instruct-2507-q4_K_M | medium]> open google.com and search for weather in Tashkent
> browser_goto {"url": "https://www.google.com/search?q=weather+in+Tashkent"}
...
[time] 42.0s total | model 35.8s | tools 6.1s | 2 model call(s)
```

## Features

- **Shell + files**: PowerShell, read / write / list files.
- **Real browser control**: opens its own Chromium window; the model sees the page text plus numbered
  elements and clicks / types by number (`browser_goto`, `browser_read`, `browser_click`, `browser_type`,
  `browser_scroll`). This simple interface works much better with small models than raw JS/CDP agents.
- **Slash commands** (like Claude Code): `/model`, `/effort`, `/cd`, `/confirm`, `/clear`, `/status`, `/help`.
- **`cd` built in**: `cd my-project` finds the folder by name under your common project folders.
- **Timing on every response**: total / model / tools time and number of model calls.
- **Fast by default**: uses a non-thinking model, 4096 context and keeps the model loaded.

## Requirements

- Windows 10/11 (PowerShell 5.1+; `winget` is used by the installer)
- An NVIDIA GPU is recommended (4 GB VRAM works; CPU-only works but is slow). Keep your NVIDIA driver up to date,
  otherwise Ollama may fall back to CPU.
- About 6 GB of free disk space (Ollama, model, Chromium)

## Install

```powershell
git clone https://github.com/safarboyalxorazmiy/local-llm-agent.git
cd local-llm-agent
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer: installs Python and Ollama if missing (via winget), pulls the model, creates a venv, installs
Playwright + Chromium, and adds the folder to your user `PATH`. Open a **new terminal** afterwards.

Optional desktop shortcut with an icon:

```powershell
powershell -ExecutionPolicy Bypass -File .\create-shortcut.ps1
```

## Usage

```
lca                              interactive session
lca "open youtube and search lofi"   one-shot task
lca -m qwen3:8b -e high "task"   choose model / effort
lca --confirm                    ask before every shell command and file write
```

| Command | What it does |
|---|---|
| `cd <path or project name>` | Change folder (also `cd ..`, `cd -`, `cd ~`, `/cd`). Searches common project folders by name. |
| `/model [name]` | List installed Ollama models and switch (`/modal` also works). |
| `/effort low\|medium\|high` | Thinking level (only affects models that support thinking, e.g. `qwen3:4b`). |
| `/confirm [on\|off]` | Ask before shell commands / file writes. |
| `/clear` | Reset the conversation. |
| `/status` | Show model, effort, context size, folder. |

Environment variables: `LCA_MODEL`, `LCA_EFFORT`, `LCA_CTX`, `LCA_HOST`, `LCA_CONFIRM=1`.

## Choosing a model

Any Ollama model with **tool calling** support works (`/model` lists what you have installed).
Only `qwen3:4b-instruct-2507-q4_K_M` (default) and `qwen3:4b` have been tested.

| GPU VRAM | Suggestion (untested unless noted) |
|---|---|
| 4 GB | `qwen3:4b-instruct-2507-q4_K_M` (default, tested) |
| 6–8 GB | `qwen3:8b` |
| 12 GB+ | `qwen3:14b` or larger |

Thinking models (like `qwen3:4b`) are slower: they generate hundreds of reasoning tokens before each action.

## ⚠️ Safety

**By default `lca` runs everything the model decides, with no permission prompts** — the same idea as
`claude --dangerously-skip-permissions`. Small local models make mistakes: a wrong command can delete or
change real files, and web pages can contain text that tries to steer the model (prompt injection).

- Start with `lca --confirm` (or `/confirm on`) if you want to approve each action.
- Don't run it on folders or accounts you can't afford to lose.
- The browser uses its own profile (`browser-profile/`), separate from your normal Chrome, but anything
  you sign in to inside that window is available to the agent.

## Limitations

- Small models misread pages, loop, or answer from memory instead of using a tool. Verify important results.
- Browser steps are fast, but multi-step web tasks may still fail on sites with captchas or complex UIs.
- Windows only for now (PowerShell tool, Windows paths in a few places). Ports are welcome.

## How it works

`lca.py` is a single file (standard library + Playwright). It calls Ollama's `/api/chat` with tool
definitions, executes the tool calls the model returns, feeds results back, and repeats until the model
answers without a tool call. The browser tool starts Chromium with a debug port and connects Playwright to it.

## Contributing

Issues and pull requests are welcome — especially: Linux/macOS support, more tested models, better
browser tools, and tests.

## License

MIT — see [LICENSE](LICENSE).

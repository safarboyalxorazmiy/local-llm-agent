"""lca - local coding/computer agent. Runs on Ollama, no permission prompts.

Usage:
  lca                      interactive session
  lca "do something"       one-shot task, then exit
  lca -m qwen3:4b "task"   pick another Ollama model
"""
import asyncio
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "result")
OLLAMA = os.environ.get("LCA_HOST", "http://localhost:11434")
MODEL = os.environ.get("LCA_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
NUM_CTX = int(os.environ.get("LCA_CTX", "4096"))
MAX_OUT = 4000
# effort level -> (think, max tokens per reply, max agent steps, max browser steps, description)
EFFORTS = {
    "low":    (False, 600,  10, 8,  "no thinking, fastest, least reliable"),
    "medium": (True,  1200, 20, 12, "short thinking, balanced"),
    "high":   (True,  3000, 40, 20, "deep thinking, slowest, most reliable"),
}
CONFIRM = os.environ.get("LCA_CONFIRM", "0") == "1"  # ask before shell commands / file writes
EFFORT = os.environ.get("LCA_EFFORT", "medium")
if EFFORT not in EFFORTS:
    EFFORT = "medium"

SYSTEM = f"""You are lca, an autonomous agent running on the user's Windows 11 PC. \
You have full permission to act; never ask for confirmation, just do the task.
Tools: powershell (run any command), read_file, write_file, list_dir, and a REAL browser: browser_goto(url), \
browser_read, browser_click(index), browser_type(index,text), browser_scroll.
Browser rules: you CAN browse. For any web request, actually use the browser tools; never answer web questions from \
memory and never say you cannot browse. Use ONLY the search terms the user gave - never invent them; if the query is \
missing, ask. Workflow: browser_goto(url) shows the page text and numbered elements; then browser_type / browser_click \
using those numbers; read the resulting page text and answer from it. To google something you may open \
https://www.google.com/search?q=<terms> directly. Use powershell for apps, files, system settings, installs. Keep tool calls minimal and precise. When done, reply with a short final answer \
and no tool call. Current directory: {{cwd}}."""

TOOLS = [
    {"type": "function", "function": {
        "name": "powershell", "description": "Run a PowerShell command on the PC and return its output.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Write (overwrite) a text file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "list_dir", "description": "List a directory.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "browser_goto", "description": "Open a URL in the real browser. Returns the page text and a numbered "
        "list of clickable/input elements. To search Google use https://www.google.com/search?q=<terms>.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "browser_read", "description": "Re-read the current browser page (text + numbered elements).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "browser_click", "description": "Click element number N from the last browser result.",
        "parameters": {"type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]}}},
    {"type": "function", "function": {
        "name": "browser_type", "description": "Type text into input element number N and press Enter (submit=true).",
        "parameters": {"type": "object", "properties": {"index": {"type": "integer"}, "text": {"type": "string"},
                                                        "submit": {"type": "boolean"}}, "required": ["index", "text"]}}},
    {"type": "function", "function": {
        "name": "browser_scroll", "description": "Scroll the browser page up or down.",
        "parameters": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["up", "down"]}}}}},
]


def clip(s: str) -> str:
    s = s if isinstance(s, str) else str(s)
    return s if len(s) <= MAX_OUT else s[:MAX_OUT // 2] + "\n...[truncated]...\n" + s[-MAX_OUT // 2:]


def t_powershell(command):
    try:
        r = subprocess.run(["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                           capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
        return clip((r.stdout + r.stderr).strip() or f"(no output, exit {r.returncode})")
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 300s"
    except FileNotFoundError:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True,
                           timeout=300, errors="replace")
        return clip((r.stdout + r.stderr).strip() or f"(no output, exit {r.returncode})")


def t_read_file(path):
    return clip(Path(path).expanduser().read_text(encoding="utf-8", errors="replace"))


def t_write_file(path, content):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {p}"


def t_list_dir(path):
    return clip("\n".join(sorted(x.name + ("/" if x.is_dir() else "") for x in Path(path).expanduser().iterdir())))


_browser = None
MESSAGES = []


def kill_stale_chromium():
    """Kill Playwright-Chromium processes (never the user's own Chrome)."""
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-Process chrome -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*ms-playwright*' } "
                    "| Stop-Process -Force"], capture_output=True)


_pw = {"p": None, "page": None}

_ELEMS_JS = """() => {
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[role=textbox],[onclick]';
  document.querySelectorAll('[data-lca]').forEach(e => e.removeAttribute('data-lca'));
  const out = []; let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (r.bottom < 0 || r.top > innerHeight * 2.5) continue;
    el.setAttribute('data-lca', i);
    const label = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.title || el.name || '')
        .trim().replace(/\\s+/g, ' ').slice(0, 60);
    out.push('[' + i + '] <' + el.tagName.toLowerCase() + (el.type ? ' type=' + el.type : '') + '> ' + label
        + (el.href ? ' -> ' + el.href.slice(0, 90) : ''));
    if (++i >= 40) break;
  }
  return out;
}"""


def _page():
    """Connect Playwright to our own Chromium (started on a debug port) and return the active page."""
    if _pw["page"] is not None and not _pw["page"].is_closed():
        return _pw["page"]
    import glob
    import time
    from playwright.sync_api import sync_playwright
    port = 9333
    cdp = f"http://127.0.0.1:{port}"

    def up():
        try:
            urllib.request.urlopen(f"{cdp}/json/version", timeout=2)
            return True
        except Exception:  # noqa: BLE001
            return False

    if not up():
        exes = glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-*\chrome-win*\chrome.exe"))
        if not exes:
            raise RuntimeError("Playwright Chromium not found; run: python -m playwright install chromium")
        prof = str(Path(__file__).resolve().parent / "browser-profile")
        subprocess.Popen([exes[-1], f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                          "--no-first-run", "--no-default-browser-check", "--start-maximized", "about:blank"])
        for _ in range(30):
            if up():
                break
            time.sleep(1)
    if _pw["p"] is None:
        _pw["p"] = sync_playwright().start()
    browser = _pw["p"].chromium.connect_over_cdp(cdp)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(8000)
    _pw["page"] = page
    return page


def _snapshot(page, note=""):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
        page.wait_for_timeout(700)
    except Exception:  # noqa: BLE001
        pass
    try:
        text = page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        text = ""
    text = " ".join(text.split())[:1800]
    try:
        elems = page.evaluate(_ELEMS_JS)
    except Exception as e:  # noqa: BLE001
        elems = [f"(could not list elements: {e})"]
    return clip(f"{note}\nURL: {page.url}\nTITLE: {page.title()}\nPAGE TEXT: {text}\n"
                f"CLICKABLE/INPUT ELEMENTS:\n" + "\n".join(elems))


def t_browser_goto(url):
    p = _page()
    if not url.startswith(("http://", "https://", "about:", "file:")):
        url = "https://" + url
    p.goto(url, wait_until="domcontentloaded", timeout=30000)
    return _snapshot(p)


def t_browser_read():
    return _snapshot(_page())


def t_browser_click(index):
    p = _page()
    p.locator(f'[data-lca="{int(index)}"]').first.click(timeout=6000)
    return _snapshot(p, f"clicked [{index}]")


def t_browser_type(index, text, submit=True):
    p = _page()
    loc = p.locator(f'[data-lca="{int(index)}"]').first
    loc.click(timeout=6000)
    loc.fill(str(text), timeout=6000)
    if submit in (True, "true", "True", 1, "1"):
        loc.press("Enter")
    return _snapshot(p, f"typed into [{index}]" + (" and pressed Enter" if submit else ""))


def t_browser_scroll(direction="down"):
    p = _page()
    p.mouse.wheel(0, -900 if str(direction).lower() == "up" else 900)
    return _snapshot(p, f"scrolled {direction}")


IMPL = {"powershell": t_powershell, "read_file": t_read_file, "write_file": t_write_file,
        "list_dir": t_list_dir, "browser_goto": t_browser_goto, "browser_read": t_browser_read,
        "browser_click": t_browser_click, "browser_type": t_browser_type, "browser_scroll": t_browser_scroll}


def chat(messages):
    think, num_predict = EFFORTS[EFFORT][0], EFFORTS[EFFORT][1]
    body = {"model": MODEL, "messages": messages, "tools": TOOLS, "stream": False, "keep_alive": "30m",
            "options": {"num_ctx": NUM_CTX, "temperature": 0.2, "num_predict": num_predict}}
    if "instruct" not in MODEL:  # instruct models have no thinking mode
        body["think"] = think

    def post(b):
        req = urllib.request.Request(f"{OLLAMA}/api/chat", json.dumps(b).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.loads(r.read())["message"]

    try:
        msg = post(body)
    except urllib.error.HTTPError as e:
        if e.code == 400 and "think" in body:  # model doesn't support thinking
            body.pop("think")
            msg = post(body)
        else:
            raise
    # some models leak reasoning into content when thinking is off; drop it
    if "</think>" in msg.get("content", ""):
        msg["content"] = msg["content"].split("</think>", 1)[1].strip()
    return msg


def chat_with_spinner(messages):
    import threading
    import time
    stop = threading.Event()
    t0 = time.time()

    def spin():
        frames = "|/-\\"
        i = 0
        while not stop.wait(0.2):
            sys.stdout.write(f"\r\033[90m{frames[i % 4]} thinking... {int(time.time() - t0)}s\033[0m ")
            sys.stdout.flush()
            i += 1

    th = threading.Thread(target=spin, daemon=True)
    th.start()
    try:
        return chat(messages)
    finally:
        STATS["model"] += time.time() - t0
        STATS["steps"] += 1
        stop.set()
        th.join()
        sys.stdout.write("\r" + " " * 40 + "\r")


def installed_models():
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as r:
        return [m["name"] for m in json.loads(r.read()).get("models", [])]


def pick(title, options, current):
    """Numbered picker. options: list of (value, description). Returns chosen value or None."""
    print(f"\n{title}")
    for i, (val, desc) in enumerate(options, 1):
        mark = "*" if val == current else " "
        print(f" {mark} {i}. {val}" + (f"  - {desc}" if desc else ""))
    try:
        ans = input("Select number or name (Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not ans:
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(options):
        return options[int(ans) - 1][0]
    return ans if ans in [o[0] for o in options] else None


PREV_DIR = [None]
PROJECT_ROOTS = ["~", "~/Desktop", "~/Documents", "~/source", "~/source/repos", "~/repos", "~/projects",
                 "~/dev", "~/code", "~/work", "~/Downloads", "C:/", "D:/"]


def find_project(name):
    """Find a folder by (partial) name under common project roots. Returns list of matches."""
    name = name.lower().strip("\\/")
    exact, partial = [], []
    for root in PROJECT_ROOTS:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        try:
            for d in base.iterdir():
                if d.is_dir() and not d.name.startswith("."):
                    if d.name.lower() == name:
                        exact.append(d)
                    elif name in d.name.lower():
                        partial.append(d)
        except (PermissionError, OSError):
            continue
    seen, out = set(), []
    for d in exact + partial:
        if str(d).lower() not in seen:
            seen.add(str(d).lower())
            out.append(d)
    return out


def do_cd(arg):
    """Built-in cd. Supports ~, -, quotes, env vars, relative paths, drive switch and project-name search."""
    arg = arg.strip()
    if arg.lower().startswith("/d "):
        arg = arg[3:].strip()
    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
        arg = arg[1:-1]
    cur = os.getcwd()
    if not arg or arg == "~":
        target = Path.home()
    elif arg == "-":
        if not PREV_DIR[0]:
            print("No previous directory.")
            return
        target = Path(PREV_DIR[0])
    else:
        p = Path(os.path.expandvars(arg)).expanduser()
        if not p.is_absolute() and len(arg) == 2 and arg[1] == ":":
            p = Path(arg + "\\")
        target = p if p.is_absolute() else Path(cur) / p
        if not target.is_dir():
            matches = [] if any(c in arg for c in "\\/") and p.is_absolute() else find_project(arg)
            if not matches:
                print(f"cd: no such directory or project: {arg}")
                return
            if len(matches) == 1:
                target = matches[0]
            else:
                choice = pick(f"Multiple matches for '{arg}'", [(str(m), "") for m in matches[:9]], "")
                if not choice:
                    return
                target = Path(choice)
    try:
        target = target.resolve()
        os.chdir(target)
    except OSError as e:
        print(f"cd: {e}")
        return
    PREV_DIR[0] = cur
    if MESSAGES and MESSAGES[0].get("role") == "system":
        base = MESSAGES[0]["content"]
        MESSAGES[0]["content"] = base.rsplit("Current directory:", 1)[0] + f"Current directory: {target}."
    markers = [m for m in ("package.json", "pyproject.toml", "requirements.txt", ".git", "Cargo.toml", "go.mod",
                           "pom.xml", "README.md") if (target / m).exists()]
    print(f"-> {target}" + (f"   [{', '.join(markers)}]" if markers else ""))


def slash(cmd):
    """Handle a /command. Returns True if handled."""
    global MODEL, EFFORT, CONFIRM
    parts = cmd.split(None, 1)
    name, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
    if name in ("/model", "/modal"):
        try:
            models = installed_models()
        except Exception as e:  # noqa: BLE001
            print(f"Could not list models: {e}")
            return True
        if arg:
            choice = arg if arg in models else next((m for m in models if m.startswith(arg)), None)
            if not choice:
                print(f"Model '{arg}' is not installed. Installed: {', '.join(models)}\n"
                      f"Install with: ollama pull {arg}")
                return True
        else:
            choice = pick("Select model", [(m, "") for m in models], MODEL)
        if choice:
            MODEL = choice
            print(f"Model set to {MODEL}")
    elif name == "/effort":
        if arg in EFFORTS:
            choice = arg
        else:
            choice = pick("Select effort", [(k, v[4]) for k, v in EFFORTS.items()], EFFORT)
        if choice in EFFORTS:
            EFFORT = choice
            print(f"Effort set to {EFFORT}")
        elif choice:
            print("Effort must be: " + ", ".join(EFFORTS))
    elif name == "/confirm":
        CONFIRM = arg.lower() != "off"
        print("Confirmation " + ("ON: asks before shell commands and file writes" if CONFIRM
                                 else "OFF: runs everything without asking"))
    elif name == "/cd":
        do_cd(arg)
    elif name == "/clear":
        MESSAGES[:] = MESSAGES[:1]
        print("Conversation cleared.")
    elif name == "/status":
        print(f"model={MODEL}  effort={EFFORT}  ctx={NUM_CTX}  cwd={os.getcwd()}")
    elif name in ("/help", "/?"):
        print("cd <path|project-name>   change folder (also /cd, cd .., cd -, cd ~)\n"
              "/model [name]   switch model (alias: /modal)\n/effort [low|medium|high]   thinking level\n"
              "/confirm [on|off]   ask before shell commands / file writes\n"
              "/clear   reset conversation\n/status   show settings\n/exit   quit")
    elif name in ("/exit", "/quit"):
        raise SystemExit
    else:
        print(f"Unknown command {name}. Try /help")
    return True


STATS = {"model": 0.0, "tools": 0.0, "steps": 0}


def run_task(messages, task):
    """Run a task and print how long the whole response took."""
    import time
    STATS.update(model=0.0, tools=0.0, steps=0)
    t0 = time.time()
    try:
        _run_task(messages, task)
    finally:
        total = time.time() - t0
        print(f"\033[33m[time] {total:.1f}s total | model {STATS['model']:.1f}s | "
              f"tools {STATS['tools']:.1f}s | {STATS['steps']} model call(s)\033[0m")


def _run_task(messages, task):
    messages.append({"role": "user", "content": task})
    for _ in range(EFFORTS[EFFORT][2]):
        try:
            msg = chat_with_spinner(messages)
        except Exception as e:  # noqa: BLE001
            body = ""
            if hasattr(e, "read"):
                try:
                    body = e.read().decode(errors="replace")[:500]
                except Exception:  # noqa: BLE001
                    pass
            print(f"\n\033[31mModel error: {e} {body}\033[0m\n(try /clear, a lower /effort, or retry)\n")
            if messages and messages[-1].get("role") == "user":
                messages.pop()
            return
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            print(f"\n{msg.get('content', '').strip()}\n")
            return
        if msg.get("content", "").strip():
            print(msg["content"].strip())
        for c in calls:
            fn, args = c["function"]["name"], c["function"].get("arguments") or {}
            print(f"\033[36m> {fn}\033[0m {json.dumps(args, ensure_ascii=False)[:300]}")
            t_tool = __import__("time").time()
            try:
                if CONFIRM and fn in ("powershell", "write_file") and \
                        input("  allow this action? [y/N] ").strip().lower() != "y":
                    out = "ERROR: the user declined this action"
                elif fn in IMPL:
                    import inspect
                    ok = inspect.signature(IMPL[fn]).parameters
                    out = IMPL[fn](**{k: v for k, v in args.items() if k in ok})
                else:
                    out = f"ERROR: unknown tool {fn}"
            except Exception as e:  # noqa: BLE001
                out = f"ERROR: {e}"
            STATS["tools"] += __import__("time").time() - t_tool
            print(f"\033[90m{out[:500]}\033[0m")
            messages.append({"role": "tool", "tool_name": fn, "content": out})
    print("\n(stopped: step limit reached)\n")


def main():
    global MODEL, EFFORT, CONFIRM
    args = sys.argv[1:]
    if args and args[0] == "--confirm":
        CONFIRM, args = True, args[1:]
    while len(args) >= 2 and args[0] in ("-m", "--model", "-e", "--effort"):
        if args[0] in ("-m", "--model"):
            MODEL = args[1]
        elif args[1] in EFFORTS:
            EFFORT = args[1]
        args = args[2:]
    try:
        urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5)
    except Exception:  # noqa: BLE001
        exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        print("Starting Ollama...")
        subprocess.Popen([str(exe), "serve"], creationflags=0x08000000)
        import time
        time.sleep(6)
    import atexit
    kill_stale_chromium()
    atexit.register(kill_stale_chromium)
    messages = MESSAGES
    messages.append({"role": "system", "content": SYSTEM.replace("{cwd}", os.getcwd())})
    if args:
        run_task(messages, " ".join(args))
        return
    print(f"lca  model={MODEL}  effort={EFFORT}  (no permission prompts - full PC access)\n"
          "Type /help for commands, /model and /effort to change settings.")
    while True:
        try:
            folder = Path.cwd().name or str(Path.cwd())
            task = input(f"\n\033[35m{folder}\033[0m \033[32mlca [{MODEL} | {EFFORT}]>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if task.lower() in ("exit", "quit"):
            break
        low = task.lower()
        if low == "cd" or low.startswith(("cd ", "cd\\", "cd/", "cd..", "chdir ")):
            do_cd(task.split(None, 1)[1] if " " in task else ("" if low == "cd" else task[2:]))
            continue
        if task.startswith("/"):
            try:
                slash(task)
            except SystemExit:
                break
            continue
        if task:
            run_task(messages, task)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        input("\nlca crashed - press Enter to close...")

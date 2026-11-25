# Quick install (Windows)

1. Open PowerShell and run:

```powershell
git clone https://github.com/safarboyalxorazmiy/local-llm-agent.git
cd local-llm-agent
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

2. Open a **new** terminal and run:

```powershell
lca
```

That's it. The installer sets up Python, Ollama, the model (~2.5 GB) and the browser for you.

- Safer mode (asks before each action): `lca --confirm`
- Update your NVIDIA driver, or it will run slowly on CPU.

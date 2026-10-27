# Jarvis

Local-first desktop assistant: a floating bar at the top of the screen. Click the orb and talk,
or type. It answers on a card and out loud.

- **Fast path (local, free):** Ollama + `qwen3:4b-instruct` picks a tool: open apps/sites, YouTube/Google search,
  write a Word document, draft an email (never sends), remember facts. Remembers sites you opened.
- **Screen path (Azure credits):** for things only clicking can do, an Azure vision model looks at the
  screen and clicks/types. Slam the mouse into a screen corner to abort.

## Setup on a new PC (Windows 10/11)

1. Install **Python 3.12+** from python.org (tick "Add python.exe to PATH") and **Git**.
2. Install **Ollama**: https://ollama.com/download, or `winget install Ollama.Ollama`.
   Start it once so it runs in the tray.
3. Get the code and install dependencies:
   ```
   git clone <your repo URL> jarvis
   cd jarvis
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
4. Download the model (~2.5 GB):
   ```
   ollama pull qwen3:4b-instruct
   ```
5. Optional: `copy .env.example .env` and fill in the Azure keys for screen control.
6. Check and run:
   ```
   .venv\Scripts\python.exe test_brain.py
   .venv\Scripts\pythonw.exe jarvis.py
   ```

The first command after startup takes ~15s while the model loads into the GPU; after that ~1s.
Without an NVIDIA GPU (4 GB+ VRAM) it runs on the CPU and answers take 10-30s.

## Files
- `jarvis.py`: window, voice in, speech out
- `ui.html`: the bar's design
- `brain.py`: model calls, tools, memory, screen agent
- `memory.json`: chat history, opened sites, facts (created on first use, not in git)
- Word docs go to `Documents\Jarvis`

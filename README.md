![text-transform-service — turn text into predictable JSON, locally](docs/assets/banner.png)

# text-transform-service

**Messy text in → tidy, predictable data out — using an AI model that runs entirely on your own computer.
No cloud, no accounts, nothing leaves your machine.**

> 🧑‍💻 **Want to build on it?** → [Developer guide](docs/for-developers.md)
> 🤖 **You're an AI agent?** → [Dense reference](docs/ai-reference.md)
>
> Everyone else — read on. This page is written to be understood without any special background.

---

## What is this?

It's a small tool that takes ordinary text — a news article, a page from a book — and hands it back to you
as **neat, structured data** (a format called JSON) that a program can rely on.

The clever part: it does this using an **AI language model that runs on your own machine**. You are not
sending your text to a company's servers, you don't need an account or an API key, and it keeps working
even with the internet unplugged.

It's a focused building block, not a chat app. It was built to power two larger projects — a news site
(*Brickfeed*) and an illustrated-book maker (*Scriptorium*) — so it does a handful of specific jobs
extremely reliably rather than trying to do everything.

## What does it do?

You give it some text and the name of a **transform** — a specific job you want done. It gives you back a
clean, predictable result. For example, the *image-prompt* transform turns a news story into a short
description you could hand to an image generator:

> **In:**  “MERIDAN — A magnitude 6.4 earthquake toppled the town's clock tower at dawn…”
>
> **Out:**  `{ "prompt": "A fallen brick clock tower lies shattered on a cold town square at dawn" }`

Other transforms read a book page and list the characters mentioned, or track where and when each scene
takes place. Each one always answers in the **same shape**, every time — so whatever you build on top of
it never has to guess. (The full list lives in the [developer guide](docs/for-developers.md).)

## Why would I use it?

- **It's private.** The AI runs on your computer. Your text isn't uploaded anywhere.
- **It's predictable.** Every answer comes back in a fixed, reliable shape — not a rambling paragraph.
- **There's nothing to pay for.** No subscriptions, no per-request fees, no API keys to manage.
- **It's yours.** It's a small, open codebase you can read, run, and change however you like.

## Set it up

You'll install two free tools, download an AI model, and run one command. Pick your operating system below.
It's the same three ingredients everywhere:

1. **[Ollama](https://ollama.com/download)** — runs the AI model on your machine.
2. **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — sets up and runs this project.
3. **A model** — start with the small one (`qwen3.5:2b`) so it works on an ordinary laptop.

> 💡 The small model is perfect for trying it out. The project's "real" jobs use a bigger model
> (`qwen3.5:9b`) that's sharper but needs a proper graphics card (GPU). Start small; you can switch later.

### Windows

1. Install **Ollama** from [ollama.com/download](https://ollama.com/download) and open it (it runs quietly
   in the background).
2. Install **uv** — open **PowerShell** and paste:
   ```powershell
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
3. Download the model — in the same window:
   ```powershell
   ollama pull qwen3.5:2b
   ```
4. Download this project and start it:
   ```powershell
   git clone https://github.com/kbennett2000/text-transform-service.git
   cd text-transform-service
   uv sync
   uv run uvicorn tts.app:app --host 0.0.0.0 --port 8712 --reload
   ```
5. Check it's alive — open <http://localhost:8712/health> in your browser. You should see `"status": "ok"`.

### Mac

1. Install **Ollama** from [ollama.com/download](https://ollama.com/download) (or `brew install ollama`)
   and launch it.
2. Install **uv** — open **Terminal** and paste:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. Download the model:
   ```bash
   ollama pull qwen3.5:2b
   ```
4. Download this project and start it:
   ```bash
   git clone https://github.com/kbennett2000/text-transform-service.git
   cd text-transform-service
   uv sync
   make dev
   ```
5. Check it's alive — open <http://localhost:8712/health>. You should see `"status": "ok"`.

### Linux

1. Install **Ollama**:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```
2. Install **uv**:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. Download the model:
   ```bash
   ollama pull qwen3.5:2b
   ```
4. Download this project and start it:
   ```bash
   git clone https://github.com/kbennett2000/text-transform-service.git
   cd text-transform-service
   uv sync
   make dev
   ```
5. Check it's alive:
   ```bash
   curl -s localhost:8712/health
   ```
   You should see `"status": "ok"`.

### Now try it

With the service running, send it a bit of text (the small `qwen3.5:2b` model powers the demo `echo`
transform):

```bash
curl -s localhost:8712/v1/transform/echo \
  -H 'content-type: application/json' \
  -d '{"text": "First sentence. Second sentence."}'
```

That's the whole idea: text in, predictable data out. To do the real jobs (news image prompts, book
character extraction, and so on), see the guide below.

## Go deeper

- 🧑‍💻 **[Developer guide](docs/for-developers.md)** — every transform, the full API, configuration,
  authentication, and how to add your own transform.
- 🤖 **[AI reference](docs/ai-reference.md)** — the whole service at maximum density, for agents.
- 📐 **[Design doc](text-transform-service-DESIGN.md)** and **[build plan](text-transform-service-BUILD-PLAN.md)** — how and why it was built.
- 🧩 **[Architecture decisions](docs/adr/)** and **[model notes](docs/models.md)** — the reasoning behind the choices.
- 🚀 **[Deployment](deploy/README.md)** — running it as an always-on service under systemd.

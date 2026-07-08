# Plutarch

A local, private, AI-assisted note-taking app. Notes live in a SQLite
database on your machine. A small on-device model (via
[Ollama](https://ollama.com)) helps you find a note you wrote earlier by
searching over title, tags, description, and full text.

Ask the assistant something like *"what was the prompt I wrote about the
drone-swarm coordinator last week?"* and Plutarch returns the top matches
as clickable cards with a one-sentence reason each. Nothing leaves your
machine.

---

## Features

- **Local-only.** No API keys, no cloud calls. Ollama runs on your box.
- **Rich-text editor** powered by TipTap. Manual save + auto-save on note
  switch and on sleep.
- **Chat sidebar** that streams tokens from the on-device model, calls
  tools mid-stream, and re-injects their output — pattern adapted from
  [`FlipperHydra/simple-agent`](https://github.com/FlipperHydra/simple-agent).
- **Deterministic scoring** for note matches (BM25 + tag overlap + title
  Jaccard + description Jaccard + recency decay), normalised to `[0, 1]`.
- **Sleep state.** Press the Sleep button, the model unloads from VRAM,
  DB pool closes, chat log is deleted. Health check keeps listening on
  the same port so the welcome screen can wake the app back up.
- **Session-end tagging.** On sleep, any un-tagged notes are tagged and
  given &le; 3-sentence descriptions using a fixed vocabulary, then the
  session ends.
- **Model manager.** Recommended models are one click to pull. Manual
  entry supports any Ollama tag. VRAM heuristics warn before loading a
  model that won't fit.
- **PDF export** via `html2pdf.js` (falls back to a print dialog).
- **SQLite FTS5** full-text index kept in sync with triggers.

---

## Screenshots

_Coming soon._

---

## Requirements

- **Python 3.12+** (works on 3.14 too; only `tiktoken` needed the pin bump)
- **[Ollama](https://ollama.com/download)** running locally on port 11434
- At least one small model pulled, for example:
  ```bash
  ollama pull gemma3:270m
  ```

Recommended models, all &le; 4B parameters:

| Tag              | Approx VRAM (16k ctx) |
| ---------------- | --------------------- |
| `gemma3:270m`    | 0.7 GB                |
| `qwen2.5:0.5b`   | 1.0 GB                |
| `llama3.2:1b`    | 1.6 GB                |
| `gemma3:1b`      | 1.7 GB                |
| `phi3:mini`      | 4.0 GB                |
| `gemma3:4b`      | 4.5 GB                |

Any other Ollama model works too — add it from the manual entry dialog.

---

## Quick start (without Docker)

```bash
git clone https://github.com/FlipperHydra/Plutarch.git
cd Plutarch

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

# (Optional) drop the TipTap + html2pdf bundles into frontend/vendor/.
# See frontend/vendor/README.md for the exact files. Without them the
# editor falls back to a plain contenteditable box and PDF export falls
# back to the browser print dialog.

# In a separate terminal, make sure Ollama is running:
#   ollama serve
#   ollama pull gemma3:270m

cd backend
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000).

---

## Docker

Plutarch ships with a `Dockerfile` and a `docker-compose.yml`. The image
runs the FastAPI backend and serves the static frontend from the same
container. Ollama is expected to be running on your **host**, not inside
the container.

### Prerequisites

- Docker Engine 24+ (or Docker Desktop)
- Docker Compose v2 (`docker compose` command, not `docker-compose`)
- Ollama running on the host at `localhost:11434`

### Build and run

```bash
git clone https://github.com/FlipperHydra/Plutarch.git
cd Plutarch
docker compose up --build
```

The first build takes a couple of minutes because it installs the
Python dependencies. Subsequent runs are fast because layers are cached.

Open [http://localhost:8000](http://localhost:8000).

### How the container talks to your host's Ollama

The compose file sets:

```yaml
environment:
  OLLAMA_HOST: http://host.docker.internal:11434
extra_hosts:
  - "host.docker.internal:host-gateway"
```

That maps `host.docker.internal` to the host on both Docker Desktop
(Mac/Windows) and modern Linux Docker Engine. If you are on an older
Linux Docker install where `host-gateway` is not supported, uncomment
the `network_mode: host` line in `docker-compose.yml` and remove the
`extra_hosts` block. That gives the container direct access to
`localhost:11434`.

### Persistent data

The compose file mounts a named Docker volume at `/app/data` inside the
container:

```yaml
volumes:
  - plutarch_data:/app/data
```

This holds:

- `plutarch.db` — your notes, tags, and settings
- `chat_session.json` — the active session's chat log (deleted on sleep)

To reset the app back to a clean state:

```bash
docker compose down -v      # -v also removes the plutarch_data volume
```

To keep your data but rebuild the image:

```bash
docker compose down
docker compose up --build
```

### Common commands

```bash
docker compose logs -f              # tail the backend log
docker compose exec plutarch bash   # shell inside the container
docker compose restart plutarch     # restart the app
docker compose down                 # stop and remove containers
```

### Environment variables

Override any of these under `environment:` in `docker-compose.yml`:

| Variable                       | Default              | Purpose                              |
| ------------------------------ | -------------------- | ------------------------------------ |
| `PLUTARCH_DATA_DIR`            | `/app/data`          | Where SQLite + chat log live         |
| `OLLAMA_HOST`                  | inherited from env   | URL of the Ollama daemon             |
| `PLUTARCH_NUM_CTX`             | `16384`              | Ollama context window (tokens)       |
| `PLUTARCH_COMPACT_THRESHOLD`   | `2000`               | Soft-threshold for chat compaction   |
| `PLUTARCH_COMPACT_REPROMPT_DELTA` | `250`             | Tokens between compaction attempts   |
| `PLUTARCH_SMALL_NOTE_TOKENS`   | `1500`               | Below this, tag directly; above, chunk-summarize first |

---

## How a session works

1. Open the site. You land on the welcome screen.
2. Press **Start app**. The FastAPI backend enters `active` state.
3. Take notes in the middle column. Left sidebar = notes ordered by
   modified time (Today / Yesterday / This week / Older). Right sidebar
   = your chat with the assistant.
4. Pick a model from the bottom-left model pill. On first launch nothing
   loads automatically — check *Use as default* next to a model to
   enable autoload on future starts.
5. Ask the assistant a retrieval question. It calls `query_notes` first,
   prunes to at most 10 candidates, calls `score_candidate` on each, and
   surfaces the top 3 with reasons and open-note buttons. Turn on
   **Show tool calls** to see the underlying XML tool blocks, the tool
   results, the model's thinking, and the numeric scores.
6. Press **Sleep** when you're done. If a default model is set, tagging
   runs in the background before the model unloads. If not, Plutarch
   asks you: use the current model this one time, set it as default, or
   skip tagging and defer until later.

---

## Architecture

```
plutarch/
├── backend/                 FastAPI app
│   ├── main.py              Route registration + static mount
│   ├── config.py            Env, paths, atomic writes
│   ├── db.py                aiosqlite pool + schema + FTS5 triggers + seed data
│   ├── state.py             cold / waking / active / sleeping-* state machine
│   ├── ollama_client.py     Async wrapper: list, pull, load, unload, chat_stream
│   ├── vram.py              Heuristic VRAM table + nvidia-smi + psutil probes
│   ├── tagging.py           Session-end tagging + description pass + pending-tag governance
│   ├── routes/
│   │   ├── lifecycle.py     /wake  /sleep  /status
│   │   ├── notes.py         CRUD + FTS5-backed /notes/search
│   │   ├── chat.py          /chat/stream (SSE)
│   │   ├── models.py        /models list / pull / select / default / manual-add / vram
│   │   ├── tags.py          Vocabulary + pending-tag review
│   │   └── settings.py      Key/value settings
│   └── agent/               Streaming chat + tool loop
│       ├── loop.py          Multi-round chat orchestration (up to 4 tool rounds)
│       ├── tool_processor.py    Streaming XML parser + async dispatcher
│       ├── tool_registry.py     Dynamic tool registration
│       ├── tools.py         query_notes, score_candidate, propose_top3, list_tags, get_datetime
│       ├── prompts.py       System + tagging + description + reduce + top3 + compaction
│       ├── compaction.py    Token-budgeted summarization of older turns
│       └── conversation.py  Session-scoped chat log (deleted on sleep)
└── frontend/                Vanilla HTML/CSS/JS + TipTap
    ├── index.html           Welcome screen
    ├── app.html             Main workspace (3-column layout)
    ├── css/                 theme, layout, editor, modals
    ├── js/                  api, lifecycle, editor, history, models, chat, main
    └── vendor/README.md     TipTap + html2pdf drop-in instructions
```

### Confidence scoring formula

```
score = 0.35 * normalize(bm25)
      + 0.25 * (matched_tags / max(1, |query_tokens|))
      + 0.20 * jaccard(query_tokens, title_tokens)
      + 0.10 * jaccard(query_tokens, description_tokens)
      + 0.10 * exp(-days_since_modified / 30)
```

All terms are in `[0, 1]`. The `propose_top3` prompt tells the model to
stay silent if every score is below `0.30` rather than surface weak
matches.

---

## Tag vocabulary

Plutarch ships with 14 seed tags. Each has a prompt line shown to the
model at tagging time:

`creative`, `academic`, `personal`, `draft`, `idea`, `technology`,
`philosophy`, `fantasy`, `politics`, `education`, `management`,
`theology`, `todo`, `schedule`.

The model may propose brand-new tags when nothing fits. New proposals
land in a `pending_tags` table and never enter the active vocabulary
automatically — you review them from the tag editor (accept as-is,
merge into an existing tag, or reject).

---

## Data lives in

- SQLite: `./data/plutarch.db` (local) or the `plutarch_data` volume (Docker)
- Session chat: `./data/chat_session.json` — deleted on sleep

Delete `./data/` (or `docker compose down -v`) to reset everything.

---

## Third-party notes

- [`FlipperHydra/simple-agent`](https://github.com/FlipperHydra/simple-agent)
  is MIT-licensed. The XML streaming tool processor, tool registry
  pattern, and context-compaction approach are adapted from it.
- [`RyanRiffle/Poe`](https://github.com/RyanRiffle/Poe) (GPL-3.0) was
  used as a UX reference for the paginated word-processor look. No Poe
  code is included in this project.
- [TipTap](https://tiptap.dev) (MIT), [html2pdf.js](https://ekoopmans.github.io/html2pdf.js/)
  (MIT), [Ollama Python client](https://github.com/ollama/ollama-python) (MIT).

---

## License

MIT. See [`LICENSE`](./LICENSE).

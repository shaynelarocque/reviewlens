# ReviewLens AI

A review intelligence portal that ingests customer reviews and lets analysts explore them through a guardrailed conversational interface.

## Architecture

```
CSV Upload / URL Scrape
        │
        ▼
   Parse & Index ──→ ChromaDB (in-process vector store)
        │
        ▼
  Ingestion Summary ──→ Chat Interface
                            │
                      User Message
                            │
                            ▼
                    Claude Agent SDK
                    (per-message loop)
                            │
                    ┌───────┼───────┐
                    │       │       │
              search_reviews │  generate_chart
                    analyze_sentiment
                    calculate_stats
                    suggest_follow_ups
                            │
                            ▼
                   SSE Stream → Chat UI
                   (text + charts + follow-ups)
```

**Stack:** FastAPI, Jinja2/HTMX, SSE streaming, Claude Agent SDK with custom MCP tools, ChromaDB, Chart.js.

## Key Design Decisions

### Scope Guard (Three-Layer Defense)
The AI only answers questions about the ingested reviews:
1. **System prompt** — explicit instructions to refuse out-of-scope questions
2. **Retrieval confidence** — if ChromaDB returns nothing relevant, auto-reject
3. **Architectural boundary** — the agent has no tools that access external data

### CSV-First Ingestion
CSV upload is the polished primary path. URL scraping via Firecrawl is best-effort secondary — review platforms are heavily anti-bot and the results are unpredictable.

### Conversational Agent Loop
Unlike a fire-and-forget agent, ReviewLens invokes the agent per-message. Each user message triggers a fresh agent loop with conversation history as context.

### Chart.js Inline Charts
The `generate_chart` tool returns Chart.js config JSON. The frontend renders charts inline in chat messages. The agent decides when a chart adds value.

## Running Locally

### Prerequisites
- Python 3.12+
- An [Anthropic API key](https://console.anthropic.com/)

### Setup

```bash
# Clone and enter the project
git clone <repo-url>
cd reviewlens

# Create virtual environment
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
```

Open `.env` and set your API key:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### Run

```bash
uvicorn app.main:app --reload --reload-exclude '.venv'
```

Open [http://localhost:8000](http://localhost:8000)

### Test It

1. Go to `http://localhost:8000`
2. Enter a product name and upload a CSV (see CSV Format below)
3. You'll be redirected to the chat interface with an ingestion summary
4. Ask questions about the reviews — try the suggested follow-up buttons

A sample test CSV:
```csv
review_text,rating,date,author
"Great product, battery life is amazing.",5,2024-06-15,Alice
"Shipping took forever. Product itself is fine.",3,2024-06-20,Bob
"Terrible customer service. Broke after 2 weeks.",1,2024-07-01,Charlie
"Best purchase this year. Highly recommend.",5,2024-07-10,Diana
```

## Deploy (Render)

1. Push to GitHub
2. Connect repo in Render dashboard
3. Set `ANTHROPIC_API_KEY` environment variable
4. Deploy — uses `render.yaml` for config

## CSV Format

The parser auto-detects common column names. Minimum required: a text/review column.

| Column | Aliases |
|--------|---------|
| text | text, review, review_text, content, body, comment |
| rating | rating, score, stars, star_rating |
| date | date, review_date, created_at, timestamp |
| author | author, reviewer, user, username |
| platform | platform, source, site |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `CLAUDE_MODEL` | No | Model override (default: claude-sonnet-4-6-20250514) |
| `FIRECRAWL_API_KEY` | No | Enables URL scraping |
| `DATA_DIR` | No | Data directory (default: data) |
| `CHROMA_DIR` | No | ChromaDB directory (default: data/chroma) |

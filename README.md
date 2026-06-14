# FitFindr

FitFindr is a thrift-shopping styling **agent**. You describe a secondhand piece you want
(optionally with a size and budget), and the agent finds a real listing from a mock marketplace,
shows you how to wear it with clothes you already own, and writes a shareable "fit card" caption for
the find. It is built around a deterministic **planning loop** that wires three tools together,
passing all state through a single `session` dict and branching on what each tool returns.

---

## Setup

```bash
pip install -r requirements.txt
```

Set your Groq API key in a `.env` file (free key at [console.groq.com](https://console.groq.com)):

```
GROQ_API_KEY=your_key_here
```

## Run

```bash
python app.py
```

Open the URL printed in your terminal (usually `http://localhost:7860`, but check the output — the
port can change). Type a request (e.g. *"vintage graphic tee under $30, size M"*), pick a wardrobe,
and click **Find it**. The three panels populate with the listing, an outfit idea, and a fit card.

You can also drive the agent without the UI:

```bash
python agent.py          # runs a happy-path query and a no-results query
pytest tests/            # 12 tool tests (incl. every failure mode)
```

---

## Tool Inventory

All three tools live in `tools.py`.

### 1. `search_listings(description, size, max_price) -> list[dict]`
- **Inputs:**
  - `description: str` — free-text keywords for the piece (e.g. `"vintage graphic tee"`).
  - `size: str | None` — optional size filter (e.g. `"M"`, `"W30"`, `"US 8"`); `None` = no size filter.
  - `max_price: float | None` — optional budget ceiling; `None` = no price filter.
- **Output:** a `list[dict]` of matching listings, **sorted best-match first**. Empty list `[]` when
  nothing matches — never an exception.
- **Purpose:** the only deterministic tool. It loads `data/listings.json` via `load_listings()`,
  applies the hard price/size filters, then **weighted keyword scoring** — a query token found in
  `style_tags`/`colors`/`category` scores **+2**, a token found in `title`/`description` scores **+1**.
  Zero-score listings are dropped; the rest are sorted by score (stable, so ties keep dataset order).

### 2. `suggest_outfit(new_item, wardrobe) -> str`
- **Inputs:**
  - `new_item: dict` — the listing the user is considering (the top search result).
  - `wardrobe: dict` — `{"items": [...]}`; may be empty.
- **Output:** a non-empty `str` with 1–2 outfit ideas. Never empty, never raises.
- **Purpose:** calls Groq `llama-3.3-70b-versatile` (temp `0.4`, low for faithful styling). The system
  prompt **grounds** the model: it may use *only* the new item and the wardrobe pieces listed by name,
  and must not invent clothing the user doesn't own. If the wardrobe is empty it returns general
  styling advice instead.

### 3. `create_fit_card(outfit, new_item) -> str`
- **Inputs:**
  - `outfit: str` — the outfit string produced by `suggest_outfit`.
  - `new_item: dict` — the same selected listing.
- **Output:** a 2–4 sentence caption `str` (casual OOTD voice) that names the item, its price, and
  platform once each. Returns a descriptive error string if `outfit` is empty — never raises.
- **Purpose:** calls Groq `llama-3.3-70b-versatile` at **temp `0.9`** so repeated calls on the same
  input read differently (verified — two runs produced distinct captions).

---

## Planning Loop — What the Agent *Decides*

`run_agent(query, wardrobe)` in `agent.py` runs a fixed 7-step sequence **with a real decision point**,
not an unconditional pipeline:

1. **Initialize** a `session` dict (`_new_session`).
2. **Parse the query — hybrid.** A regex first pass extracts `max_price` (`under $30`, `$30`,
   `less than 30`…), `size` (an explicit `size M` cue, or a multi-character token like `W30`/`US 8`),
   and a leftover `description`. **Decision:** if the regex description comes out empty *or* the query
   is long/noisy (> 12 words of styling chatter), the agent makes **one LLM call** to extract a clean
   `{description, size, max_price}`, then merges it over the regex result. Short queries never pay the
   LLM cost; messy sentences get cleaned up.
3. **`search_listings`** with the parsed params. **This is the branch:**
   - **No results →** set `session["error"]` to a specific, actionable message and **`return`
     immediately** — the two LLM tools are never called.
   - **Results →** continue.
4. **Select** `session["search_results"][0]` (top-ranked match) as `selected_item`.
5. **`suggest_outfit`** on the selected item + wardrobe (empty wardrobe handled *inside* the tool, so
   the loop keeps going — an empty closet is not an error).
6. **`create_fit_card`** on the outfit + selected item.
7. **Return** the session.

The agent therefore behaves differently for different inputs: an impossible query stops at step 3 with
an error and no LLM calls; a long sentence triggers the LLM parser at step 2; a normal query runs all
three tools. (Verified: on the no-results query, `suggest_outfit` and `create_fit_card` were called
**0 times**.)

---

## State Management

A single **`session` dict** is the one source of truth for an interaction. Each step reads fields
written by earlier steps and writes its own — nothing is held in globals, and no step ever re-prompts
the user or uses hardcoded values.

| Field | Written by | Read by |
|-------|-----------|---------|
| `query` | entry point | parse step |
| `parsed` (`{description, size, max_price}`) | parse step | `search_listings` |
| `search_results` | `search_listings` | error check + item selection |
| `selected_item` | selection (`results[0]`) | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | passed in at start | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | final output |
| `error` | any failing step | gate that ends the run early |

`error` acts as a gate: once it is non-`None`, the run returns and the downstream fields stay `None`.
The UI (`app.py`) checks `error` first, then renders the listing + outfit + fit card.

**State passing is verified by identity, not just value.** Using test spies, the exact same dict
object selected at step 4 is the object passed into both `suggest_outfit` and `create_fit_card`
(`selected_item is captured_arg` → `True`), and the `outfit_suggestion` string stored at step 5 is the
exact object handed to `create_fit_card` at step 6.

---

## Error Handling (per tool, with triggered examples)

Every failure mode was triggered deliberately, not assumed. All three return a specific, informative
value instead of raising.

| Tool | Failure mode | Agent response | Triggered result |
|------|-------------|----------------|------------------|
| `search_listings` | No listing matches | Returns `[]`; the loop sets `error` and returns early with an actionable message | `search_listings('designer ballgown', size='XXS', max_price=5)` → `[]`. Full agent → `error`: *"No listings matched 'designer ballgown' in size XXS under $5. Try raising your budget, removing the size filter, or using broader keywords."*, `fit_card` = `None` |
| `suggest_outfit` | Empty wardrobe | Returns general styling advice (not an error); loop continues to the fit card | `suggest_outfit(item, get_empty_wardrobe())` → *"This adorable Y2K baby tee is perfect for a playful, nostalgic look. You can pair it with high-waisted jeans and sneakers… or with a flowy skirt and sandals…"* |
| `suggest_outfit` | LLM/API call fails | Returns a graceful fallback string naming the item + a couple wardrobe pieces | Verified via a test that forces `_chat` to raise — returns a non-empty fallback, no exception |
| `create_fit_card` | Empty/whitespace outfit | Returns a descriptive error string *before* calling the LLM | `create_fit_card('', item)` → *"Couldn't write a caption — no outfit was provided. Generate an outfit suggestion first, then create the fit card."* |

The full suite (`pytest tests/`) has **12 tests, at least one per failure mode**, all passing.

---

## Spec Reflection

**One way the spec guided the implementation.** The State Management table in `planning.md` was written
*before* any code — it named every field and which step writes vs. reads it. Implementing `run_agent`
then became almost mechanical: each numbered step had exactly one field to write, and the `error`-gate
rule told me precisely where the early `return` belonged. Because the contract was fixed up front, the
identity check (same dict flowing between tools) passed on the first run — there was never a temptation
to recompute or re-prompt for a value a previous step already owned.

**One way the implementation diverged from the spec.** The spec described size matching as a
"case-insensitive substring" match. When I implemented it against the real data, naive substring
matching was wrong: a search for size `"S"` would match the shoe size `"US 8"` (the `s`/`S` is a
substring of `US`). I diverged to **token-aware** matching — split the listing size on
non-alphanumerics and match whole tokens — which still satisfies the spec's example (`"M"` matches
`"S/M"`) but correctly excludes `"US 8"`. I confirmed this with a test
(`test_search_size_token_match_not_substring`).

---

## AI Usage

**Instance 1 — implementing `search_listings`.** I gave the AI the Tool 1 spec block from
`planning.md` (the three parameters, the weighted-scoring rule, and the empty-list failure behavior)
plus the function stub. It produced a working scorer, but its size filter used a plain
`size.lower() in listing_size.lower()` substring check. I **overrode** that: against the real listings
it caused false positives (`"S"` matching `"US 8"`), so I replaced it with token-aware matching and
added a stopword filter to the keyword tokenizer so chatter words like *"for"*/*"a"* don't score. I
verified the fix with a dedicated test before trusting it.

**Instance 2 — implementing the planning loop.** I gave the AI the **Planning Loop**, **State
Management**, and **Architecture** (ASCII diagram) sections plus the `agent.py` stub, and asked it to
implement `run_agent` following the 7 steps and the `session` dict exactly. It produced the loop with
the correct early-return branch. I **changed two things**: (1) I made the hybrid parser actually
*merge* the LLM result over the regex result (keep regex price/size if the LLM omitted them) rather
than replacing wholesale, and (2) I added an explicit identity verification (spies asserting the same
dict flows between tools) because "it ran" isn't proof that state is passing correctly — and that test
is what caught how important the single-dict discipline was.

---

## Dataset & Wardrobe (reference)

- `data/listings.json` — mock secondhand listings with `id`, `title`, `description`, `category`,
  `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`.
- `data/wardrobe_schema.json` — wardrobe format plus an `example_wardrobe` (10 items) and an
  `empty_wardrobe` template. Load via `get_example_wardrobe()` / `get_empty_wardrobe()` in
  `utils/data_loader.py`.

## Project Layout

```
ai201-project2-fitfindr-starter/
├── agent.py            # run_agent() — the planning loop + hybrid query parser
├── tools.py            # search_listings, suggest_outfit, create_fit_card
├── app.py              # Gradio UI (handle_query maps session → 3 panels)
├── conftest.py         # makes `from tools import ...` resolve under pytest
├── tests/test_tools.py # 12 tests, ≥1 per failure mode
├── data/               # listings.json, wardrobe_schema.json
├── utils/data_loader.py
└── planning.md         # full design: tools, loop, state, errors, architecture
```

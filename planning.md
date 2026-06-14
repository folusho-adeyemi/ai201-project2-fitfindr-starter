# FitFindr — planning.md

> Complete this document before writing any implementation code.
> Your spec and agent diagram are what you'll use to direct AI tools (Claude, Copilot, etc.) to generate your implementation — the more specific they are, the more useful the generated code will be.
> Your planning.md will be reviewed as part of your submission.
> Update it before starting any stretch features.

---

## Tools

List every tool your agent will use. For each tool, fill in all four fields.
You must have at least 3 tools. The three required tools are listed — add any additional tools below them.

### Tool 1: search_listings

**What it does:**
Searches the mock secondhand-listings dataset (via `load_listings()`) for items matching the
user's description keywords, then ranks them by a weighted keyword-overlap score. It first applies
hard filters (price ceiling, size), then scores what's left by style relevance. No LLM — pure,
deterministic Python so it's fast and unit-testable.

**Input parameters:**
- `description` (str): free-text keywords describing the wanted piece (e.g., `"vintage graphic tee"`).
  Lower-cased and tokenized, then matched against each listing's fields.
- `size` (str | None): optional size filter. Case-insensitive **substring** match against the
  listing's `size` field (so `"M"` matches `"S/M"` and `"M"`). `None` = skip size filtering.
- `max_price` (float | None): optional inclusive price ceiling — keep listings where
  `price <= max_price`. `None` = skip price filtering.

**What it returns:**
A `list[dict]` of matching listings, **sorted by relevance score (highest first)**. Each dict is a
full listing: `id, title, description, category, style_tags (list), size, condition, price (float),
colors (list), brand, platform`. Returns an **empty list** (never raises) when nothing matches.

Scoring rule (weighted overlap): for each query token, `+2` if it appears in
`style_tags` / `colors` / `category` (curated style signals), `+1` if it appears in
`title` / `description` (prose). Listings scoring `0` are dropped; the rest are sorted descending.

**What happens if it fails or returns nothing:**
It returns `[]` rather than erroring. The planning loop detects the empty list, sets
`session["error"]` to a specific, actionable message (naming the query and suggesting the user
raise `max_price` or drop the `size` filter), and **returns early** — it does not call
`suggest_outfit` with no item.

---

### Tool 2: suggest_outfit

**What it does:**
Given the selected thrifted item and the user's wardrobe, it asks the LLM (Groq) to build 1–2
complete outfits that combine the new item with pieces the user already owns. The whole wardrobe
(~10 items) is sent in the prompt and the model is instructed to use **only named wardrobe pieces +
the new item** — so it can't invent clothes the user doesn't own.

**Input parameters:**
- `new_item` (dict): the selected listing dict from `search_listings` (the item being considered).
- `wardrobe` (dict): a wardrobe in the schema format — `{"items": [ {id, name, category, colors,
  style_tags, notes}, ... ]}`. May be empty (`items == []`).

**What it returns:**
A non-empty `str` describing 1–2 outfits, referencing specific wardrobe pieces by name (e.g.,
"pair it with your baggy dark-wash jeans and chunky white sneakers, then layer the vintage black
denim jacket"). Run at **low temperature (~0.4)** to stay grounded in the actual pieces.

**What happens if it fails or returns nothing:**
- **Empty wardrobe** is a normal case, not an error: the tool instead returns general styling
  advice for the item (what categories/colors/vibes pair well) and the agent keeps going.
- If the **LLM/API call fails**, the tool returns a short graceful fallback string (e.g.,
  "Couldn't generate a styled outfit right now — here are the item's style tags to build around:
  …") so the loop can still produce a result instead of crashing.

---

### Tool 3: create_fit_card

**What it does:**
Turns the outfit suggestion + the thrifted item into a short, casual, shareable caption (think
OOTD/Instagram post, not a product blurb). Calls the LLM at **higher temperature (~0.9)** so the
caption sounds fresh and varies between runs.

**Input parameters:**
- `outfit` (str): the outfit-suggestion string returned by `suggest_outfit`.
- `new_item` (dict): the selected listing dict (used to name the item, price, and platform).

**What it returns:**
A 2–4 sentence caption `str` that: feels authentic, mentions the item **name, price, and platform
once each** naturally, and captures the outfit's vibe in specific terms.

**What happens if it fails or returns nothing:**
It guards against an empty/whitespace-only `outfit` up front and, in that case, returns a
**descriptive error message string** (e.g., "Can't write a fit card — no outfit was provided.")
rather than raising. The agent surfaces that string instead of a broken card.

---

### Additional Tools (if any)

<!-- Copy the block above for any tools beyond the required three -->

---

## Planning Loop

**How does your agent decide which tool to call next?**
The loop is a **deterministic sequence with conditional early-exit** (not LLM-driven tool
selection). It always runs the same ordered pipeline, but branches on the result of each step:

1. **Initialize:** `session = _new_session(query, wardrobe)`.
2. **Parse the query (hybrid):** First try **regex/rules** on the raw query:
   - `max_price`: match patterns like `under $30`, `$30`, `less than 30`, `below 30` → `float(30)`.
   - `size`: match an explicit size cue — the word after `"size"` (e.g., `size M`) or a known size
     token (`XS/S/M/L/XL`, `W30`, `US 7`, etc.). Otherwise `None`.
   - `description`: the query with the matched price/size phrases removed and filler trimmed.
   - **LLM fallback:** if regex leaves the description empty or obviously noisy (e.g., the user wrote
     a full sentence with styling context), call the LLM once to extract clean
     `{description, size, max_price}`. Store the result in `session["parsed"]`.
3. **search_listings:** call with the parsed params; store in `session["search_results"]`.
   - **Branch (error):** `if not search_results:` set `session["error"]` to a specific message
     ("No listings matched '<description>' under $<max_price>. Try raising your budget or removing
     the size filter.") and **`return session` immediately**. Do not continue.
   - **Branch (success):** proceed.
4. **Select item:** `session["selected_item"] = search_results[0]` (top-ranked match).
5. **suggest_outfit:** call with `selected_item` + `wardrobe`; store in
   `session["outfit_suggestion"]`. (Empty wardrobe is handled inside the tool, not here — it returns
   general advice, so the loop keeps going.)
6. **create_fit_card:** call with `outfit_suggestion` + `selected_item`; store in
   `session["fit_card"]`.
7. **Done:** `return session`. The interaction is complete when `fit_card` is set (success) or as
   soon as `error` is set (early exit).

**How it knows it's done:** there is no open-ended loop — it terminates after step 6 on success, or
the moment `session["error"]` is set on the no-results branch.

---

## State Management

**How does information from one tool get passed to the next?**
A single **`session` dict** (created by `_new_session()` in `agent.py`) is the one source of truth
for the whole interaction. Each step reads the fields written by earlier steps and writes its own:

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

Nothing is stored in globals — passing the dict (rather than returning loose values) means any step
can see the full context, and `error` acts as a gate: once it's non-`None`, downstream fields stay
`None` and the run returns. The UI checks `error` first, then renders `fit_card` + the outfit.

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Set `session["error"]` and return early with a specific, actionable message that echoes the search and offers a next step: *"No listings matched 'vintage graphic tee' under $30. Try raising your budget, removing the size filter, or using broader keywords."* No outfit/card is generated. |
| suggest_outfit | Wardrobe is empty | Not treated as an error — the tool returns **general styling advice** for the item (complementary categories, colors, and vibe to build around) and the loop continues to `create_fit_card`. The UI can also nudge: *"Add items to your wardrobe for outfits built from pieces you own."* |
| suggest_outfit | LLM/API call fails | Return a graceful fallback string (e.g., *"Couldn't generate a styled outfit right now — build around these style tags: vintage, graphic tee, streetwear."*) so the run still produces output instead of crashing. |
| create_fit_card | Outfit input is missing or incomplete | Guard against empty/whitespace `outfit`; return a descriptive message string — *"Can't write a fit card — no outfit was provided."* — instead of raising or producing a broken caption. |

---

## Architecture

<!-- Draw a diagram of your agent showing how the components connect:
     User input → Planning Loop → Tools (search_listings, suggest_outfit, create_fit_card)
                                                                          ↕
                                                                   State / Session
     Show what triggers each tool, how state flows between them, and where error paths branch off.
     ASCII art, a Mermaid diagram (https://mermaid.js.org/syntax/flowchart.html), or an embedded
     sketch are all fine. You'll share this diagram with an AI tool when asking it to implement
     the planning loop and each individual tool. -->

```
User query: "vintage graphic tee under $30, I wear baggy jeans"   wardrobe (dict)
      │                                                                 │
      ▼                                                                 │
┌──────────────────────────────────────────────────────────────────────┼────────┐
│ PLANNING LOOP  (agent.run_agent)                                       │        │
│                                                                        ▼        │
│  Step 1  session = _new_session(query, wardrobe) ───────────────► SESSION STATE │
│                                                                   (one dict:     │
│  Step 2  parse query  ── regex (price/size) ──┐                    query,        │
│            └─ if description empty/noisy ─► LLM fallback            parsed,       │
│                                               │                    search_results│
│            writes ► session["parsed"] = {description, size, max_price}           │
│                                               │                    selected_item │
│  Step 3  search_listings(description, size, max_price)             outfit_sugg.  │
│            │  (load_listings → filter price/size → weighted        fit_card,     │
│            │   keyword score → drop 0 → sort)                       error)       │
│            │                                                                     │
│            ├─ results == []  ─► session["error"] = "No listings matched…         │
│            │                     raise budget / drop size"  ──► RETURN (early) ──┼──► [ERROR PATH]
│            │                                                                     │
│            │ results = [item, …]   writes ► session["search_results"]            │
│            ▼                                                                     │
│  Step 4  session["selected_item"] = results[0]                                   │
│            │                                                                     │
│  Step 5  suggest_outfit(selected_item, wardrobe)   ── LLM (temp ~0.4) ──         │
│            │   empty wardrobe ► general advice (NOT an error, continue)          │
│            │   writes ► session["outfit_suggestion"]                             │
│            ▼                                                                     │
│  Step 6  create_fit_card(outfit_suggestion, selected_item) ── LLM (temp ~0.9)    │
│            │   empty outfit ► descriptive error string                           │
│            │   writes ► session["fit_card"]                                      │
│            ▼                                                                     │
│  Step 7  RETURN session ◄────────────────────────────────────────────────── ◄──┘
└─────────────────────────────────────────────────────────────────────────────────┘
      │
      ▼
UI (app.py): if session["error"] → show the error message;
             else → show selected_item + outfit_suggestion + fit_card
```

---

## AI Tool Plan

<!-- For each part of the implementation below, describe:
     - Which AI tool you plan to use (Claude, Copilot, ChatGPT, etc.)
     - What you'll give it as input (which sections of this planning.md, your agent diagram)
     - What you expect it to produce
     - How you'll verify the output matches your spec before moving on

     "I'll use AI to help me code" is not a plan.
     "I'll give Claude my Tool 1 spec (inputs, return value, failure mode) and ask it to implement
     search_listings() using load_listings() from the data loader — then test it against 3 queries
     before trusting it" is a plan. -->

**Milestone 3 — Individual tool implementations:**
I'll use **Claude (in Cursor)**, one tool at a time, giving it the matching planning.md tool block
plus the function stub in `tools.py`:
- *`search_listings`* — give it the Tool 1 block (the three params + weighted-scoring rule + empty-list
  behavior) and ask it to implement using `load_listings()`. **Verify before trusting:** confirm it
  (a) filters by `max_price` and `size` case-insensitively, (b) scores tag/color/category hits higher
  than title/description, (c) drops 0-score and sorts descending, (d) returns `[]` not an exception.
  Then test with 3 queries: a normal one ("vintage graphic tee"), a too-expensive filter (expect
  `[]`), and a size filter ("M").
- *`suggest_outfit`* — give it the Tool 2 block and ask it to build the LLM prompt that sends the whole
  wardrobe and forbids non-wardrobe pieces, at temp ~0.4. **Verify:** empty wardrobe returns general
  advice (not a crash); a populated wardrobe names real pieces (`w_00x`) and doesn't invent clothes.
- *`create_fit_card`* — give it the Tool 3 block and ask for the high-temp caption prompt. **Verify:**
  empty `outfit` returns the guard string; a real outfit yields a 2–4 sentence caption naming the
  item/price/platform once each.

**Milestone 4 — Planning loop and state management:**
I'll give Claude the **Planning Loop**, **State Management**, and **Architecture** sections (the
ASCII diagram especially) plus the `agent.py` stub, and ask it to implement `run_agent()` following
the 7 steps and the `session` dict exactly. **Verify before trusting:** trace that (a) parse fills
`session["parsed"]`, (b) the empty-`search_results` branch sets `error` and returns *before*
`suggest_outfit`, (c) `selected_item = results[0]`, and (d) each field is written by the right step.
Then run the two built-in CLI cases in `agent.py` (happy path → a fit card; "designer ballgown size
XXS under $5" → an error message, no card).

---

## A Complete Interaction (Step by Step)

**What FitFindr does :** FitFindr is a thrift-shopping styling agent — a user describes a secondhand piece they want (with an optional size and budget), and the agent finds real listings and shows how to wear them with clothes the user already owns. A shopping request triggers `search_listings`, which filters `listings.json` by description, size, and `max_price`; once a listing is picked, that result triggers `suggest_outfit`, which pairs the new item against the user's wardrobe by matching category, colors, and style_tags; and the completed pairing triggers `create_fit_card`, which formats the item plus its styled outfit into one clean, shareable summary. On failure each tool degrades gracefully instead of guessing: if `search_listings` finds nothing it says so and suggests loosening filters (e.g., raising the budget) rather than inventing listings, if the wardrobe is empty `suggest_outfit` reports it can't style anything yet and asks the user to add items, and if `create_fit_card` gets incomplete outfit data it flags the missing pieces instead of rendering a broken card.

Write out what a full user interaction looks like from start to finish — tool call by tool call. Use a specific example query.

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — Parse the query (hybrid).** The agent initializes the `session`, then parses the raw
query. Regex catches `under $30` → `max_price = 30.0` and finds no explicit size cue → `size = None`.
Because the leftover text still contains styling context ("I mostly wear baggy jeans and chunky
sneakers… how would I style it"), the **LLM fallback** runs once to isolate the search intent →
`description = "vintage graphic tee"`. Result stored: `session["parsed"] = {"description": "vintage
graphic tee", "size": None, "max_price": 30.0}`.

**Step 2 — search_listings("vintage graphic tee", None, 30.0).** Price filter keeps items ≤ $30;
weighted scoring rewards the curated tags `"graphic tee"` and `"vintage"`. Top scorers are
`lst_006` (Graphic Tee — 2003 Tour Bootleg, $24, tags `graphic tee/vintage/grunge`) and `lst_002`
(Y2K Baby Tee, $18, tags `graphic tee/vintage`). Returns a ranked, non-empty list →
`session["search_results"]`. Non-empty, so the loop continues.

**Step 3 — Select item.** `session["selected_item"] = search_results[0]` → the `lst_006` bootleg
graphic tee (best score).

**Step 4 — suggest_outfit(lst_006, wardrobe).** The whole example wardrobe is sent to the LLM
(temp ~0.4) with the rule "use only these named pieces + the new item." It returns something like:
*"Wear the black bootleg graphic tee with your baggy dark-wash jeans (w_001) and chunky white
sneakers (w_007); layer your vintage black denim jacket (w_006) and add the black crossbody bag."*
Stored in `session["outfit_suggestion"]`.

**Step 5 — create_fit_card(outfit, lst_006).** The caption tool (temp ~0.9) produces a casual OOTD
line naming the item, price, and platform once each → `session["fit_card"]`, e.g. *"Scored this 2003
tour bootleg tee on Depop for $24 🖤 styled it with my baggy jeans + chunky sneakers and threw the
vintage denim jacket on top — effortless grunge-streetwear energy."*

**Step 6 — Return.** `session` is returned with `error = None` and all fields populated.

**Final output to user:**
The user sees three things: (1) the **found listing** — "Graphic Tee — 2003 Tour Bootleg Style, $24
on Depop"; (2) the **outfit suggestion** referencing their own baggy jeans, chunky sneakers, and
denim jacket; and (3) the **shareable fit card** caption. If instead the search had returned nothing
(e.g., "under $5"), they'd see only the error message suggesting they raise the budget — and no
outfit or card.

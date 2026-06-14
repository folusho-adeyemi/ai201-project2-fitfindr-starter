"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import json
import re

from tools import search_listings, suggest_outfit, create_fit_card, _chat


# ── query parsing (hybrid: regex first, LLM fallback) ─────────────────────────

# Budget cues: "under $30", "$30", "less than 30", "below 30", "max 30", "up to 30".
_PRICE_RE = re.compile(
    r"(?:under|below|less than|max(?:imum)?|up to|<)\s*\$?\s*(\d+(?:\.\d+)?)"
    r"|\$\s*(\d+(?:\.\d+)?)",
    re.I,
)
# Explicit "size M" cue — captures single-letter sizes safely.
_SIZE_CUE_RE = re.compile(r"\bsize\s+([a-z0-9/]+)", re.I)
# Standalone size tokens — only multi-char/numeric ones, so a stray "m" in "I'm"
# is never misread as size M (single letters require the explicit "size" cue).
_SIZE_TOKEN_RE = re.compile(r"\b(xxs|xxl|xs|xl|w\d{2}(?:\s*l\d{2})?|us\s?\d{1,2})\b", re.I)


def _parse_with_regex(query: str) -> dict:
    """Deterministic first pass — pull price/size out, leave the rest as description."""
    max_price = None
    pm = _PRICE_RE.search(query)
    if pm:
        max_price = float(pm.group(1) or pm.group(2))

    size = None
    sm = _SIZE_CUE_RE.search(query)
    if sm:
        size = sm.group(1).upper()
    else:
        tm = _SIZE_TOKEN_RE.search(query)
        if tm:
            size = re.sub(r"\s+", " ", tm.group(1)).upper()

    # Description = query with the matched price/size phrases stripped out.
    desc = _PRICE_RE.sub(" ", query)
    desc = _SIZE_CUE_RE.sub(" ", desc)
    desc = _SIZE_TOKEN_RE.sub(" ", desc)
    desc = re.sub(r"[^\w\s/&'-]", " ", desc)        # drop stray punctuation
    desc = re.sub(r"\s+", " ", desc).strip()

    return {"description": desc, "size": size, "max_price": max_price}


def _looks_noisy(query: str) -> bool:
    """A long, sentence-y query (styling chatter, multiple clauses) is better
    parsed by the LLM than by regex leftovers."""
    return len(re.findall(r"\w+", query)) > 12


def _parse_with_llm(query: str) -> dict | None:
    """LLM fallback — extract clean {description, size, max_price} as JSON.
    Returns None if the call or JSON parse fails (caller keeps the regex result)."""
    messages = [
        {
            "role": "system",
            "content": (
                "Extract thrift-search filters from the user's request. Respond with "
                "ONLY a JSON object: {\"description\": string, \"size\": string|null, "
                "\"max_price\": number|null}. 'description' is just the garment keywords "
                "(e.g. 'vintage graphic tee') with no price, size, or styling chatter."
            ),
        },
        {"role": "user", "content": query},
    ]
    try:
        raw = _chat(messages, temperature=0.0, max_tokens=150)
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I).strip()
        data = json.loads(raw)
        if not isinstance(data, dict) or not data.get("description"):
            return None
        mp = data.get("max_price")
        return {
            "description": str(data["description"]).strip(),
            "size": (str(data["size"]).strip() if data.get("size") else None),
            "max_price": float(mp) if mp is not None else None,
        }
    except Exception:
        return None


def _parse_query(query: str) -> dict:
    """Hybrid parse: regex first; fall back to the LLM only when the regex
    description comes out empty or the query is long/noisy."""
    parsed = _parse_with_regex(query)
    if not parsed["description"] or _looks_noisy(query):
        llm = _parse_with_llm(query)
        if llm:
            # Prefer the LLM's cleaner description; keep regex price/size if the
            # LLM omitted them.
            parsed = {
                "description": llm["description"] or parsed["description"],
                "size": llm["size"] or parsed["size"],
                "max_price": llm["max_price"] if llm["max_price"] is not None else parsed["max_price"],
            }
    return parsed


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Step 1 — initialize the single source of truth for this interaction.
    session = _new_session(query, wardrobe)

    # Step 2 — parse the query (hybrid regex → LLM fallback).
    session["parsed"] = _parse_query(query)
    parsed = session["parsed"]

    # Step 3 — search, then BRANCH on the result.
    session["search_results"] = search_listings(
        description=parsed["description"],
        size=parsed["size"],
        max_price=parsed["max_price"],
    )
    if not session["search_results"]:
        # No-results path: set error and return BEFORE calling the LLM tools.
        budget = f" under ${parsed['max_price']:.0f}" if parsed["max_price"] is not None else ""
        size = f" in size {parsed['size']}" if parsed["size"] else ""
        session["error"] = (
            f"No listings matched '{parsed['description']}'{size}{budget}. "
            "Try raising your budget, removing the size filter, or using broader keywords."
        )
        return session

    # Step 4 — select the top-ranked match.
    session["selected_item"] = session["search_results"][0]

    # Step 5 — style it against the wardrobe (empty wardrobe handled inside the tool).
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )

    # Step 6 — turn the styled outfit into a shareable fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7 — done.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")

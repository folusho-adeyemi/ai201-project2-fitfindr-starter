"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

MODEL = "llama-3.3-70b-versatile"

# Trivial words to ignore when tokenizing a free-text search description, so a
# query like "looking for a vintage tee" scores on "vintage"/"tee", not "for"/"a".
_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "of", "to", "in", "on", "my",
    "i", "im", "is", "it", "some", "looking", "want", "wanted", "need", "needs",
    "under", "less", "than", "below", "over", "around", "about", "that", "this",
    "something", "like", "please", "find", "me",
}


def _tokenize(text: str) -> list[str]:
    """Lower-case, split on non-alphanumerics, drop stopwords and 1-char tokens."""
    return [
        t for t in re.findall(r"[a-z0-9]+", text.lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


def _size_matches(query_size: str, listing_size: str) -> bool:
    """Token-aware, case-insensitive size match.

    Splitting on non-alphanumerics and matching whole tokens means "M" matches
    "S/M" (token 'm') without "S" wrongly matching "US 7" (tokens 'us','7').
    Multi-char sizes (e.g. "w30", "xxl") may also match inside a compound string.
    """
    q = query_size.strip().lower()
    if not q:
        return True
    ls = listing_size.lower()
    tokens = re.split(r"[^a-z0-9]+", ls)
    if q in tokens:
        return True
    return len(q) >= 3 and q in ls


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    listings = load_listings()
    tokens = _tokenize(description or "")

    scored: list[tuple[int, dict]] = []
    for item in listings:
        # 1. Hard filters first — cheaply discard anything out of budget or size.
        if max_price is not None and item["price"] > max_price:
            continue
        if size is not None and not _size_matches(size, item["size"]):
            continue

        # 2. Weighted keyword score. Curated style fields (tags/colors/category)
        #    are stronger relevance signals than free-text title/description.
        tag_text = " ".join(
            item.get("style_tags", []) + item.get("colors", []) + [item.get("category", "")]
        ).lower()
        prose_text = f"{item.get('title', '')} {item.get('description', '')}".lower()

        score = 0
        for tok in tokens:
            if tok in tag_text:
                score += 2
            elif tok in prose_text:
                score += 1

        # 3. Drop zero-score (no relevant match).
        if score > 0:
            scored.append((score, item))

    # 4. Sort by score, highest first (stable sort keeps dataset order on ties).
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


# ── Shared LLM helper ─────────────────────────────────────────────────────────

def _chat(messages: list[dict], temperature: float = 0.7, max_tokens: int = 600) -> str:
    """Single Groq chat call. Raises on API/connection errors so callers can
    catch and return a graceful fallback string."""
    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def _describe_item(item: dict) -> str:
    """Compact one-line summary of a listing for use inside an LLM prompt."""
    parts = [item.get("title", "an item")]
    if item.get("category"):
        parts.append(f"category: {item['category']}")
    if item.get("colors"):
        parts.append("colors: " + ", ".join(item["colors"]))
    if item.get("style_tags"):
        parts.append("style: " + ", ".join(item["style_tags"]))
    if item.get("price") is not None:
        parts.append(f"${item['price']:.0f}")
    if item.get("platform"):
        parts.append(f"on {item['platform']}")
    return " | ".join(parts)


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    item_desc = _describe_item(new_item)
    items = (wardrobe or {}).get("items") or []

    # Empty-wardrobe path: no closet to ground against, so give general advice.
    if not items:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are FitFindr, a thrift styling assistant. Give brief, "
                    "practical styling advice in a friendly, casual tone."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"I'm considering buying this secondhand item:\n{item_desc}\n\n"
                    "I haven't entered my wardrobe yet. Suggest 1-2 general outfit "
                    "directions for this piece — what kinds of items pair well and "
                    "what vibe it suits. Keep it to a few sentences."
                ),
            },
        ]
        try:
            return _chat(messages, temperature=0.5)
        except Exception:
            return (
                f"I couldn't reach the styling model right now, but the "
                f"{new_item.get('title', 'piece')} is versatile — pair it with simple "
                "basics in complementary colors and let it be the statement piece."
            )

    # Grounded path: build outfits only from named wardrobe pieces + the new item.
    wardrobe_lines = []
    for w in items:
        descriptors = []
        if w.get("colors"):
            descriptors.append(", ".join(w["colors"]))
        if w.get("category"):
            descriptors.append(w["category"])
        suffix = f" ({'; '.join(descriptors)})" if descriptors else ""
        wardrobe_lines.append(f"- {w.get('name', 'item')}{suffix}")
    wardrobe_block = "\n".join(wardrobe_lines)

    messages = [
        {
            "role": "system",
            "content": (
                "You are FitFindr, a thrift styling assistant. Build outfits ONLY "
                "from the new item and the wardrobe pieces the user lists by name. "
                "Never invent clothing the user does not own. Refer to each wardrobe "
                "piece by its given name."
            ),
        },
        {
            "role": "user",
            "content": (
                f"New item I'm considering:\n{item_desc}\n\n"
                f"My current wardrobe:\n{wardrobe_block}\n\n"
                "Suggest 1-2 complete outfits that style the new item with pieces "
                "from my wardrobe. Name each wardrobe piece you use. Keep each outfit "
                "to 1-2 sentences."
            ),
        },
    ]
    try:
        return _chat(messages, temperature=0.4)
    except Exception:
        names = ", ".join(w.get("name", "a piece") for w in items[:3])
        return (
            "The styling model is unavailable right now. As a starting point, try "
            f"pairing the {new_item.get('title', 'new item')} with {names} from your closet."
        )


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    # Guard: an empty/whitespace outfit means upstream produced nothing to caption.
    if not outfit or not outfit.strip():
        return (
            "Couldn't write a caption — no outfit was provided. "
            "Generate an outfit suggestion first, then create the fit card."
        )

    title = new_item.get("title", "this thrifted find")
    price = new_item.get("price")
    price_str = f"${price:.0f}" if price is not None else "a steal"
    platform = new_item.get("platform", "secondhand")

    messages = [
        {
            "role": "system",
            "content": (
                "You write short, authentic OOTD captions for thrift finds — the kind "
                "a real person posts on Instagram or TikTok. Casual and specific, never "
                "like a product listing. No hashtags unless they feel natural."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Item: {title} ({price_str}, found on {platform})\n"
                f"Outfit I styled it in:\n{outfit}\n\n"
                "Write a 2-4 sentence caption capturing this outfit's vibe. Mention the "
                f"item name, its price ({price_str}), and that it's from {platform} — "
                "each naturally and only once."
            ),
        },
    ]
    try:
        # High temperature so repeated calls on the same input read differently.
        return _chat(messages, temperature=0.9)
    except Exception:
        return (
            f"Snagged the {title} for {price_str} on {platform} and I'm obsessed. "
            "(Caption generator is offline right now — here's a quick placeholder.)"
        )

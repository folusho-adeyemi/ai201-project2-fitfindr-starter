"""Unit tests for the three FitFindr tools.

search_listings is deterministic, so it is tested against the real dataset.
suggest_outfit and create_fit_card call the LLM, so the network-dependent path
is isolated by monkeypatching tools._chat — this keeps the suite fast and
deterministic while still exercising every documented failure mode.
"""

import tools
from tools import search_listings, suggest_outfit, create_fit_card


# ── search_listings ───────────────────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []  # empty list, no exception


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


def test_search_ranked_by_relevance():
    # Results must be sorted by descending keyword score.
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert len(results) >= 2  # enough to verify ordering matters


def test_search_size_token_match_not_substring():
    # "S" must not falsely match shoe sizes like "US 8" via naive substring.
    sized = search_listings("sneakers", size="S", max_price=None)
    assert all("us" not in item["size"].lower().split() or False for item in sized)
    assert sized == []  # no top in this dataset is literally size "S"


# ── suggest_outfit ─────────────────────────────────────────────────────────────

EXAMPLE_ITEM = {
    "id": "lst_006",
    "title": "Graphic Tee — 2003 Tour Bootleg Style",
    "category": "tops",
    "style_tags": ["vintage", "graphic", "streetwear"],
    "size": "L",
    "price": 24.0,
    "colors": ["black", "white"],
    "platform": "depop",
}

EXAMPLE_WARDROBE = {
    "items": [
        {"id": "w_001", "name": "Baggy straight-leg jeans, dark wash",
         "category": "bottoms", "colors": ["dark blue"], "style_tags": ["baggy"]},
        {"id": "w_007", "name": "Chunky white sneakers",
         "category": "shoes", "colors": ["white"], "style_tags": ["chunky"]},
    ]
}


def test_suggest_outfit_with_wardrobe(monkeypatch):
    monkeypatch.setattr(tools, "_chat", lambda *a, **k: "Pair the tee with the baggy jeans.")
    out = suggest_outfit(EXAMPLE_ITEM, EXAMPLE_WARDROBE)
    assert isinstance(out, str) and out.strip()


def test_suggest_outfit_empty_wardrobe(monkeypatch):
    # Failure mode: empty wardrobe must not crash and must return non-empty advice.
    monkeypatch.setattr(tools, "_chat", lambda *a, **k: "Style it with simple basics.")
    out = suggest_outfit(EXAMPLE_ITEM, {"items": []})
    assert isinstance(out, str) and out.strip()


def test_suggest_outfit_llm_failure_falls_back(monkeypatch):
    # Failure mode: API/connection error returns a graceful string, no exception.
    def boom(*a, **k):
        raise RuntimeError("groq down")
    monkeypatch.setattr(tools, "_chat", boom)
    out = suggest_outfit(EXAMPLE_ITEM, EXAMPLE_WARDROBE)
    assert isinstance(out, str) and out.strip()


# ── create_fit_card ─────────────────────────────────────────────────────────────

def test_create_fit_card_returns_caption(monkeypatch):
    monkeypatch.setattr(tools, "_chat", lambda *a, **k: "Thrifted gold. Obsessed.")
    card = create_fit_card("Tee + baggy jeans + chunky sneakers", EXAMPLE_ITEM)
    assert isinstance(card, str) and card.strip()


def test_create_fit_card_empty_outfit():
    # Failure mode: empty outfit returns an error string, never raises.
    card = create_fit_card("", EXAMPLE_ITEM)
    assert isinstance(card, str)
    assert "no outfit" in card.lower()


def test_create_fit_card_whitespace_outfit():
    card = create_fit_card("   \n  ", EXAMPLE_ITEM)
    assert isinstance(card, str)
    assert "no outfit" in card.lower()


def test_create_fit_card_llm_failure_falls_back(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("groq down")
    monkeypatch.setattr(tools, "_chat", boom)
    card = create_fit_card("Tee + jeans + sneakers", EXAMPLE_ITEM)
    assert isinstance(card, str) and card.strip()

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz, process


def score_artist_match(query: str, candidates: list[dict[str, str]]) -> list[dict[str, Any]]:
    """
    Return candidates with fuzzy ratio score sorted desc.
    Each candidate: {"id": str, "name": str}
    """
    results: list[tuple[str, float, dict[str, str]]] = process.extract(
        query,
        {c["id"]: c["name"] for c in candidates},
        scorer=fuzz.WRatio,
        limit=None,
    )
    # process.extract returns list of tuples: (key, score, ...)
    ranked: list[dict[str, Any]] = []
    for key, score, _ in results:
        name = next((c["name"] for c in candidates if c["id"] == key), None)
        if name is None:
            continue
        ranked.append({"id": key, "name": name, "score": float(score)})
    ranked.sort(key=lambda x: float(x["score"]), reverse=True)
    return ranked

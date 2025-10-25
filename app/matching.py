from __future__ import annotations

from typing import TypedDict

from rapidfuzz import fuzz, process


class ArtistCandidate(TypedDict):
    id: str
    name: str
    score: float


def score_artist_match(query: str, candidates: list[dict[str, str]]) -> list[ArtistCandidate]:
    """
    Return candidates with fuzzy ratio score sorted desc.
    Each candidate: {"id": str, "name": str}
    """
    results: list[tuple[str, float, object]] = process.extract(
        query,
        {c["id"]: c["name"] for c in candidates},
        scorer=fuzz.WRatio,
        limit=None,
    )
    # process.extract returns list of tuples: (key, score, ...)
    ranked: list[ArtistCandidate] = []
    for key, score, _ in results:
        name = next((c["name"] for c in candidates if c["id"] == key), None)
        if name is None:
            continue
        ranked.append({"id": key, "name": name, "score": float(score)})
    ranked.sort(key=lambda x: float(x["score"]), reverse=True)
    return ranked

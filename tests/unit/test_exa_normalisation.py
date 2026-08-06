"""`SearchResponse` normalisation from Exa's raw payload shape — provider
details (the `id`/`score`/`publishedDate` fields Exa returns) must not leak
into the typed `SearchResult` fields, only into the opaque `raw` escape
hatch."""

from __future__ import annotations

from api.search.exa import _parse_response


def test_parses_results_and_credits() -> None:
    body = {
        "results": [
            {
                "id": "https://www.openproject.org/",
                "title": "OpenProject",
                "url": "https://www.openproject.org/",
                "publishedDate": "2026-07-23T08:07:00.000Z",
                "score": 1,
                "text": "Open source project management software. " * 20,
            }
        ],
        "costDollars": {"total": 0.007},
    }
    results, credits = _parse_response(body)

    assert credits == 0.007
    assert len(results) == 1
    r = results[0]
    assert r.title == "OpenProject"
    assert r.url == "https://www.openproject.org/"
    assert r.rank == 0
    assert r.provider == "exa"
    assert r.snippet.startswith("Open source project management software.")
    assert len(r.snippet) <= 500
    assert r.raw["id"] == "https://www.openproject.org/"  # provider extras stay in `raw`


def test_multiple_results_get_sequential_rank() -> None:
    body = {"results": [{"url": "a", "title": "A"}, {"url": "b", "title": "B"}]}
    results, _ = _parse_response(body)
    assert [r.rank for r in results] == [0, 1]


def test_missing_cost_dollars_defaults_to_zero() -> None:
    results, credits = _parse_response({"results": []})
    assert credits == 0.0
    assert results == []


def test_missing_text_gives_empty_snippet() -> None:
    results, _ = _parse_response({"results": [{"url": "a", "title": "A"}]})
    assert results[0].snippet == ""

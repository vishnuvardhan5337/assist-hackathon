"""Vector DB (Chroma) tests: privacy is enforced inside the vector query, and everything works offline."""
import pytest

from assist import store, vectors
from assist.world import World

chromadb = pytest.importorskip("chromadb")

QUERIES = ["trip to Pune next week", "contract renewal pricing", "who owns the account now",
           "blocked on legal review", "leave and holiday", "budget approval", "personal medical appointment"]


@pytest.fixture(scope="module")
def world():
    return World(store.connect())


def test_index_exists(world):
    col = vectors._collection(vectors._client())
    assert col.count() > 0


def test_every_hit_passes_the_privacy_gate(world):
    """For every person and many queries, no vector hit is a record that person may not see."""
    by_id = {r["id"]: r for r in world.records}
    checked = 0
    for pid in world.people:
        org = world.org_of.get(pid)
        for q in QUERIES:
            hits = vectors.search(pid, org, q, k=25)
            assert hits is not None
            for rid, _ in hits:
                assert world.can_see(pid, by_id[rid]), f"{pid} got {rid}"
                checked += 1
    assert checked > 0


def test_private_and_restricted_records_reach_only_allowed_people(world):
    """Search for each private/restricted record using its own text: only allowed people may get it back."""
    secret = [r for r in world.records if r.get("visibility") in ("private", "restricted")]
    assert secret
    for r in secret[:15]:
        q = (r.get("body") or r.get("title") or "")[:200]
        for pid in world.people:
            hits = vectors.search(pid, world.org_of.get(pid), q, k=10) or []
            if r["id"] in {i for i, _ in hits}:
                assert world.can_see(pid, r), f"{pid} leaked {r['id']}"


def test_other_company_gets_nothing(world):
    by_id = {r["id"]: r for r in world.records}
    for pid in world.people:
        org = world.org_of.get(pid)
        for rid, _ in vectors.search(pid, org, "client meeting update", k=25) or []:
            assert by_id[rid].get("org") == org


def test_time_filter(world):
    first = min(r["at"] for r in world.records)
    pid = next(iter(world.people))
    hits = vectors.search(pid, world.org_of[pid], "update", until_ts=first, k=25) or []
    by_id = {r["id"]: r for r in world.records}
    assert all(by_id[i]["at"] <= first for i, _ in hits)


def test_embedder_is_offline_and_semantic():
    a, b, c = vectors.embed(["renewal of the contract", "contract renewals", "lunch menu on friday"])
    sim = lambda x, y: sum(p * q for p, q in zip(x, y))
    assert len(a) == vectors.DIM
    assert sim(a, b) > sim(a, c)


def test_unknown_visibility_fails_closed():
    m = vectors._meta({"id": "x", "org": "o", "visibility": "weird", "author": "p1", "at": "2026-11-01T10:00:00Z"})
    assert m["scope"] == "restricted" and not any(k.startswith("r_") for k in m)

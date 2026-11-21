"""Vector database: Chroma (local, persistent) for semantic search over records.

Design rules
- LOCAL ONLY: Chroma in persistent mode under the store folder (.assist_store/chroma). No server, no cloud.
- OFFLINE BY DEFAULT: the organisers' replay allows network access only to the model API, so embeddings must not
  download anything. The default embedder is built in: words, simple stems and character trigrams hashed into a
  512-dim vector (numpy). Set EMBED_MODEL=<sentence-transformers model> to use a real embedding model if it is
  installed and cached locally.
- PRIVACY INSIDE THE QUERY: every record is stored with its company, visibility scope and readers, and every search
  filters on them inside Chroma (pre-filtering). Results then pass World.visible() again (defence in depth).
- NEVER REQUIRED: if chromadb is missing or fails, callers fall back to keyword search.
"""
import hashlib
import os
import re
import shutil
import sys

from . import store
from .world import parse_ts, record_text

DIM = 512
COLLECTION = "records"
_WORD = re.compile(r"[a-z0-9]+")


# ------------------------------------------------------------------ embeddings (offline)
def _stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _features(text):
    words = _WORD.findall((text or "").lower())
    feats = []
    for w in words:
        feats.append(("w", w, 1.0))
        feats.append(("s", _stem(w), 0.8))
        if len(w) > 3:
            pad = f"#{w}#"
            feats += [("c", pad[i:i + 3], 0.3) for i in range(len(pad) - 2)]
    for a, b in zip(words, words[1:]):
        feats.append(("b", a + " " + b, 0.6))
    return feats


def hash_embed(text):
    import numpy as np
    v = np.zeros(DIM, dtype="float32")
    for kind, tok, wt in _features(text):
        h = int(hashlib.md5(f"{kind}:{tok}".encode()).hexdigest(), 16)
        v[h % DIM] += wt if (h >> 20) & 1 else -wt
    n = float(np.linalg.norm(v))
    return (v / n).tolist() if n else v.tolist()


_model = None


def embed(texts):
    """Embeddings for a list of texts. A real model only if EMBED_MODEL is set and available locally."""
    global _model
    name = os.getenv("EMBED_MODEL")
    if name:
        try:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(name, local_files_only=True)
            return [list(map(float, e)) for e in _model.encode(list(texts), normalize_embeddings=True)]
        except Exception as e:                     # not installed / not cached: stay offline
            print(f"[vectors] EMBED_MODEL unavailable ({e.__class__.__name__}); using built-in embedder", file=sys.stderr)
    return [hash_embed(t) for t in texts]


# ------------------------------------------------------------------ Chroma
def _client():
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    import chromadb
    from chromadb.config import Settings
    path = os.path.join(store.store_dir(), "chroma")
    return chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))


def _collection(client):
    return client.get_or_create_collection(COLLECTION, embedding_function=None, metadata={"hnsw:space": "cosine"})


def reset():
    shutil.rmtree(os.path.join(store.store_dir(), "chroma"), ignore_errors=True)


def _meta(r):
    """Metadata used to filter inside the vector query: company, visibility scope, readers, time."""
    org, vis = r.get("org") or "", r.get("visibility", "team")
    if vis == "team":
        scope = f"team:{org}"
    elif vis == "private":
        scope = f"private:{r.get('author')}"
    else:
        scope = "restricted"                       # fail closed: only listed readers below can match
    m = {"org": org, "scope": scope, "ts": int(parse_ts(r["at"]).timestamp()),
         "type": r.get("type") or "", "client": r.get("client") or "", "author": str(r.get("author") or "")}
    if vis == "restricted":
        for p in r.get("readers") or []:
            m[f"r_{p}"] = True
    return m


def index_records(records):
    """Add records (one ingested day) to the vector DB. Returns how many were added, or -1 if unavailable."""
    recs = [r for r in records if (r.get("body") or r.get("title") or r.get("subject"))]
    if not recs:
        return 0
    try:
        col = _collection(_client())
        for i in range(0, len(recs), 500):
            chunk = recs[i:i + 500]
            docs = [record_text(r)[:2000] for r in chunk]
            col.upsert(ids=[r["id"] for r in chunk], documents=docs, embeddings=embed(docs),
                       metadatas=[_meta(r) for r in chunk])
        return len(recs)
    except Exception as e:
        print(f"[vectors] indexing skipped: {e}", file=sys.stderr)
        return -1


def search(person, org, query, until_ts=None, k=8):
    """Semantic search, privacy-filtered INSIDE Chroma. Returns [(record_id, similarity)] or None if unavailable."""
    try:
        col = _collection(_client())
        if col.count() == 0:
            return []
        allowed = {"$or": [{"scope": f"team:{org}"}, {"scope": f"private:{person}"}, {f"r_{person}": True}]}
        clauses = [{"org": org}, allowed]
        if until_ts:
            clauses.append({"ts": {"$lte": int(parse_ts(until_ts).timestamp())}})
        res = col.query(query_embeddings=embed([query]), n_results=k, where={"$and": clauses},
                        include=["distances"])
        ids, dists = res["ids"][0], res["distances"][0]
        return [(i, 1.0 - d) for i, d in zip(ids, dists)]
    except Exception as e:
        print(f"[vectors] search unavailable: {e}", file=sys.stderr)
        return None

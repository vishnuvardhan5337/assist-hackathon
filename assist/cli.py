"""Work assistant that remembers. One command, four sub-commands (their harness adds the arguments):

  python3 main.py ingest --day N
  python3 main.py brief  --person P --date D
  python3 main.py ask    --person P --at ISO --ask "text"
  python3 main.py forget --person P --what "text"

Prints JSON only on stdout; logs go to stderr. Always exits 0 (errors become a safe JSON answer).
"""
import argparse
import json
import os
import sys
import traceback

from . import store


def cmd_ingest(day: int):
    con = store.connect(fresh=(day == 1))          # day 1 starts a fresh store
    from . import vectors
    if day == 1:
        vectors.reset()
    root = store.pack_root()
    path = os.path.join(root, "data", "days", f"day-{day:02d}.jsonl")
    n, day_records = 0, []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    con.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?)", (r["id"], day, r.get("at", ""), line.strip()))
                    day_records.append(r)
                    n += 1
    # feedback written on day N-1 reaches us now (never earlier)
    fpath = os.path.join(root, "data", "feedback", f"day-{day - 1:02d}.jsonl")
    nf = 0
    if os.path.isfile(fpath):
        with open(fpath, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    fb = json.loads(line)
                    con.execute("INSERT OR IGNORE INTO feedback VALUES (?,?,?,?)",
                                (fb.get("day", day - 1), fb.get("person"), fb.get("type"), line.strip()))
                    nf += 1
    store.set_meta(con, "ingested_day", max(day, store.get_meta(con, "ingested_day", 0) or 0))
    con.commit()
    # model fallback for facts the rules miss (skipped when no model is configured)
    from . import extract
    from .llm import Usage
    usage = Usage()
    try:
        found = extract.run(con, day, usage)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        found = 0
    indexed = vectors.index_records(day_records)   # vector DB (Chroma, local); -1 = unavailable
    return {"ok": True, "day": day, "records": n, "feedback": nf, "vectors": indexed, "model_facts": found,
            "model_tokens": usage.input_tokens + usage.output_tokens}


def cmd_forget(person: str, what: str):
    con = store.connect()
    con.execute("INSERT OR IGNORE INTO forgets VALUES (?,?,?)", (person, what, store.get_meta(con, "ingested_day", 0)))
    con.commit()
    return {"ok": True}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="assist")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest"); p.add_argument("--day", type=int, required=True)
    p = sub.add_parser("brief"); p.add_argument("--person", required=True); p.add_argument("--date", required=True)
    p = sub.add_parser("ask"); p.add_argument("--person", required=True); p.add_argument("--at", required=True)
    p.add_argument("--ask", required=True)
    p = sub.add_parser("forget"); p.add_argument("--person", required=True); p.add_argument("--what", required=True)
    a = ap.parse_args(argv)

    try:
        if a.cmd == "ingest":
            result = cmd_ingest(a.day)
        elif a.cmd == "forget":
            result = cmd_forget(a.person, a.what)
        else:
            from .world import World
            con = store.connect()
            world = World(con)
            if a.cmd == "brief":
                from . import brief
                result = brief.run(world, a.person, a.date)
            else:
                from . import ask
                result = ask.run(world, a.person, a.at, a.ask)
            con.commit()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        empty_usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "ms": 0}
        result = {"ok": False} if a.cmd in ("ingest", "forget") else (
            [] if a.cmd == "brief" else
            {"answer": {"text": "I have nothing on that.", "sources": []}, "matched": [], "clarify": None,
             "refused": None, "usage": empty_usage})
    print(json.dumps(result, ensure_ascii=False))
    return 0



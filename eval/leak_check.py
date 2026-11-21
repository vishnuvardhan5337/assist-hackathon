#!/usr/bin/env python3
"""Stricter privacy check than the official scorer.

The scorer only looks at cited record ids. This also looks at the TEXT of every output:
if any 6-word phrase from a record the person may not see appears in what we printed for
them, it is reported as a leak.

  python3 eval/leak_check.py --run runs/mine          (uses $R3_PACK for the data)
"""
import argparse
import glob
import json
import os
import re
import sys


def shingles(text, n=6):
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    root = os.environ.get("R3_PACK", ".")
    recs = []
    for fn in sorted(glob.glob(os.path.join(root, "data", "days", "day-*.jsonl"))):
        recs += [json.loads(l) for l in open(fn, encoding="utf-8") if l.strip()]
    org_of = {}
    for org, v in json.load(open(os.path.join(root, "data", "people.json"))).items():
        for p in v["people"]:
            org_of[p["id"]] = org

    def can_see(person, r):
        if r.get("org") != org_of.get(person):
            return False
        vis = r.get("visibility", "team")
        return vis == "team" or (vis == "private" and r.get("author") == person) or \
            (vis == "restricted" and person in (r.get("readers") or []))

    tasks = {}
    for fn in glob.glob(os.path.join(root, "data", "tasks", "day-*.json")):
        d = int(re.search(r"day-(\d+)", fn)[1])
        t = json.load(open(fn))
        for q in t.get("asks", []):
            tasks[(d, "ask-" + q["qid"])] = q["person"]
        for b in t.get("briefs", []):
            tasks[(d, f"brief-{b['person']}-{b['date']}")] = b["person"]

    leaks = 0
    for (d, name), person in tasks.items():
        path = os.path.join(a.run, f"day-{d:02d}", name + ".json")
        if not os.path.isfile(path):
            continue
        out = open(path, encoding="utf-8").read()
        sh = shingles(out)
        for r in recs:
            if can_see(person, r):
                continue
            hit = sh & shingles(r.get("body") or "")
            if hit:
                leaks += 1
                print(f"LEAK day {d} {name} (for {person}): text from {r['id']} ({r.get('org')}/{r.get('visibility')}): "
                      f"\"{next(iter(hit))}\"")
    print("no text leaks" if not leaks else f"{leaks} text leaks", file=sys.stderr)
    sys.exit(1 if leaks else 0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Validate every output of a harness run against the output contract and integrity rules.

  python3 eval/validate_outputs.py --run runs/mine          (uses $R3_PACK for the data)

Contract (from DATA_FORMAT.md)
  brief -> a JSON list; each item has exactly: meeting_id, status, changed, promises, open_these,
           question_to_ask, usage. status is "brief" or "nothing_new"; nothing_new has empty lists
           and question_to_ask null.
  ask   -> one object with answer, matched, clarify, refused, usage; exactly one of
           answer / clarify / refused is not null.
  usage -> input_tokens, output_tokens, cost_usd, ms: numbers >= 0; input_tokens <= 8000 (brief) / 4000 (ask).

Integrity
  every cited id exists, was ingested by that day, and is visible to the person (company, private, restricted);
  promise owners are person ids, due dates are real YYYY-MM-DD dates; no empty text lines.
Exit code 1 if anything fails. Each problem is printed with the file it came from.
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import date

BRIEF_KEYS = {"meeting_id", "status", "changed", "promises", "open_these", "question_to_ask", "usage"}
ASK_KEYS = {"answer", "matched", "clarify", "refused", "usage"}
USAGE_KEYS = {"input_tokens", "output_tokens", "cost_usd", "ms"}
LIMIT = {"brief": 8000, "ask": 4000}
PERSON = re.compile(r"^[pq]\d+$")


class Data:
    def __init__(self, root):
        self.rec, self.org_of = {}, {}
        for fn in sorted(glob.glob(os.path.join(root, "data", "days", "day-*.jsonl"))):
            day = int(re.search(r"day-(\d+)", fn)[1])
            for line in open(fn, encoding="utf-8"):
                if line.strip():
                    r = json.loads(line)
                    r["_day"] = day
                    self.rec[r["id"]] = r
        for org, v in json.load(open(os.path.join(root, "data", "people.json"), encoding="utf-8")).items():
            for p in v.get("people", []):
                self.org_of[p["id"]] = org
        self.tasks = {}
        for fn in glob.glob(os.path.join(root, "data", "tasks", "day-*.json")):
            d = int(re.search(r"day-(\d+)", fn)[1])
            t = json.load(open(fn, encoding="utf-8"))
            for b in t.get("briefs", []):
                self.tasks[(d, f"brief-{b['person']}-{b['date']}")] = ("brief", b["person"])
            for q in t.get("asks", []):
                self.tasks[(d, f"ask-{q['qid']}")] = ("ask", q["person"])

    def visible(self, rid, person):
        r = self.rec.get(rid)
        if not r or r.get("org") != self.org_of.get(person):
            return False
        vis = r.get("visibility", "team")
        return vis == "team" or (vis == "private" and r.get("author") == person) or \
            (vis == "restricted" and person in (r.get("readers") or []))


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0


def check_usage(u, kind, where, errs):
    if not isinstance(u, dict) or set(u) != USAGE_KEYS:
        errs.append(f"{where}: usage must have exactly {sorted(USAGE_KEYS)}, got {u!r:.80}")
        return
    for k in USAGE_KEYS:
        if not _is_num(u[k]):
            errs.append(f"{where}: usage.{k} must be a number >= 0, got {u[k]!r}")
    if _is_num(u["input_tokens"]) and u["input_tokens"] > LIMIT[kind]:
        errs.append(f"{where}: input_tokens {u['input_tokens']} over the {LIMIT[kind]} limit")


def check_line(line, where, errs, need_sources=True):
    if not isinstance(line, dict) or set(line) != {"text", "sources"}:
        errs.append(f"{where}: must be {{text, sources}}, got {line!r:.80}")
        return []
    if not isinstance(line["text"], str) or not line["text"].strip():
        errs.append(f"{where}: empty text")
    if not isinstance(line["sources"], list) or not all(isinstance(s, str) for s in line["sources"]):
        errs.append(f"{where}: sources must be a list of ids")
        return []
    if need_sources and not line["sources"]:
        errs.append(f"{where}: a brief line with no sources")
    return line["sources"]


def check_brief(obj, where, errs):
    """Returns every cited id."""
    cited = []
    if not isinstance(obj, list):
        errs.append(f"{where}: a brief must be a JSON list")
        return cited
    seen = set()
    for i, b in enumerate(obj):
        w = f"{where}[{i}]"
        if not isinstance(b, dict) or set(b) != BRIEF_KEYS:
            errs.append(f"{w}: keys must be exactly {sorted(BRIEF_KEYS)}, got {sorted(b) if isinstance(b, dict) else b!r}")
            continue
        if b["meeting_id"] in seen:
            errs.append(f"{w}: meeting {b['meeting_id']} listed twice")
        seen.add(b["meeting_id"])
        if b["status"] not in ("brief", "nothing_new"):
            errs.append(f"{w}: status must be brief or nothing_new, got {b['status']!r}")
        for k in ("changed", "promises", "open_these"):
            if not isinstance(b[k], list):
                errs.append(f"{w}: {k} must be a list")
        if b["status"] == "nothing_new":
            if b["changed"] or b["promises"] or b["open_these"] or b["question_to_ask"] is not None:
                errs.append(f"{w}: nothing_new must have empty lists and question_to_ask null")
        if b["status"] == "brief" and not b["changed"]:
            errs.append(f"{w}: status brief but no changes")
        for j, c in enumerate(b["changed"] if isinstance(b["changed"], list) else []):
            cited += check_line(c, f"{w}.changed[{j}]", errs)
        for j, p in enumerate(b["promises"] if isinstance(b["promises"], list) else []):
            pw = f"{w}.promises[{j}]"
            if not isinstance(p, dict) or set(p) != {"owner", "what", "due", "sources"}:
                errs.append(f"{pw}: keys must be owner, what, due, sources")
                continue
            if not PERSON.match(str(p["owner"])):
                errs.append(f"{pw}: owner must be a person id, got {p['owner']!r}")
            try:
                date.fromisoformat(str(p["due"]))
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(p["due"])):
                    raise ValueError
            except ValueError:
                errs.append(f"{pw}: due must be YYYY-MM-DD, got {p['due']!r}")
            if not str(p["what"]).strip():
                errs.append(f"{pw}: empty what")
            if not p["sources"]:
                errs.append(f"{pw}: a promise with no sources")
            cited += list(p["sources"])
        for j, o in enumerate(b["open_these"] if isinstance(b["open_these"], list) else []):
            if not isinstance(o, dict) or set(o) != {"record", "why"} or not str(o.get("why", "")).strip():
                errs.append(f"{w}.open_these[{j}]: must be {{record, why}} with a reason")
            else:
                cited.append(o["record"])
        if b["question_to_ask"] is not None:
            cited += check_line(b["question_to_ask"], f"{w}.question_to_ask", errs)
        check_usage(b["usage"], "brief", w, errs)
    return cited


def check_ask(a, where, errs):
    cited = []
    if not isinstance(a, dict) or set(a) != ASK_KEYS:
        errs.append(f"{where}: keys must be exactly {sorted(ASK_KEYS)}, got {sorted(a) if isinstance(a, dict) else a!r}")
        return cited
    filled = [k for k in ("answer", "clarify", "refused") if a[k] is not None]
    if len(filled) != 1:
        errs.append(f"{where}: exactly one of answer/clarify/refused must be filled, got {filled}")
    if a["answer"] is not None:
        cited += check_line(a["answer"], f"{where}.answer", errs, need_sources=False)
    if a["clarify"] is not None:
        c = a["clarify"]
        if not isinstance(c, dict) or set(c) != {"question", "options"} or not str(c.get("question", "")).strip():
            errs.append(f"{where}.clarify: must be {{question, options}}")
        elif not isinstance(c["options"], list) or len(c["options"]) < 2 or \
                not all(isinstance(o, dict) and set(o) == {"id", "text"} for o in c["options"]):
            errs.append(f"{where}.clarify.options: need 2+ items of {{id, text}}")
    if a["refused"] is not None:
        if not isinstance(a["refused"], dict) or set(a["refused"]) != {"reason"} or not str(a["refused"]["reason"]).strip():
            errs.append(f"{where}.refused: must be {{reason}}")
    if not isinstance(a["matched"], list):
        errs.append(f"{where}.matched must be a list")
    else:
        for j, m in enumerate(a["matched"]):
            if not isinstance(m, dict) or set(m) != {"phrase", "id", "sources"} or not isinstance(m.get("sources"), list):
                errs.append(f"{where}.matched[{j}]: must be {{phrase, id, sources}}")
            else:
                cited += m["sources"]
    check_usage(a["usage"], "ask", where, errs)
    return cited


def check_cited(cited, data, day, person, where, errs):
    for rid in set(cited):
        r = data.rec.get(rid)
        if r is None:
            errs.append(f"{where}: cites {rid!r}, which is not in the data")
        elif r["_day"] > day:
            errs.append(f"{where}: cites {rid} from day {r['_day']} before it was ingested (day {day})")
        elif not data.visible(rid, person):
            errs.append(f"{where}: PRIVACY: cites {rid} that {person} may not see")


def validate_run(run_dir, root):
    data = Data(root)
    errs, n = [], 0
    for (day, name), (kind, person) in sorted(data.tasks.items()):
        d = os.path.join(run_dir, f"day-{day:02d}")
        if not os.path.isdir(d):
            continue
        path = os.path.join(d, name + ".json")
        if not os.path.isfile(path):
            errs.append(f"{name} (day {day}): output missing or not valid JSON")
            continue
        try:
            obj = json.load(open(path, encoding="utf-8"))
        except ValueError:
            errs.append(f"{name}: not valid JSON")
            continue
        n += 1
        where = f"day {day} {name}"
        cited = check_brief(obj, where, errs) if kind == "brief" else check_ask(obj, where, errs)
        check_cited(cited, data, day, person, where, errs)
    log = os.path.join(run_dir, "log.jsonl")
    if os.path.isfile(log):
        for line in open(log, encoding="utf-8"):
            e = json.loads(line)
            if e.get("exit") != 0 or e.get("valid") is False:
                errs.append(f"harness: {e.get('kind')} {e.get('args')} exit={e.get('exit')} valid={e.get('valid')}")
    return n, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    root = os.environ.get("R3_PACK", ".")
    n, errs = validate_run(a.run, root)
    for e in errs:
        print("FAIL", e)
    print(f"{n} outputs checked, {len(errs)} problems", file=sys.stderr)
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()

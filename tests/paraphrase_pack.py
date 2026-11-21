#!/usr/bin/env python3
"""Overfitting stress test: days 1-5 with the SAME records, ids and official answer keys, but the messages,
feedback and questions REWORDED the way real people write. If the program only learned the exact phrasings
of days 1-5, its score drops here. The size of the drop is an honest measure of brittleness.

  python3 tests/paraphrase_pack.py --out /tmp/para_pack
  R3_PACK=/tmp/para_pack python3 /tmp/para_pack/tools/run_days.py --cmd "python3 main.py" --days 1-5 --out runs/para
  R3_PACK=/tmp/para_pack python3 /tmp/para_pack/tools/score.py --run runs/para --days 1-5

Each matching message gets one of several rewordings, picked by a hash of its id (repeatable).
Meaning is kept, so the official keys (keywords, owners, due dates, sources) stay correct.
"""
import argparse
import hashlib
import json
import os
import re
import shutil


def pick(rid, options):
    return options[int(hashlib.md5(rid.encode()).hexdigest(), 16) % len(options)]


# ---- messages: (pattern on body, list of rewrites using named groups) ----
RULES = [
    # 'the X kal tak bhej dunga'  -> due tomorrow
    (r"^the (?P<x>.+?) kal tak bhej dunga$", [
        "I'll send the {x} tomorrow.",
        "the {x} kal tak share kar dunga",
        "Will get the {x} to you by tomorrow.",
        "the {x} kal tak pakka bhej dungi"]),
    # 'the X next Monday tak share kar dungi/dunga, pakka.' -> due next Monday
    (r"^the (?P<x>.+?) next Monday tak share kar dungi/dunga, pakka\.$", [
        "I'll share the {x} by next Monday.",
        "the {x} Monday tak bhej dunga, pakka.",
        "Will send the {x} by Monday.",
        "the {x} next Monday tak de dungi"]),
    # 'Will share the X by EOD today.' -> due today
    (r"^Will share the (?P<x>.+?) by EOD today\.$", [
        "I'll send the {x} by end of day.",
        "Will send over the {x} by EOD.",
        "the {x} aaj shaam tak bhej dunga",
        "We'll share the {x} by EOD today."]),
    # 'Hi team, I will send the X by Thursday.'
    (r"^Hi team, I will send the (?P<x>.+?) by (?P<d>\w+day)\.$", [
        "I'll share the {x} by {d}.",
        "Will send the {x} by {d}, team."]),
    # 'Sharing the X as promised. Let me know if anything is missing.'
    (r"^Sharing the (?P<x>.+?) as promised\. Let me know if anything is missing\.$", [
        "Here is the {x}, as promised.",
        "Attaching the {x} as promised.",
        "Sending the {x} as promised. Shout if anything is missing.",
        "Here's the {x} as agreed."]),
    # 'From this week Arjun takes over Northbeam billing from Kavya. ...'
    (r"^From this week (?P<a>\w+) takes over (?P<c>\w+) (?P<w>\w+) from (?P<b>\w+)\.(?P<rest>.*)$", [
        "{a} will now handle {c} {w}, not {b}.{rest}",
        "Heads up: {a} has taken over {c} {w} from {b}.{rest}",
        "{a} now owns {c} {w}.{rest}"]),
    # 'Hi Meera, we need to move the launch to 14 Nov. ...'
    (r"^Hi (?P<n>\w+), we need to move the launch to (?P<d>\d+ \w+)\.(?P<rest>.*)$", [
        "Hi {n}, the launch has to be pushed to {d}.{rest}",
        "Hi {n}, we are moving the launch to {d}.{rest}",
        "Hi {n}, please note we have postponed the launch to {d}.{rest}"]),
]

NOTE_DECISION = (r"^Decision: ", ["Decision: ", "Decided: ", "Agreed: "])

# ---- variant B: a HELD-OUT set of rewordings, written before any fix and scored once, to check
#      that fixes made for variant A are general and not tuned to A's exact sentences ----
RULES_B = [
    (r"^the (?P<x>.+?) kal tak bhej dunga$", [
        "I'm going to send the {x} tomorrow.",
        "the {x} kal tak bhej deta hoon",
        "Tomorrow I will share the {x}.",
        "We'll deliver the {x} tomorrow."]),
    (r"^the (?P<x>.+?) next Monday tak share kar dungi/dunga, pakka\.$", [
        "Expect the {x} from me next Monday.",
        "I'll get the {x} over to you on Monday.",
        "the {x} agle Monday tak share kar dunga"]),
    (r"^Will share the (?P<x>.+?) by EOD today\.$", [
        "the {x} will be with you by end of day.",
        "I will share the {x} later today.",
        "Sending the {x} tonight."]),
    (r"^Hi team, I will send the (?P<x>.+?) by (?P<d>\w+day)\.$", [
        "Team, you'll have the {x} by {d}."]),
    (r"^Sharing the (?P<x>.+?) as promised\. Let me know if anything is missing\.$", [
        "As promised, here is the {x}.",
        "The {x} is attached, as promised.",
        "Delivered the {x} as promised."]),
    (r"^From this week (?P<a>\w+) takes over (?P<c>\w+) (?P<w>\w+) from (?P<b>\w+)\.(?P<rest>.*)$", [
        "{b} is handing {c} {w} over to {a} this week.{rest}",
        "Going forward {a} is responsible for {c} {w}.{rest}"]),
    (r"^Hi (?P<n>\w+), we need to move the launch to (?P<d>\d+ \w+)\.(?P<rest>.*)$", [
        "Hi {n}, launch is now {d}.{rest}",
        "Hi {n}, we will launch on {d} instead.{rest}"]),
]
NOTE_DECISION_B = (r"^Decision: ", ["We decided: ", "Outcome: ", "Final call: "])
FEEDBACK_B = {
    "Skip newsletters in my briefs.": "I don't want newsletters in my briefs.",
    "No, Arjun owns billing now, not Kavya.": "Wrong: billing moved to Arjun, Kavya no longer has it.",
    "Too long. Max 5 changes per brief for me, most important first.": "Please keep my briefs to five changes, top ones first.",
}
QUESTIONS_B = [
    (r"^When is the (?P<t>.+?) for (?P<c>.+?) due\?$", "By when should the {t} for {c} be done?"),
    (r"^Who owns the (?P<c>NB|NB Retail|CL|Cobalt|Northbeam|London account) (?P<t>.+?)\?$", "Who's on the {c} {t}?"),
    (r"^What's the status of (?P<t>.+?) \((?P<c>.+?)\)\?$", "Where are we with {t} for {c}?"),
    (r"^Who handles billing for (?P<c>.+?)\?$", "Who does billing for {c}?"),
    (r"^Who owns billing for (?P<c>.+?)\?$", "Who does {c} billing now?"),
]
VARIANT = "a"

FEEDBACK = {
    "Skip newsletters in my briefs.": "Please drop newsletters from my briefs.",
    "No, Arjun owns billing now, not Kavya.": "No, Arjun handles billing now, not Kavya.",
    "Too long. Max 5 changes per brief for me, most important first.":
        "Too long. At most 5 changes per brief please, most important first.",
}

# questions: (pattern, rewrite) applied to tasks/day-NN.json asks
QUESTIONS = [
    (r"^When is the (?P<t>.+?) for (?P<c>.+?) due\?$", "What's the deadline for the {t} ({c})?"),
    (r"^Who owns the (?P<c>NB|NB Retail|CL|Cobalt|Northbeam|London account) (?P<t>.+?)\?$", "Who is responsible for the {c} {t}?"),
    (r"^What's the status of (?P<t>.+?) \((?P<c>.+?)\)\?$", "Is the {t} for {c} done yet?"),
    (r"^Who handles billing for (?P<c>.+?)\?$", "Who looks after billing for {c}?"),
    (r"^Who owns billing for (?P<c>.+?)\?$", "Who is in charge of billing for {c}?"),
]


def reword_body(r):
    body = r.get("body") or ""
    rules, note = (RULES_B, NOTE_DECISION_B) if VARIANT == "b" else (RULES, NOTE_DECISION)
    if r.get("type") in ("chat", "mail"):
        for pat, outs in rules:
            m = re.match(pat, body, re.S)
            if m:
                return pick(r["id"], outs).format(**m.groupdict())
    if r.get("type") == "note" and re.search(note[0], body, re.M):
        return re.sub(note[0], pick(r["id"], note[1]), body, flags=re.M)
    return body


def build(src, out, variant="a"):
    global VARIANT
    VARIANT = variant
    if os.path.exists(out):
        shutil.rmtree(out)
    shutil.copytree(src, out, ignore=shutil.ignore_patterns("runs", "__pycache__"))
    d = os.path.join(out, "data")
    changed = 0
    for fn in sorted(os.listdir(os.path.join(d, "days"))):
        path = os.path.join(d, "days", fn)
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        for r in rows:
            new = reword_body(r)
            if new != r.get("body"):
                r["body"] = new
                changed += 1
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""))
    for fn in sorted(os.listdir(os.path.join(d, "feedback"))):
        path = os.path.join(d, "feedback", fn)
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        for fb in rows:
            fb["words"] = (FEEDBACK_B if VARIANT == "b" else FEEDBACK).get(fb.get("words"), fb.get("words"))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + ("\n" if rows else ""))
    for fn in sorted(os.listdir(os.path.join(d, "tasks"))):
        path = os.path.join(d, "tasks", fn)
        t = json.load(open(path, encoding="utf-8"))
        for q in t.get("asks", []):
            for pat, rep in (QUESTIONS_B if VARIANT == "b" else QUESTIONS):
                m = re.match(pat, q["ask"])
                if m:
                    q["ask"] = rep.format(**m.groupdict())
                    break
        json.dump(t, open(path, "w", encoding="utf-8"), indent=1)
    # the answer keys also store the question text; keep them in step (scoring ignores it)
    return out, changed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--pack", default=os.environ.get("R3_PACK", "."))
    ap.add_argument("--variant", choices=["a", "b"], default="a", help="b = held-out rewordings")
    a = ap.parse_args()
    out, n = build(a.pack, a.out, a.variant)
    print(f"{out}: {n} messages reworded")

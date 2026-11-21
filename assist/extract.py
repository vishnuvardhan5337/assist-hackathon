"""Model fallback for facts the rules miss. Runs once per day at ingest; results are stored per record.

Only messages that LOOK like an ownership change, a date move or a promise, and that no rule in facts.py
already understood, are sent: a few per day, in small batches, wrapped as untrusted data.
Every fact the model returns is checked by code before it is kept:
  - its record id must be one we sent;
  - its quote must really appear in that record's text (no invented facts);
  - the person must exist in the record's company; the date must parse.
Facts are stored against their record, so the privacy gate still decides who can use them.
"""
import json
import re

from . import facts as F
from . import llm
from .world import World, local_date, parse_day_text

CUES = re.compile(r"(?i)\b(over to|hand(?:ing|s|ed)? over|responsible|handl\w*|owner|owns|in charge|point of contact|"
                  r"take[sn]? over|took over|moved?|moving|push\w*|postpon\w*|resched\w*|launch\w*|go-live|deadline|"
                  r"will|'ll|tak|tomorrow|kal|parso|expect|deliver\w*|from me)\b")
PROMPT = """These are work messages. Find ONLY these three kinds of facts:
- "owner": someone now owns/handles a topic for a client (e.g. billing). "person" = the NEW owner (who has it
  AFTER the change), "previous" = who had it before (or null). Never put the person handing it over in "person".
- "date": a client milestone (e.g. launch) moved to a new date.
- "promise": the AUTHOR commits to deliver something; give what and when (a date like 14 Nov, or a word like tomorrow, Friday, EOD).
Copy a short exact quote (3-12 words) from the record for each fact. Skip anything else, including questions.
Reply {"facts": [{"record": id, "kind": "owner"|"date"|"promise", "topic": short topic, "person": name or null, "previous": name or null,
"when": date or word or null, "what": thing promised or null, "quote": exact words}]}.

Records:
"""


GIVER_TMPL = (r"(?:\b(?:from|by)\s+{n}\b|\b{n}\s+(?:is\s+|was\s+)?(?:handing|hands|handed|passing|passes|passed|"
              r"giving|gives|gave|transferring)\b|\b{n}\s+(?:no longer|is no longer|will no longer|won't|will not)\b|\bnot\s+{n}\b)")
GIVER = re.compile(r"\x00")                        # placeholder: the real test is GIVER_TMPL with the person's name


def clean_topic(world, topic):
    """'billing for nb' / 'Northbeam billing' -> 'billing'."""
    t = F.norm_attr(topic)
    t = re.sub(r"\bfor\b.*$", "", t).strip()
    for c in world.clients:
        for w in [c["id"]] + c.get("name", "").lower().split():
            t = re.sub(r"\b" + re.escape(w.lower()) + r"\b", "", t)
    t = re.sub(r"\b[a-z]{2}\b(?=\s|$)", lambda m: "" if m[0] in ("nb", "cl", "ap") else m[0], t)
    return re.sub(r"\s+", " ", t).strip()


def rule_hits(r, world):
    body = r.get("body") or ""
    if F.find_promise(r, world) or F.DELIVERED.search(body) or F.DECISION.search(body):
        return True
    return any(rx.search(body) for rx in (F.TAKEOVER, F.NOW_OWNS, F.MOVE_DATE))


def candidates(world, day):
    out = []
    for r in world.records:
        if r["_day"] != day or r.get("type") not in ("chat", "mail", "note"):
            continue
        author = str(r.get("author") or "")
        if author == "system" or (author.startswith("ext:") and not r.get("client")):
            continue                               # bulk mail and notifications
        body = (r.get("body") or "").strip()
        if not body or body.endswith("?") or len(body) > 600 or not CUES.search(body):
            continue
        if rule_hits(r, world):
            continue
        out.append(r)
    return out


def check(fact, by_id, world):
    """Code-side validation of one model fact. Returns a stored fact or None."""
    r = by_id.get(fact.get("record"))
    if not r:
        return None
    quote = " ".join(str(fact.get("quote") or "").split())
    body = " ".join((r.get("body") or "").split())
    if len(quote.split()) < 3 or quote.lower() not in body.lower():
        return None                                # not in the record: invented
    kind = fact.get("kind")
    today = local_date(r["at"], world.zone(r.get("author")))
    if kind == "owner":
        attr = clean_topic(world, str(fact.get("topic") or ""))
        name = str(fact.get("person") or "").strip()
        prev = str(fact.get("previous") or "").strip()
        pid = F.resolve_person(world, name, r.get("org"), hint_attr=attr)
        if not attr or len(attr) > 25 or not pid:
            return None
        if prev and (prev.lower() == name.lower() or F.resolve_person(world, prev, r.get("org"), hint_attr=attr) == pid):
            return None                            # new owner = previous owner: contradiction
        first = world.first(pid)
        if GIVER.search(body.replace(first, "\x00")) and re.search(GIVER_TMPL.format(n=re.escape(first)), body, re.I):
            return None                            # the person is the one handing it over / losing it
        return {"kind": "owner", "attr": attr, "value": pid}
    if kind == "date":
        attr = clean_topic(world, str(fact.get("topic") or ""))
        d = F.due_from(str(fact.get("when") or ""), today) or parse_day_text(str(fact.get("when") or ""), today)
        if not attr or len(attr) > 25 or not d:
            return None
        return {"kind": "date", "attr": attr, "value": d.isoformat()}
    if kind == "promise":
        if not F.PERSON_ID.match(str(r.get("author") or "")):
            return None
        what = str(fact.get("what") or "").strip(" .,")
        d = F.due_from(str(fact.get("when") or ""), today)
        if len(what) < 3 or len(what) > 80 or not d:
            return None
        if not what.lower().startswith(("the ", "a ", "an ", "our ", "my ")):
            what = "the " + what
        return {"kind": "promise", "what": what, "due": d.isoformat()}
    return None


def run(con, day, usage):
    """Extract facts for day `day`'s records. Returns how many facts were kept."""
    if not llm.enabled():
        return 0
    world = World(con)
    cands = candidates(world, day)
    kept = 0
    for i in range(0, len(cands), 15):
        batch = cands[i:i + 15]
        by_id = {r["id"]: r for r in batch}
        data = llm.call_json(con, PROMPT + "\n".join(llm.wrap(r) for r in batch), usage, budget=3500, max_out=900)
        for f in (data or {}).get("facts", []) if isinstance(data, dict) else []:
            ok = check(f, by_id, world) if isinstance(f, dict) else None
            if ok:
                con.execute("INSERT OR REPLACE INTO llm_facts VALUES (?,?,?)",
                            (f["record"], ok["kind"] + ":" + (ok.get("attr") or ok.get("what", "")), json.dumps(ok)))
                kept += 1
    con.commit()
    return kept

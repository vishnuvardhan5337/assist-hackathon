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
                  r"will|'ll|tak|tomorrow|kal|parso|aaj|dunga|dungi|denge|expect|deliver\w*|from me|"
                  r"decid\w*|agreed|outcome|final call|dropp\w*|scrap\w*|cut|approv\w*|signed off|going with|"
                  r"confirm\w*|cancel\w*|parked|deferred|no longer|instead|not)\b")
PROMPT = """These are work messages (English, Hindi or Hinglish). Find ONLY these four kinds of facts:
- "owner": someone now owns/handles a topic for a client (e.g. billing). "person" = the NEW owner (who has it
  AFTER the change), "previous" = who had it before (or null). Never put the person handing it over in "person".
- "date": a client milestone (e.g. launch) moved to a new date.
- "promise": the AUTHOR commits to deliver something; give what and when (a date like 14 Nov, or a word like tomorrow, Friday, EOD).
  Hinglish counts: "kal tak bhej dungi" = I will send it by tomorrow; "Friday tak kar dunga" = I will do it by Friday.
- "decision": a decision or outcome agreed for the client (something approved, dropped, chosen, cancelled, moved out of
  scope). "what" = the decision in a few plain words, using words from the record and NAMING THE THING decided
  (e.g. "Store locator page dropped from phase 1", never just "Dropped from phase 1").
  For "what" in a promise give only the thing (e.g. "the checkout copy"), not the verb.
A correction such as "No, Arjun handles billing now, not Kavya" is an "owner" fact: person = Arjun, previous = Kavya.
Copy a short exact quote (3-12 words) from the record for each fact. Skip anything else, including questions.
Reply {"facts": [{"record": id, "kind": "owner"|"date"|"promise", "topic": short topic, "person": name or null, "previous": name or null,
"when": date or word or null, "what": thing promised or decision or null, "quote": exact words}]}.

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
        limit = 2000 if r.get("type") == "note" else 600     # meeting notes are long and hold several facts
        if not body or body.endswith("?") or len(body) > limit or not CUES.search(body):
            continue
        if r.get("type") != "note" and rule_hits(r, world):
            continue                               # short message already read by a rule (notes hold several facts)
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
    if kind == "decision":
        what = " ".join(str(fact.get("what") or "").split()).strip(" .,")
        if r.get("type") not in ("note", "chat", "mail") or not r.get("client") or not 3 <= len(what) <= 120:
            return None
        cw = [w for w in re.findall(r"[a-z0-9]+", what.lower()) if len(w) >= 4]
        if not cw or sum(w in body.lower() for w in cw) / len(cw) < 0.6:
            return None                            # decision text must come from the record, not be invented
        # 'Dropped from phase 1' names no thing: use the record's own words (the quote) instead
        if re.match(r"(?i)(dropped|approved|cancel\w*|moved|chosen|agreed|scrapped|parked|deferred|signed|confirmed|"
                    r"removed|cut|added|kept|out of|is |was |will )", what) and \
                set(re.findall(r"[a-z0-9]+", what.lower())) <= set(re.findall(r"[a-z0-9]+", quote.lower())):
            what = re.sub(r"(?i)^(outcome|decision|decided|agreed|final call|resolved)\s*[:\-]\s*", "", quote).strip(" .,")
            what = re.sub(r"(?i)\b(is|was|has been|will be)\s+(?=(dropped|approved|cancel|moved|scrapped|parked|deferred|removed|cut))", "", what)
            if len(what) > 120:
                return None
        return {"kind": "decision", "what": what[0].upper() + what[1:]}
    if kind == "promise":
        if r.get("type") not in ("chat", "mail") or not F.PERSON_ID.match(str(r.get("author") or "")):
            return None                            # same as the rules: promises are made in chats and mails
        what = str(fact.get("what") or "").strip(" .,")
        what = re.sub(r"(?i)^(?:the\s+)?(?:send|share|deliver|get|forward|bhej\w*|share kar\w*)\s+(?:over\s+)?", "", what)
        what = re.sub(r"(?i)\s+(?:will be with you|over to you|to you)$", "", what).strip(" .,")
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
    batches, cur, size = [], [], 0
    for r in cands:                                # batches by length, so every prompt stays under budget
        w = llm.wrap(r, 2000)
        if cur and (size + len(w) > 9000 or len(cur) >= 15):
            batches.append(cur); cur, size = [], 0
        cur.append(r); size += len(w)
    if cur:
        batches.append(cur)
    for batch in batches:
        by_id = {r["id"]: r for r in batch}
        data = llm.call_json(con, PROMPT + "\n".join(llm.wrap(r, 2000) for r in batch), usage, budget=3500, max_out=900)
        for f in (data or {}).get("facts", []) if isinstance(data, dict) else []:
            ok = check(f, by_id, world) if isinstance(f, dict) else None
            if ok:
                con.execute("INSERT OR REPLACE INTO llm_facts VALUES (?,?,?)",
                            (f["record"], ok["kind"] + ":" + (ok.get("attr") or ok.get("what", "")), json.dumps(ok)))
                kept += 1
    con.commit()
    return kept

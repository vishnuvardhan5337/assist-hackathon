"""Turn visible records into structured facts. Plain code, no model.

  tasks(recs)            current state of every task + its change history
  client_facts(...)      per-client facts such as 'launch' date or 'billing' owner, with history
  decisions(recs)        'Decision: ...' lines from meeting notes
  deliveries(recs)       'Sharing the X as promised' messages
  promises(recs, world)  open promises with owner + due date
"""
import re
from datetime import timedelta

from .world import (WEEKDAYS, fmt_day, local_date, next_weekday, parse_day_text, record_text)

PERSON_ID = re.compile(r"^[pq]\d+$")


# ------------------------------------------------------------------ tasks
def tasks(recs):
    """task_id -> {id, title, client, owner, due, status, history:[change records], src:{field: record}}.

    State comes from 'created' and real changes only. Comments are ignored for state,
    because their owner/due/status fields are sometimes stale.
    """
    out = {}
    for r in recs:
        if r.get("type") != "task" or not r.get("task_id"):
            continue
        tid = r["task_id"]
        ch = r.get("change") or {}
        field = ch.get("field")
        t = out.setdefault(tid, {"id": tid, "title": r.get("title", tid), "client": r.get("client"),
                                 "owner": None, "due": None, "status": None, "history": [], "src": {},
                                 "author": r.get("author")})
        t["title"] = r.get("title") or t["title"]
        if field == "created" or t["status"] is None:
            for k in ("owner", "due", "status"):
                if r.get(k) is not None and (field == "created" or t[k] is None):
                    t[k] = r.get(k)
                    t["src"].setdefault(k, r)
        if field in ("owner", "due", "status"):
            t[field] = ch.get("to", r.get(field))
            t["src"][field] = r
            t["history"].append(r)
    return out


# ------------------------------------------------------------------ client facts
FIELD_LINE = re.compile(r"^\s*([A-Z][A-Za-z ]{2,30}):\s*(.+?)\s*$")
TAKEOVER = re.compile(
    r"(?P<new>[A-Z][a-z]+(?: [A-Z][a-z]+)?) (?:takes|will take|is taking|has taken|took) over "
    r"(?:the )?(?P<rest>[A-Za-z ]+?) from (?P<old>[A-Z][a-z]+(?: [A-Z][a-z]+)?)\b")
NOW_OWNS = re.compile(
    r"(?P<new>[A-Z][a-z]+(?: [A-Z][a-z]+)?) (?:now owns|now handles|will (?:now )?(?:own|handle)|is now (?:handling|responsible for)) "
    r"(?:the )?(?P<rest>[A-Za-z ]+?)(?:[.,!]|$| from| going| now)")
MOVE_DATE = re.compile(
    r"(?:move|moving|moved|push|pushing|pushed|postpone|postponing|postponed|shift|shifting|shifted) (?:the )?"
    r"(?P<thing>[a-z][a-z\- ]{1,25}?) (?:date )?(?:to|till|until) (?P<date>\d{1,2}(?:st|nd|rd|th)? [A-Z][a-z]{2,8}|[A-Z][a-z]{2,8} \d{1,2}|\d{4}-\d{2}-\d{2})",
    re.I)


def norm_attr(key: str) -> str:
    """'Launch date' -> 'launch', 'Billing contact' -> 'billing', 'go-live' -> 'go-live'."""
    k = key.lower().strip()
    k = re.sub(r"\b(date|contact|owner|lead|person|manager|handler)\b", "", k)
    return re.sub(r"\s+", " ", k).strip()


def resolve_person(world, text, org, hint_team=None, hint_attr=None):
    """'Kavya Nair (accounts)' / 'Arjun' -> person id, using team/role hints when a first name is shared."""
    clean = re.sub(r"\(.*?\)", "", text).strip()
    words = re.findall(r"[A-Z][a-z]+", clean)
    if not words:
        return None
    full = " ".join(words[:2])
    cands = world.people_named(full, org) if len(words) >= 2 else []
    if not cands:
        cands = world.people_named(words[0], org)
    if len(cands) <= 1:
        return cands[0] if cands else None

    def score(pid):
        p = world.people[pid]
        s = 0
        if hint_team and p.get("team") == hint_team:
            s += 2
        if hint_attr and hint_attr in (p.get("role") or "").lower():
            s += 3
        return s
    cands.sort(key=score, reverse=True)
    return cands[0] if score(cands[0]) > score(cands[1]) else None


def client_facts(recs, world, corrections=()):
    """client -> attr -> list of candidate events in time order.

    Each event: {value, display, at, day, sources, kind, authority, old}
      kind = 'baseline' (first doc version), 'update' (doc field changed / statement), 'correction'
    """
    out = {}

    def add(client, attr, ev):
        if client and attr:
            out.setdefault(client, {}).setdefault(attr, []).append(ev)

    # 1) doc fields: first version = baseline; later versions only count when the value CHANGES
    docs = {}
    for r in recs:
        if r.get("type") == "doc" and r.get("doc_id"):
            docs.setdefault(r["doc_id"], []).append(r)
    for vers in docs.values():
        vers.sort(key=lambda r: (r.get("version") or 0, r["at"]))
        prev = {}
        for r in vers:
            fields = {}
            for line in (r.get("body") or "").splitlines()[:12]:
                m = FIELD_LINE.match(line)
                if m:
                    fields[norm_attr(m[1])] = m[2].rstrip(".")
            for attr, raw in fields.items():
                if not attr:
                    continue
                if attr in prev and prev[attr] == raw:
                    continue                       # unchanged copy: not a new claim (stale-doc trap)
                ev = _value_event(world, r, attr, raw)
                ev["kind"] = "update" if attr in prev else "baseline"
                ev["old"] = prev.get(attr)
                add(r.get("client"), attr, ev)
            prev.update(fields)

    # 2) statements in chats, mails and notes
    for r in recs:
        if r.get("type") not in ("chat", "mail", "note"):
            continue
        body = r.get("body") or ""
        for sent in re.split(r"(?<=[.!?])\s+|\n", body):
            if sent.strip().endswith("?"):
                continue                           # a question is not a claim
            for rx in (TAKEOVER, NOW_OWNS):
                m = rx.search(sent)
                if not m:
                    continue
                rest = m["rest"]
                client = r.get("client")
                for c in world.clients:            # 'Northbeam billing' -> client + area
                    for w in (c["id"], c.get("name", "").split()[0]):
                        if w and re.search(r"(?i)\b" + re.escape(w) + r"\b", rest):
                            client = c["id"]
                            rest = re.sub(r"(?i)\b" + re.escape(w) + r"\b", "", rest)
                attr = norm_attr(rest)
                if not attr or len(attr) > 25:
                    continue
                old_pid = resolve_person(world, m.groupdict().get("old") or "", r.get("org")) if m.groupdict().get("old") else None
                hint_team = world.people.get(old_pid, {}).get("team") if old_pid else None
                pid = resolve_person(world, m["new"], r.get("org"), hint_team=hint_team, hint_attr=attr)
                if not pid:
                    continue
                add(client, attr, {"value": pid, "display": world.name(pid), "at": r["at"], "day": r["_day"],
                                   "sources": [r["id"]], "kind": "update", "authority": world.authority(r.get("author")),
                                   "old": old_pid})
                break
            m = MOVE_DATE.search(sent)
            if m:
                d = parse_day_text(m["date"], local_date(r["at"], world.zone(r.get("author"))))
                attr = norm_attr(m["thing"])
                if d and attr:
                    add(r.get("client"), attr, {"value": d.isoformat(), "display": fmt_day(d), "at": r["at"],
                                                "day": r["_day"], "sources": [r["id"]], "kind": "update",
                                                "authority": world.authority(r.get("author")), "old": None})

    # 2b) facts the model found where no rule matched (checked by code at ingest; only for visible records)
    for r in recs:
        for f in world.llm_facts.get(r["id"], []):
            if f["kind"] == "owner":
                add(r.get("client"), f["attr"], {"value": f["value"], "display": world.name(f["value"]), "at": r["at"],
                                                 "day": r["_day"], "sources": [r["id"]], "kind": "update",
                                                 "authority": world.authority(r.get("author")), "old": None})
            elif f["kind"] == "date":
                add(r.get("client"), f["attr"], {"value": f["value"], "display": fmt_day(f["value"]), "at": r["at"],
                                                 "day": r["_day"], "sources": [r["id"]], "kind": "update",
                                                 "authority": world.authority(r.get("author")), "old": None})

    # 3) corrections from the person being briefed (feedback), e.g. 'No, Arjun owns billing now, not Kavya.'
    for c in corrections:
        add(c["client"], c["attr"], c["event"])

    for client in out.values():
        for evs in client.values():
            evs.sort(key=lambda e: e["at"])
    return out


def _value_event(world, r, attr, raw):
    ref = local_date(r["at"], "Asia/Kolkata")
    d = parse_day_text(raw, ref)
    ev = {"at": r["at"], "day": r["_day"], "sources": [r["id"]], "authority": 1}
    if d:
        ev.update(value=d.isoformat(), display=fmt_day(d))
        return ev
    pid = resolve_person(world, raw, r.get("org"))
    if pid:
        ev.update(value=pid, display=world.name(pid))
    else:
        ev.update(value=raw.lower(), display=raw)
    return ev


def current_value(events, upto_day=None):
    """Resolve a fact: newest wins, but a lower-authority claim (intern) never overrules a higher one.
    Returns (event, accepted_events)."""
    cur, accepted = None, []
    for e in sorted(events, key=lambda e: (e["at"], e["kind"] == "correction")):
        if upto_day is not None and e["day"] > upto_day:
            continue
        if cur is not None and e["kind"] != "correction":
            if e["authority"] < min(cur["authority"], 1):
                continue                           # an intern cannot overrule anyone
            if cur["kind"] == "correction" and e["day"] <= cur["day"] and e["value"] != cur["value"]:
                continue                           # claims from before a correction cannot undo it
        cur = e
        accepted.append(e)
    return cur, accepted


# ------------------------------------------------------------------ decisions and deliveries
DECISION = re.compile(r"^\s*(?:Decision|Decided|Agreed|We agreed|Resolved)\s*:\s*(.+?)\s*$", re.M | re.I)
DELIVERED = re.compile(r"(?i)\b(?:sharing|attached|attaching|here is|here's|sent|sending|shared|uploaded)\s+(?:the\s+)?(?P<what>[^.!?\n]+?)\s+(?:as promised|as discussed|as agreed)")
DELIVERED_LOOSE = re.compile(r"(?i)^\s*(?:hi\W+\w*\W+)?(?:sharing|attaching|here is|here's|uploaded|sent)\s+(?:the\s+)?(?P<what>[^.!?\n]{3,60}?)\s*(?:[.!]|$| for | with | now)")


def decisions(recs):
    out = []
    for r in recs:
        if r.get("type") in ("note", "doc", "mail", "chat"):
            for m in DECISION.finditer(r.get("body") or ""):
                out.append({"text": m[1].rstrip("."), "rec": r})
    return out


def deliveries(recs, loose=False):
    """'Sharing the X as promised'. With loose=True also 'Here is the X.' (used only to close promises)."""
    out = []
    for r in recs:
        if r.get("type") in ("mail", "chat"):
            m = DELIVERED.search(r.get("body") or "")
            if not m and loose:
                m = DELIVERED_LOOSE.search(r.get("body") or "")
            if m:
                out.append({"what": norm_what(m["what"]), "rec": r, "strict": not loose or bool(DELIVERED.search(r.get("body") or ""))})
    return out


def norm_what(s):
    s = s.strip().rstrip(".!").lower()
    return re.sub(r"^(the|a|an|our|my)\s+", "", s)


# ------------------------------------------------------------------ promises
WD = "|".join(w.title() for w in WEEKDAYS)
WEEKDAY_RX = r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day"
DATE_RX = r"\d{1,2}(?:st|nd|rd|th)? [A-Z][a-z]{2,8}|[A-Z][a-z]{2,8} \d{1,2}"
HI_VERB = (r"(?:bhej|share kar|send kar|de|dikha|kar|bana|ready kar|finish kar|complete kar)\w*\s+"
           r"(?:dunga|dungi|denge|doonga|doongi|deta hoon|deti hoon|dunga/dungi|dungi/dunga)")
# Grammar, not sentences:  <thing> <when> tak [up to 2 filler words] <verb>   (Hinglish)
HI_WHEN = rf"kal|parso|aaj(?:\s+(?:shaam|raat|evening))?|(?:next\s+|agle\s+|is\s+|this\s+)?{WEEKDAY_RX}"
PROMISE_HI = re.compile(rf"^(?:hi\W+\w*\W+)?(?P<what>.+?)\s+(?P<when>{HI_WHEN})\s+tak\s+(?:\w+\s+){{0,2}}?{HI_VERB}", re.I)
# Grammar:  <I will|I'll|we'll|going to…> <send|share|deliver|get…> <thing> [to you|over] [by|before|on] <when>   (English)
EN_SUBJ = r"(?:I will|I'll|I am going to|I'm going to|we will|we'll|we are going to|will|going to|planning to)"
EN_VERB = r"(?:send(?: over)?|share|deliver|get|forward|pass on|hand over)"
EN_WHEN = rf"tomorrow|today|tonight|this evening|later today|EOD(?: today)?|end of (?:the )?day(?: today)?|(?:next\s+|this\s+)?{WEEKDAY_RX}|{DATE_RX}"
PROMISE_EN = re.compile(rf"\b{EN_SUBJ}\s+{EN_VERB}\s+(?P<what>.+?)\s+(?:(?:to you|over|across|over to you)\s+)?"
                        rf"(?:(?:by|before|on|latest by|no later than)\s+)?(?P<when>{EN_WHEN})\b", re.I)


def due_from(when, today):
    """Turn a time phrase into a due date, in the speaker's own calendar."""
    w = when.lower().strip()
    if w in ("kal", "tomorrow"):
        return today + timedelta(days=1)
    if w == "parso":
        return today + timedelta(days=2)
    if w.startswith(("aaj", "today", "tonight", "this evening", "later today", "eod", "end of")):
        return today
    for name in WEEKDAYS:
        if name in w:
            return next_weekday(today, name, strictly_after=True)
    return parse_day_text(when, today)


def find_promise(r, world):
    """One promise in a record, or None. Due date is worked out in the author's own time zone."""
    if r.get("type") not in ("chat", "mail") or not PERSON_ID.match(str(r.get("author") or "")):
        return None
    body = (r.get("body") or "").strip()
    for rx in (PROMISE_HI, PROMISE_EN):
        m = rx.search(body)
        if not m:
            continue
        what = m["what"].strip(" ,.")
        what = re.sub(r"(?i)^(hi|hey|hello)\s+(team|all|everyone)?[,!]?\s*", "", what)
        if len(what) > 80 or len(what) < 3 or what.lower() in ("it", "this", "that", "them"):
            continue
        today = local_date(r["at"], world.zone(r["author"]))
        due = due_from(m["when"], today)
        if not due:
            continue
        if not what.lower().startswith(("the ", "a ", "an ", "our ", "my ")):
            what = "the " + what
        return {"owner": r["author"], "what": what, "key": norm_what(what), "due": due.isoformat(), "rec": r}
    return None


def promises(recs, world, extra=()):
    """Open promises: made, and not yet delivered ('Sharing the X as promised') or done (task)."""
    made = [p for p in (find_promise(r, world) for r in recs) if p] + list(extra)
    found = {p["rec"]["id"] for p in made}
    for r in recs:                                 # model fallback, only where no rule found a promise
        if r["id"] in found:
            continue
        for f in getattr(world, "llm_facts", {}).get(r["id"], []):
            if f["kind"] == "promise":
                made.append({"owner": r["author"], "what": f["what"], "key": norm_what(f["what"]), "due": f["due"], "rec": r})
    delivered = deliveries(recs, loose=True)
    done_titles = {norm_what(t["title"]): t for t in tasks(recs).values() if t["status"] == "done"}
    out = []
    for p in made:
        closed = any(d["what"] == p["key"] and d["rec"]["at"] >= p["rec"]["at"] for d in delivered)
        if not closed and p["key"] in done_titles:
            closed = True
        if not closed:
            out.append(p)
    return out


# ------------------------------------------------------------------ leave / absence (facts that expire)
LEAVE = re.compile(
    r"(?P<who>[A-Z][a-z]+(?: [A-Z][a-z]+)?|I)\s*(?i:is|will be|'ll be|am|'m)?\s*"
    r"(?i:on leave|on vacation|on holiday|out of office|OOO|away|off work|unavailable|not available)"
    r"(?:\s+(?i:from|starting)\s+(?P<start>\d{1,2}(?:st|nd|rd|th)?(?: [A-Z][a-z]{2,8})?|[A-Z][a-z]{2,8} \d{1,2}))?"
    r"(?:\s+(?i:till|until|to|through|thru|up to)\s+(?i:the\s+)?(?P<end>\d{1,2}(?:st|nd|rd|th)?(?: [A-Z][a-z]{2,8})?|[A-Z][a-z]{2,8} \d{1,2}|(?i:next\s+)?(?i:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow)))?")


def _day_only(text, ref):
    """'12th' or '12' -> date in ref's month (next month if already past by more than 15 days)."""
    d = parse_day_text(text, ref)
    if d:
        return d
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?$", text.strip())
    if m:
        from datetime import date
        day = int(m[1])
        try:
            d = date(ref.year, ref.month, day)
        except ValueError:
            return None
        if (ref - d).days > 15:
            d = date(ref.year + (ref.month == 12), ref.month % 12 + 1, day)
        return d
    low = text.lower().strip()
    if low == "tomorrow":
        return ref + timedelta(days=1)
    for w in WEEKDAYS:
        if w in low:
            return next_weekday(ref, w, strictly_after=True)
    return None


def absences(recs, world):
    """person -> list of {start, end, rec}. A leave without an end date is ignored (cannot expire safely)."""
    out = {}
    for r in recs:
        if r.get("type") not in ("chat", "mail", "note", "doc"):
            continue
        for sent in re.split(r"(?<=[.!?])\s+|\n", r.get("body") or ""):
            if sent.strip().endswith("?"):
                continue
            m = LEAVE.search(sent)
            if not m or not m["end"]:
                continue
            ref = local_date(r["at"], world.zone(r.get("author")))
            end = _day_only(m["end"], ref)
            start = _day_only(m["start"], ref) if m["start"] else ref
            who = m["who"]
            if who.lower() in ("i", "i'm", "i am"):
                pid = r.get("author")
            else:
                pid = resolve_person(world, who, r.get("org"))
            if pid and end:
                out.setdefault(pid, []).append({"start": start, "end": end, "rec": r})
    return out

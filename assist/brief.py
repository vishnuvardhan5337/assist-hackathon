"""brief --person P --date D  ->  JSON list, one brief per meeting P attends on D (P's local date).

How a brief is chosen (all plain code):
  1. Meetings: calendar events P can see, P is an attendee, start falls on D in P's time zone.
  2. Window: everything since P's previous meeting with the same client (by day). First meeting: everything.
  3. changed: client facts that changed (launch date, billing owner...), decisions in notes, task changes
     (done / blocked / due moved / new owner / cancelled), deliveries. Most important first.
  4. promises: promises for this client still open (not delivered, task not done).
  5. Person's own memory: max N changes, blockers first, skipped topics, ignored topics, forgotten things.
  6. Every line cites real records P can see. Nothing left -> status "nothing_new".
"""
import re

from . import facts as F
from . import memory
from .llm import Usage
from .world import fmt_day, local_date, parse_day_text, parse_ts

STATUS_WORDS = {"done": "is done", "blocked": "is blocked", "cancelled": "was cancelled",
                "in_progress": "is now in progress", "todo": "is back to to-do"}


def meetings_for(world, person, day_str, vis):
    events = {}
    for r in vis:
        if r.get("type") == "event" and r.get("event_id"):
            events[r["event_id"]] = r              # latest version of each event wins
    zone = world.zone(person)
    mine = [e for e in events.values() if person in (e.get("attendees") or []) and e.get("start")
            and "cancel" not in (e.get("status") or "").lower()]
    today = [e for e in mine if local_date(e["start"], zone).isoformat() == day_str]
    return sorted(today, key=lambda e: e["start"]), mine


def window_start_day(world, person, meeting, mine):
    """Day index from which records count as 'new' for this meeting."""
    zone = world.zone(person)
    this_day = local_date(meeting["start"], zone)
    prev = [e for e in mine if e.get("client") == meeting.get("client") and e["event_id"] != meeting["event_id"]
            and local_date(e["start"], zone) < this_day]     # same-day meetings have not happened yet
    if not prev:
        return 1
    last = max(prev, key=lambda e: e["start"])
    return world.day_index(local_date(last["start"], zone))


def build_changes(world, person, client, since_day, vis, corrections):
    out = []
    crec = [r for r in vis if r.get("client") == client]

    # 1) client facts (launch date, billing owner, ...)
    cf = F.client_facts(vis, world, corrections).get(client, {})
    for attr, evs in cf.items():
        now, accepted = F.current_value(evs)
        if not now:
            continue
        before, _ = F.current_value(evs, upto_day=since_day - 1)
        if before and before["value"] == now["value"]:
            continue
        new_events = [e for e in accepted if e["day"] >= since_day and e["value"] == now["value"] and e["kind"] != "baseline"]
        if not new_events:
            continue
        old_val = before["value"] if before else next((e.get("old") for e in new_events if e.get("old")), None)
        if old_val is None and now["kind"] == "baseline":
            continue
        srcs = []
        for e in new_events:
            srcs += e["sources"]
        if not srcs:                               # a correction only: cite records that agree with it
            srcs = [s for e in accepted if e["value"] == now["value"] for s in e["sources"]]
        srcs = [s for s in dict.fromkeys(srcs) if s in world.by_id]
        if not srcs:
            continue
        cname = world.client_name.get(client, client)
        if F.PERSON_ID.match(str(now["value"])):
            was = f" (was {world.name(old_val)})" if old_val and F.PERSON_ID.match(str(old_val)) else ""
            text = f"{now['display']} now owns {cname.split()[0]} {attr}{was}"
        elif re.match(r"\d{4}-\d{2}-\d{2}$", str(now["value"])):
            if old_val and not re.match(r"\d{4}-\d{2}-\d{2}", str(old_val)):
                d = parse_day_text(str(old_val), world.day1)
                old_val = d.isoformat() if d else old_val
            old_disp = fmt_day(old_val) if old_val else None
            text = f"{attr.capitalize()} moved from {old_disp} to {now['display']}" if old_disp else \
                f"{attr.capitalize()} date is now {now['display']}"
        else:
            text = f"{attr.capitalize()} changed to {now['display']}"
        out.append({"text": text, "sources": srcs[:3], "prio": 1, "at": now["at"], "kind": "fact"})

    # 2) decisions from meeting notes
    for d in F.decisions(crec):
        if d["rec"]["_day"] >= since_day:
            out.append({"text": f"Decision: {d['text']}", "sources": [d["rec"]["id"]], "prio": 1,
                        "at": d["rec"]["at"], "kind": "decision"})

    # 3) task changes inside the window (net change per task and field)
    for t in F.tasks(vis).values():
        if t["client"] != client:
            continue
        hist = [h for h in t["history"] if h["_day"] >= since_day]
        by_field = {}
        for h in hist:
            by_field.setdefault(h["change"]["field"], []).append(h)
        title = t["title"]
        for field, hs in by_field.items():
            frm, to = hs[0]["change"].get("from"), hs[-1]["change"].get("to")
            if frm == to:
                continue
            src = [hs[-1]["id"]]
            if field == "status":
                if to == "blocked":
                    reason = re.sub(r"(?i)^blocked[:\s-]*", "", (hs[-1].get("body") or "")).strip().rstrip(".")
                    text = f"{title} is blocked" + (f" ({reason})" if reason else "")
                    prio = 1
                else:
                    text = f"{title} {STATUS_WORDS.get(to, 'status is now ' + str(to))}"
                    prio = 2 if to in ("done", "cancelled") else 3
            elif field == "due":
                text = f"{title} due date moved from {fmt_day(frm)} to {fmt_day(to)}" if frm else \
                    f"{title} is now due {fmt_day(to)}"
                prio = 2
            elif field == "owner":
                text = f"{title} now owned by {world.name(to)}" + (f" (was {world.name(frm)})" if frm else "")
                prio = 2
            else:
                continue
            out.append({"text": text, "sources": src, "prio": prio, "at": hs[-1]["at"], "kind": "task:" + field})

    # 4) deliveries ('Sharing the X as promised')
    for d in F.deliveries(crec):
        r = d["rec"]
        if r["_day"] >= since_day and F.PERSON_ID.match(str(r.get("author"))):
            out.append({"text": f"{world.first(r['author'])} delivered the {d['what']}", "sources": [r["id"]],
                        "prio": 3, "at": r["at"], "kind": "delivered"})

    # one event, one line: a cancelled task already covered by a decision line is merged into it
    decisions_ = [c for c in out if c["kind"] == "decision"]
    merged = []
    for c in out:
        if c["kind"] == "task:status" and "cancelled" in c["text"]:
            title = c["text"].split(" was cancelled")[0].lower()
            host = next((d for d in decisions_ if title in d["text"].lower()), None)
            if host:
                host["sources"] = list(dict.fromkeys(host["sources"] + c["sources"]))
                continue
        merged.append(c)
    merged.sort(key=lambda c: (c["prio"], c["at"]))
    return merged


def _mentions_any(text, words):
    low = text.lower()
    return any(w and w in low for w in words)


def apply_person_memory(world, person, changes, prefs, ignored, forgotten):
    kept = []
    for c in changes:
        srcs = [world.by_id[s] for s in c["sources"] if s in world.by_id]
        blob = c["text"] + " " + " ".join((r.get("body") or "") + " " + (r.get("subject") or "") for r in srcs)
        if prefs["skip"] and any(_skip_hit(s, blob, srcs) for s in prefs["skip"]):
            continue
        if any(_topic_hit(t, blob) for t in ignored):
            continue
        if any(all(t in blob.lower() for t in terms) for terms in forgotten):
            continue
        kept.append(c)
    if prefs["blockers_first"]:
        kept.sort(key=lambda c: (0 if "block" in c["text"].lower() else 1, c["prio"], c["at"]))
    if prefs["max_changed"]:
        kept = kept[:prefs["max_changed"]]
    return kept


def _skip_hit(skip, blob, srcs):
    s = skip.rstrip("s")
    if s in blob.lower():
        return True
    if s.startswith("newsletter"):                 # newsletters: bulk mail from outside senders
        return all(str(r.get("author", "")).startswith("ext:") and r.get("type") == "mail" and not r.get("client")
                   for r in srcs) and bool(srcs)
    return False


def _topic_hit(topic, blob):
    words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", topic)]
    caps = [w.lower() for w in re.findall(r"\b[A-Z][a-z]{3,}\b", topic)]
    low = blob.lower()
    if caps and all(c in low for c in caps[:1]):
        return True
    return len(words) >= 2 and sum(w in low for w in words) >= 2


def question_for(world, changes, proms, day_str, person=None):
    for c in changes:
        if "blocked" in c["text"]:
            name = c["text"].split(" is blocked")[0]
            return {"text": f"What do we need to unblock {name}, and who owns that?", "sources": c["sources"]}
    overdue = [p for p in proms if p["due"] < day_str and p["owner"] != person]
    if overdue:
        p = overdue[0]
        return {"text": f"{world.first(p['owner'])} promised {p['what']} by {fmt_day(p['due'])}. Is it still coming?",
                "sources": [p["rec"]["id"]]}
    for c in changes:
        if c["kind"] == "fact" and "moved" in c["text"]:
            return {"text": f"{c['text']}: does anything else in the plan need to move with it?", "sources": c["sources"]}
    if changes:
        return {"text": f"Any follow-ups needed after this: {changes[0]['text']}?", "sources": changes[0]["sources"]}
    return None


def run(world, person, day_str):
    usage = Usage()
    vis = world.visible(person)
    todays, mine = meetings_for(world, person, day_str, vis)
    prefs = memory.preferences(world, person, usage)
    ignored = memory.ignored_topics(world, person)
    forgotten = world.forgotten_terms(person)
    corrections = memory.corrections(world, usage, viewer=person)
    out = []
    for m in todays:
        client = m.get("client")
        b = {"meeting_id": m["event_id"], "status": "nothing_new", "changed": [], "promises": [],
             "open_these": [], "question_to_ask": None}
        if client:
            since = window_start_day(world, person, m, mine)
            changes = build_changes(world, person, client, since, vis, corrections)
            changes = apply_person_memory(world, person, changes, prefs, ignored, forgotten)
            if changes:
                crec = [r for r in vis if r.get("client") == client]
                proms = [p for p in F.promises(crec, world)
                         if not any(all(t in p["what"].lower() for t in terms) for terms in forgotten)]
                proms.sort(key=lambda p: (p["due"], p["rec"]["at"]))
                b["status"] = "brief"
                b["changed"] = [{"text": c["text"], "sources": c["sources"]} for c in changes]
                b["promises"] = [{"owner": p["owner"], "what": p["what"], "due": p["due"],
                                  "sources": [p["rec"]["id"]]} for p in proms]
                seen, opens = set(), []
                for c in changes:
                    rid = c["sources"][0]
                    if rid not in seen:
                        seen.add(rid)
                        opens.append({"record": rid, "why": c["text"]})
                    if len(opens) == 3:
                        break
                b["open_these"] = opens
                b["question_to_ask"] = question_for(world, changes, proms, day_str, person)
        b["usage"] = None
        out.append(b)
    u = usage.as_dict()
    n = max(1, len(out))
    for b in out:                                  # spread one command's usage over its briefs
        b["usage"] = {"input_tokens": u["input_tokens"] // n, "output_tokens": u["output_tokens"] // n,
                      "cost_usd": 0.0, "ms": u["ms"]}
    return out

"""ask --person P --at ISO --ask "text"  ->  one JSON object.

Exactly one of answer / clarify / refused is filled. Plain code handles the common shapes:
  who owns / handles <task or area> for <client>
  when is <task> due
  what is the status of <task>        /   is the <weekday> thing done?
  which <first name> is that          /   what did <person> say about me
Anything else: search the asker's visible records, and let the model answer from the few best ones.
"""
import re
from datetime import timedelta

from . import facts as F
from . import llm
from . import memory
from .llm import Usage
from .world import WEEKDAYS, STOPWORDS, fmt_day, local_date, next_weekday, parse_ts, record_text

STATUS_TEXT = {"done": "done", "in_progress": "in progress (still being worked on)", "todo": "not started yet (to do)",
               "blocked": "blocked", "cancelled": "cancelled"}
NOTHING = "I have nothing on that."


def empty(usage):
    return {"answer": None, "matched": [], "clarify": None, "refused": None, "usage": usage.as_dict()}


def words(s):
    return [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOPWORDS]


def match_task(question, tasks, client=None):
    """Best task whose title words appear in the question. Returns (task, score) or (None, 0)."""
    q = set(words(question))
    best, best_s = None, 0.0
    for t in tasks.values():
        if client and t["client"] and t["client"] != client:
            continue
        tw = words(t["title"])
        if not tw:
            continue
        s = sum(w in q for w in tw) / len(tw)
        if s > best_s or (s == best_s and best and len(tw) > len(words(best["title"]))):
            best, best_s = t, s
    return (best, best_s) if best_s >= 0.6 else (None, 0.0)


def resolve_client_llm(world, question, usage, org="org_kp"):
    """Nicknames like 'the London account': let the model choose among client profiles."""
    m = re.search(r"(?i)\b(?:the\s+)?([A-Z][A-Za-z]+(?:\s+[A-Za-z]+)?)\s+(client|account|customer|brand)\b", question)
    if not m or not world.clients:
        return None, None
    phrase = m.group(0)
    profiles = "\n".join(str(world.client_profile(c["id"], org)) for c in world.clients)
    data = llm.call_json(world.con,
                         f"Our agency's clients:\n{profiles}\n\nWhich client does the phrase \"{phrase}\" most likely mean? "
                         'Use the email domains and team locations as clues. Reply {"client": "<id or null>"}.',
                         usage, budget=1500, max_out=300)
    cid = data.get("client") if isinstance(data, dict) else None
    if cid in world.client_name:
        return cid, phrase
    return None, None


def run(world, person, at, question):
    usage = Usage()
    vis = world.visible(person, until_ts=at)
    org = world.org_of.get(person)
    out = empty(usage)
    q = question.strip()
    forgotten = world.forgotten_terms(person)

    client, phrase = world.find_client(q, vis)
    client_sure = bool(client)                      # found in code; a model guess is only a hint
    if not client:
        client, phrase = resolve_client_llm(world, q, usage, org)
    matched = [{"phrase": phrase, "id": client, "sources": []}] if client else []
    tasks = F.tasks(vis)
    low = q.lower()

    # ---- which <name> is that?
    m = re.search(r"(?i)\bwhich\s+([A-Z][a-z]+)\b", q)
    if m and world.people_named(m[1], org):
        return _which_person(world, person, m[1], client, vis, matched, usage)

    # ---- what did X say about me / Y?
    m = re.search(r"(?i)\bwhat (?:did|has|does)\s+([A-Z][a-z]+)\s+(?:say|said|write|written|think|tell)\w*\s+about\s+(me|[A-Z][a-z]+)", q)
    if m:
        return _said_about(world, person, m[1], m[2], vis, usage)

    # ---- is X on leave / away / back?  (facts that expire: answered for the asker's own date)
    if re.search(r"(?i)\b(leave|vacation|holiday|away|out of office|ooo|off|available|back)\b", q):
        names = [w for w in re.findall(r"\b[A-Z][a-z]+\b", q) if world.people_named(w, org)]
        if names:
            return _leave_answer(world, person, at, names[0], vis, usage)

    intent = None
    if re.search(r"\bwho\b.*\b(own|owns|handle|handles|handling|manage|manages|responsible|in charge)\b", low):
        intent = "owner"
    elif re.search(r"\bwhen\b.*\bdue\b|\bdue date\b|\bdeadline\b|\bwhen is\b.*\b(due|ready)\b", low):
        intent = "due"
    elif re.search(r"\bstatus\b|\bdone\b|\bfinished\b|\bcomplete\b|\bready\b", low):
        intent = "status"

    if intent:
        task, _ = match_task(q, tasks, client)
        if not task and client and not client_sure:
            task, _ = match_task(q, tasks, None)    # the model's client guess may be wrong
        if task:
            if not client_sure and task["client"]:
                matched = [{"phrase": phrase or task["client"], "id": task["client"], "sources": []}]
            return _task_answer(world, task, intent, matched, usage)
        if intent == "owner":
            tries = [(client, False)] if client else []
            if not client_sure:
                tries += [(c["id"], True) for c in world.clients]
            for cid, unique in tries:
                ans = _fact_owner(world, person, cid, q, vis, matched if not unique else
                                  [{"phrase": phrase or cid, "id": cid, "sources": []}], usage, unique=unique)
                if ans:
                    return ans
        # 'the Friday thing': tasks of the asker due on that weekday
        wd = next((w for w in WEEKDAYS if w in low), None)
        if wd or re.search(r"\b(today|tomorrow)\b", low):
            today = local_date(at, world.zone(person))
            if wd:
                target = next_weekday(today, wd, strictly_after=False)
            else:
                target = today if "today" in low else today + timedelta(days=1)
            cands = [t for t in tasks.values() if t["due"] == target.isoformat()
                     and (t["owner"] == person) and (not client or t["client"] == client)]
            if len(cands) > 1:
                out["clarify"] = {"question": "Which one do you mean: " + " or ".join(t["title"] for t in cands) + "?",
                                  "options": [{"id": t["id"], "text": t["title"]} for t in cands]}
                out["usage"] = usage.as_dict()
                return out
            if len(cands) == 1:
                return _task_answer(world, cands[0], "status" if intent == "status" else intent, matched, usage)

    # question type unclear ("Where are we with X?", "Who's on X?"): a clearly matching task gets its
    # full state (owner, due date, status) in one answer, whatever the wording
    if not intent:
        task, _ = match_task(q, tasks, client)
        if not task and client and not client_sure:
            task, _ = match_task(q, tasks, None)
        if task:
            if not client_sure and task["client"]:
                matched = [{"phrase": phrase or task["client"], "id": task["client"], "sources": []}]
            return _task_answer(world, task, "summary", matched, usage)
        tries = [(client, False)] if client else []
        if not client_sure:
            tries += [(c["id"], True) for c in world.clients]
        for cid, unique in tries:
            ans = _fact_owner(world, person, cid, q, vis, matched if not unique else
                              [{"phrase": phrase or cid, "id": cid, "sources": []}], usage, unique=unique)
            if ans:
                return ans

    return _free_answer(world, person, q, vis, matched, usage, forgotten, at)


def _leave_answer(world, person, at, name, vis, usage):
    org = world.org_of.get(person)
    cands = world.people_named(name, org)
    out = empty(usage)
    if not cands:
        out["answer"] = {"text": NOTHING, "sources": []}
        return out
    pid = cands[0]
    today = local_date(at, world.zone(person))
    spans = F.absences(vis, world).get(pid, [])
    current = [s for s in spans if s["start"] <= today <= s["end"]]
    future = [s for s in spans if s["start"] > today]
    who = world.name(pid)
    if current:
        sp = max(current, key=lambda s: s["rec"]["at"])
        out["answer"] = {"text": f"Yes. {who} is on leave until {fmt_day(sp['end'])} ({sp['end'].isoformat()}).", "sources": [sp["rec"]["id"]]}
    elif future:
        sp = min(future, key=lambda s: s["start"])
        out["answer"] = {"text": f"{who} will be away from {fmt_day(sp['start'])} to {fmt_day(sp['end'])}.",
                         "sources": [sp["rec"]["id"]]}
    else:                                          # expired or unknown: say nothing old
        out["answer"] = {"text": f"Nothing I can see says {who} is away right now.", "sources": []}
    out["matched"] = [{"phrase": name, "id": pid, "sources": []}]
    out["usage"] = usage.as_dict()
    return out


def _task_answer(world, t, intent, matched, usage):
    """The asked-for fact first, then the task's other key facts, so a misread question still gets its answer."""
    out = empty(usage)
    who, st = t["owner"], t["status"] or "unknown"
    owner_txt = f"{world.name(who)} ({who})" if who else "nobody yet"
    due_txt = f"{fmt_day(t['due'])} ({t['due']})" if t["due"] else "no due date"
    status_txt = STATUS_TEXT.get(st, st)
    matched = matched + [{"phrase": t["title"], "id": t["id"], "sources": [_src(t, "created")]}]
    if who:
        matched.append({"phrase": world.first(who), "id": who, "sources": [_src(t, "owner")]})
    if intent == "owner":
        text = f"{owner_txt} owns {t['title']} (due {due_txt}, {status_txt})."
        srcs = [_src(t, "owner")]
    elif intent == "due":
        text = f"{t['title']} is due {due_txt} (owner {owner_txt}, {status_txt})."
        srcs = [_src(t, "due")]
    elif intent == "status":
        text = f"{t['title']} is {status_txt} (owner {owner_txt}, due {due_txt})."
        srcs = [_src(t, "status")]
    else:
        text = f"{t['title']}: owned by {owner_txt}, due {due_txt}, status {status_txt}."
        srcs = [_src(t, "status")]
    srcs += [s for s in (_src(t, "owner"), _src(t, "due"), _src(t, "status")) if s not in srcs]
    out["answer"] = {"text": text, "sources": [s for s in dict.fromkeys(srcs) if s]}
    out["matched"] = _clean_matched(matched)
    out["usage"] = usage.as_dict()
    return out


def _src(t, field):
    r = t["src"].get(field) or t["src"].get("status") or (t["history"][-1] if t["history"] else None)
    return r["id"] if r else None


def _fact_owner(world, person, client, q, vis, matched, usage, unique=False):
    corrections = memory.corrections(world, usage, viewer=person)
    allf = F.client_facts(vis, world, corrections)
    cf = allf.get(client, {})
    qw = set(words(q))
    if unique:   # no client named: only answer if exactly one client has this kind of fact
        owners = [c for c, fs in allf.items() if any(set(a.split()) & qw for a in fs)]
        if owners != [client]:
            return None
    for attr, evs in cf.items():
        if not set(attr.split()) & qw:
            continue
        now, accepted = F.current_value(evs)
        if not now or not F.PERSON_ID.match(str(now["value"])):
            continue
        srcs = [s for e in reversed(accepted) if e["value"] == now["value"] for s in e["sources"]]
        srcs = [s for s in dict.fromkeys(srcs) if s in world.by_id][:2]
        cname = world.client_name.get(client, client)
        out = empty(usage)
        out["answer"] = {"text": f"{world.name(now['value'])} ({now['value']}) owns {attr} for {cname}.", "sources": srcs}
        out["matched"] = _clean_matched(matched + [{"phrase": world.first(now["value"]), "id": now["value"], "sources": srcs}])
        out["usage"] = usage.as_dict()
        return out
    return None


def _which_person(world, person, first_name, client, vis, matched, usage):
    org = world.org_of.get(person)
    cands = world.people_named(first_name, org)
    scores = {c: 0.0 for c in cands}
    evidence = {c: [] for c in cands}
    # who the asker works with: co-mentions in records the asker wrote or received, shared meetings, client roles
    corrections = memory.corrections(world, usage, viewer=person)
    cf = F.client_facts(vis, world, corrections)
    for c in cands:
        if client:
            for attr, evs in cf.get(client, {}).items():
                now, acc = F.current_value(evs)
                if now and now["value"] == c:
                    scores[c] += 3
                    evidence[c] += [s for e in acc if e["value"] == c for s in e["sources"]]
            for t in F.tasks(vis).values():
                if t["client"] == client and t["owner"] == c:
                    scores[c] += 1
                    evidence[c].append(_src(t, "owner"))
        for r in vis:
            involved = person == r.get("author") or person in (r.get("to") or []) or person in (r.get("attendees") or [])
            if not involved:
                continue
            if c in (r.get("to") or []) or c in (r.get("attendees") or []) or c == r.get("author") \
                    or world.name(c) in (r.get("body") or ""):
                scores[c] += 0.5
                evidence[c].append(r["id"])
    ranked = sorted(cands, key=lambda c: scores[c], reverse=True)
    out = empty(usage)
    if len(ranked) >= 2 and scores[ranked[0]] == scores[ranked[1]]:
        out["clarify"] = {"question": f"Which {first_name} do you mean?",
                          "options": [{"id": c, "text": f"{world.name(c)} ({world.role(c)})"} for c in ranked]}
        out["usage"] = usage.as_dict()
        return out
    best = ranked[0]
    srcs = [s for s in dict.fromkeys(evidence[best]) if s][:2]
    role = world.people[best].get("role", "")
    out["answer"] = {"text": f"That is {world.name(best)} ({best}), {role}" +
                     (f", who works with you on {world.client_name.get(client, client)}." if client else "."),
                     "sources": srcs}
    out["matched"] = _clean_matched(matched + [{"phrase": first_name, "id": best, "sources": srcs}])
    out["usage"] = usage.as_dict()
    return out


def _said_about(world, person, speaker_name, target, vis, usage):
    org = world.org_of.get(person)
    speakers = world.people_named(speaker_name, org)
    target_id = person if target.lower() == "me" else (world.people_named(target, org) or [None])[0]
    tname = world.first(target_id) if target_id else target
    out = empty(usage)

    def about(r):
        return r.get("author") in speakers and re.search(r"\b" + re.escape(tname) + r"\b", r.get("body") or "")
    seen = [r for r in vis if about(r)]
    if seen:
        r = seen[-1]
        out["answer"] = {"text": f"{world.name(r['author'])} wrote: \"{(r.get('body') or '')[:200]}\"", "sources": [r["id"]]}
    elif any(about(r) for r in world.records if r.get("org") == org and not world.can_see(person, r)):
        out["refused"] = {"reason": "That is private to someone else, so I can't share it."}
    else:
        out["answer"] = {"text": NOTHING, "sources": []}
    out["usage"] = usage.as_dict()
    return out


def _free_answer(world, person, q, vis, matched, usage, forgotten, at=None):
    """Search the asker's visible records; the model answers only from the best few."""
    out = empty(usage)
    qw = [w for w in words(q) if len(w) >= 4 and w not in ("when", "what", "where", "which", "there", "this", "month",
                                                          "week", "anything", "anywhere", "status")]
    if not qw:
        out["answer"] = {"text": NOTHING, "sources": []}
        return out
    scored = []
    for r in vis:
        if r.get("type") in ("notification",) or str(r.get("author", "")).startswith("ext:") and not r.get("client"):
            continue
        text = record_text(r).lower()
        s = sum(1 for w in qw if re.search(r"\b" + re.escape(w), text))
        if s:
            mine = 1 if r.get("author") == person else 0
            scored.append((s, mine, r["at"], r))
    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    top = [x[3] for x in scored[:6] if x[0] >= max(1, min(2, len(qw)))]
    top = [r for r in top if not any(all(t in record_text(r).lower() for t in terms) for terms in forgotten)]
    if not top:
        out["answer"] = {"text": NOTHING, "sources": []}
        out["usage"] = usage.as_dict()
        return out
    recs = "\n".join(llm.wrap(r) for r in top)
    today_txt = local_date(at, world.zone(person)).strftime("%A %d %b %Y") if at else "unknown"
    asker = f"{world.name(person)} ({person}), time zone {world.zone(person)}, today is {today_txt}"
    data = llm.call_json(world.con,
                         f"Question from {asker}:\n\"{q}\"\n\nRecords:\n{recs}\n\n"
                         'Answer in one short sentence using only these records. If they do not answer it, say so. '
                         'Reply {"answer": "...", "sources": ["record ids you used"], "answered": true/false}.',
                         usage, budget=3500, max_out=500)
    if isinstance(data, dict) and data.get("answer"):
        ids = {r["id"] for r in top}
        srcs = [s for s in data.get("sources") or [] if s in ids]
        if data.get("answered") is False or not srcs:
            out["answer"] = {"text": NOTHING, "sources": []}
        else:
            out["answer"] = {"text": str(data["answer"])[:300], "sources": srcs}
    else:                                          # no model: quote the best record
        r = top[0]
        out["answer"] = {"text": f"From {world.name(r.get('author'))}: \"{(r.get('body') or '')[:200]}\"", "sources": [r["id"]]}
    text = out["answer"]["text"].lower()
    if any(all(t in text for t in terms) for terms in forgotten):
        out["answer"] = {"text": NOTHING, "sources": []}
    out["matched"] = _clean_matched(matched)
    out["usage"] = usage.as_dict()
    return out


def _clean_matched(ms):
    seen, out = set(), []
    for m in ms:
        if not m or not m.get("id") or m["id"] in seen:
            continue
        seen.add(m["id"])
        out.append({"phrase": m.get("phrase") or m["id"], "id": m["id"], "sources": [s for s in m.get("sources") or [] if s]})
    return out

"""Feedback -> memory. Every rule here is scoped to the person who gave the feedback,
except corrections of team facts (which fix the fact for everyone who can see it).

  preferences(world, person) -> {"max_changed": int|None, "blockers_first": bool, "skip": [words]}
  ignored_topics(world, person) -> [topic strings the person marked 'ignored']
  corrections(world) -> list of fact events for facts.client_facts()
"""
import re

from . import llm
from . import facts as F
from .facts import norm_attr, resolve_person
from .world import record_text as world_text, fmt_day, local_date, parse_day_text

MAX_N = re.compile(r"(?i)\b(?:max(?:imum)?|at most|no more than|only|up to|limit(?: to)?)\.?\s*(\d{1,2})\s*(?:changes|items|lines|points|bullets|updates)?")
BLOCKERS = re.compile(r"(?i)block\w*\s+(?:first|on top|at the top|up front)|(?:first|top)\W+(?:\w+\W+){0,3}block")
SKIP = re.compile(r"(?i)\b(?:skip|drop|remove|hide|exclude|leave out|no more|don't (?:include|show))\s+(?:the\s+|any\s+|all\s+)?([a-z][a-z\- ]{2,30}?)(?:\s+(?:in|from)\s+my|\.|,|$)")


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20}


def _digits(text):
    return re.sub(r"(?i)\b(" + "|".join(NUMBER_WORDS) + r")\b", lambda m: str(NUMBER_WORDS[m[1].lower()]), text)


def preferences(world, person, usage=None):
    prefs = {"max_changed": None, "blockers_first": False, "skip": []}
    for fb in world.feedback:
        if fb.get("person") != person or fb.get("type") != "preference":
            continue
        words = _digits(fb.get("words", ""))
        hit = False
        m = MAX_N.search(words) or re.search(r"(?i)\b(?:keep|limit|cap)\b.{0,30}?\b(\d{1,2})\s+(?:changes|items|lines|points|updates)", words)
        if m:
            prefs["max_changed"] = int(m[1]); hit = True
        if BLOCKERS.search(words):
            prefs["blockers_first"] = True; hit = True
        for s in SKIP.findall(words):
            prefs["skip"].append(s.strip().lower()); hit = True
        if not hit and usage is not None:
            data = llm.call_json(world.con,
                                 "A person told their meeting-brief assistant how they want briefs. Convert it to JSON "
                                 '{"max_changed": int or null, "blockers_first": true/false, "skip": [short topic words]}.\n'
                                 f"Preference: <record id=\"pref\" type=\"feedback\">{words}</record>",
                                 usage, budget=600, max_out=200)
            if isinstance(data, dict):
                if isinstance(data.get("max_changed"), int):
                    prefs["max_changed"] = data["max_changed"]
                if data.get("blockers_first") is True:
                    prefs["blockers_first"] = True
                prefs["skip"] += [str(x).lower() for x in data.get("skip") or [] if isinstance(x, str)]
    return prefs


def ignored_topics(world, person):
    return [fb.get("topic", "") for fb in world.feedback
            if fb.get("person") == person and fb.get("type") == "verdict" and fb.get("verdict") == "ignored"]


CORRECT_OWNER = re.compile(
    r"(?i)^(?:no[,.!]?\s*)?(?:actually[,]?\s*)?(?P<new>[A-Z][a-z]+(?: [A-Z][a-z]+)?)\s+(?:now\s+)?(?:owns|handles|is handling|manages|is responsible for|is in charge of)\s+"
    r"(?:the\s+)?(?P<attr>[a-z][a-z ]{1,25}?)(?:\s+now)?(?:\s*,?\s*not\s+(?P<old>[A-Z][a-z]+(?: [A-Z][a-z]+)?))?\s*[.!]?\s*$")
CORRECT_DATE = re.compile(
    r"(?i)^(?:no[,.!]?\s*)?(?:the\s+)?(?P<attr>[a-z][a-z ]{1,25}?)\s+(?:is|moved to|is now|is on)\s+(?:on\s+)?"
    r"(?P<date>\d{1,2}(?:st|nd|rd|th)? [A-Z][a-z]{2,8}|[A-Z][a-z]{2,8} \d{1,2})")


TOPICS_RX = r"(billing|accounts|invoicing|payments|launch|design|copy|qa|testing|development|support)"


def _loose_owner_correction(world, words, org):
    """General shapes: '<topic> moved to X', 'X has it now', 'X now owns <topic>', '... not Y', 'Y no longer ...'."""
    topic = re.search(r"(?i)\b" + TOPICS_RX + r"\b", words)
    if not topic:
        return None
    names = [n for n in re.findall(r"\b[A-Z][a-z]+\b", words) if world.people_named(n, org)]
    old = [n for n in names if re.search(r"(?i)(\bnot\s+" + n + r"\b|\b" + n + r"\s+(?:no longer|is not|isn't|doesn't|does not))", words)]
    new = [n for n in names if n not in old and re.search(
        r"(?i)(\b(?:to|is|now)\s+" + n + r"\b|\b" + n + r"\s+(?:now\s+)?(?:has|owns|handles|is handling|took over|takes over|manages))", words)]
    if len(new) != 1:
        return None
    attr = norm_attr(topic[1].lower())
    old_pid = resolve_person(world, old[0], org) if old else None
    hint_team = world.people.get(old_pid, {}).get("team") if old_pid else None
    pid = resolve_person(world, new[0], org, hint_team=hint_team, hint_attr=attr)
    if not pid:
        return None
    return {"value": pid, "display": world.name(pid), "old": old_pid}, attr


def corrections(world, usage=None, viewer=None):
    """Feedback corrections as fact events. The client comes from the meeting the correction is about.
    Only corrections from the viewer's own company apply (never leak across organisations)."""
    out = []
    viewer_org = world.org_of.get(viewer) if viewer else None
    events = {r.get("event_id"): r for r in world.records if r.get("type") == "event"}
    for fb in world.feedback:
        if fb.get("type") != "correction":
            continue
        person, words = fb.get("person"), fb.get("words", "")
        if viewer is not None and world.org_of.get(person) != viewer_org:
            continue
        meeting = events.get(fb.get("meeting"))
        if meeting and viewer is not None and not world.can_see(viewer, meeting):
            meeting = None
        client = meeting.get("client") if meeting else None
        org = world.org_of.get(person)
        if not client:                             # no meeting given: the client named in the correction itself
            client, _ = world.find_client(words, [r for r in world.records if r.get("org") == org
                                                  and (viewer is None or world.can_see(viewer, r))])
        day = fb.get("day", 0)
        at = (world.day1.fromordinal(world.day1.toordinal() + day - 1)).isoformat() + "T23:59:59Z"
        ev = None
        m = CORRECT_OWNER.match(words.strip())
        if m:
            attr = norm_attr(m["attr"])
            old = resolve_person(world, m["old"], org) if m["old"] else None
            hint_team = world.people.get(old, {}).get("team") if old else None
            pid = resolve_person(world, m["new"], org, hint_team=hint_team, hint_attr=attr)
            if pid and attr:
                ev = {"value": pid, "display": world.name(pid), "old": old}
        else:
            m = CORRECT_DATE.match(words.strip())
            if m:
                d = parse_day_text(m["date"], world.day1)
                attr = norm_attr(m["attr"])
                if d and attr:
                    ev = {"value": d.isoformat(), "display": fmt_day(d), "old": None}
        if ev is None:
            ev, attr = _loose_owner_correction(world, words, org) or (None, None)
        if ev is None and usage is not None:
            data = llm.call_json(world.con,
                                 "A manager corrected a fact in their meeting brief. Extract it as JSON "
                                 '{"attr": short topic like "billing" or "launch", "kind": "person" or "date", '
                                 '"value": the new person name or the new date like "14 Nov"}.\n'
                                 f"<record id=\"fb\" type=\"feedback\">{words}</record>", usage, budget=600, max_out=200)
            if isinstance(data, dict) and data.get("attr") and data.get("value"):
                attr = norm_attr(str(data["attr"]))
                if data.get("kind") == "date":
                    d = parse_day_text(str(data["value"]), world.day1)
                    if d:
                        ev = {"value": d.isoformat(), "display": fmt_day(d), "old": None}
                else:
                    pid = resolve_person(world, str(data["value"]), org, hint_attr=attr)
                    if pid:
                        ev = {"value": pid, "display": world.name(pid), "old": None}
        if ev and client and attr:                 # 'Northbeam billing' -> 'billing' (client named in the words)
            names = {a for a, c in world.client_aliases([r for r in world.records if r.get("client") == client]).items()
                     if c == client} | set(world.client_name.get(client, client).lower().split())
            attr = " ".join(w for w in attr.split() if w not in names) or attr
        if ev and client:
            srcs = []
            if viewer is not None and viewer != person:
                # Feedback is private to the person who gave it. For anyone else the correction counts only if a
                # record THEY can see already supports the corrected value; that record becomes the source.
                srcs = _support(world, viewer, client, attr, ev, at)
                if not srcs:
                    continue
            ev.update(at=at, day=day, sources=srcs, kind="correction", authority=2, by=person)
            out.append({"client": client, "attr": attr, "event": ev})
    return out


def _support(world, viewer, client, attr, ev, until):
    """Ids of records the viewer can see (up to the correction) that state the corrected value for this topic."""
    if F.PERSON_ID.match(str(ev["value"])):
        p = world.people.get(ev["value"], {})
        names = [n.lower() for n in [p.get("name", "")] + p.get("name", "").split()[:1] if n]
    else:
        names = [ev["display"].lower(), str(ev["value"])]
    topic = [w for w in attr.lower().split() if len(w) >= 3]
    out = []
    for r in world.visible(viewer, until_ts=until):
        if r.get("client") not in (client, None, ""):
            continue
        text = world_text(r).lower()
        if any(n and n in text for n in names) and (not topic or any(t in text for t in topic)):
            out.append(r["id"])
    return out[-2:]

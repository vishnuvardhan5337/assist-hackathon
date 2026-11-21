"""Everything the program knows, loaded from the store, plus the privacy gate.

The single most important function here is `World.visible(person)`.
Every brief and every answer is built ONLY from what it returns.
"""
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import store

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ------------------------------------------------------------------ time helpers
def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def local_date(ts: str, zone: str) -> date:
    return parse_ts(ts).astimezone(ZoneInfo(zone or "Asia/Kolkata")).date()


def fmt_day(d) -> str:
    """2026-11-14 -> '14 Nov' (the form the answer keys use)."""
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    return f"{d.day} {MONTHS[d.month - 1]}"


def next_weekday(d: date, name: str, strictly_after: bool = True) -> date:
    target = WEEKDAYS.index(name.lower())
    delta = (target - d.weekday()) % 7
    if delta == 0 and strictly_after:
        delta = 7
    return d + timedelta(days=delta)


def parse_day_text(text: str, ref: date):
    """'14 Nov', 'Nov 14', '14th', '2026-11-14' -> date (year taken from ref)."""
    t = text.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})", t)
    if m and m[2][:3].title() in MONTHS:
        return date(ref.year, MONTHS.index(m[2][:3].title()) + 1, int(m[1]))
    m = re.match(r"([A-Za-z]{3})[a-z]*\s+(\d{1,2})", t)
    if m and m[1][:3].title() in MONTHS:
        return date(ref.year, MONTHS.index(m[1][:3].title()) + 1, int(m[2]))
    return None


# ------------------------------------------------------------------ the world
class World:
    def __init__(self, con):
        self.con = con
        self.ingested_day = store.get_meta(con, "ingested_day", 0)
        self.records = []
        for row in con.execute("SELECT json, day FROM records ORDER BY at, id"):
            r = json.loads(row["json"])
            r["_day"] = row["day"]
            self.records.append(r)
        self.by_id = {r["id"]: r for r in self.records}
        root = store.pack_root()
        self.people = {}
        self.org_of = {}
        try:
            with open(os.path.join(root, "data", "people.json"), encoding="utf-8") as f:
                for org, v in json.load(f).items():
                    for p in v.get("people", []):
                        p = dict(p, org=org)
                        self.people[p["id"]] = p
                        self.org_of[p["id"]] = org
        except (OSError, ValueError):
            pass
        try:
            with open(os.path.join(root, "data", "clients.json"), encoding="utf-8") as f:
                self.clients = json.load(f)
        except (OSError, ValueError):
            self.clients = []
        self.client_name = {c["id"]: c.get("name", c["id"]) for c in self.clients}
        self.day1 = self._day_one_date()
        self.feedback = [json.loads(r["json"]) for r in con.execute("SELECT json FROM feedback ORDER BY day")]
        self.forgets = [dict(r) for r in con.execute("SELECT person, words, day FROM forgets")]
        self.llm_facts = {}
        for row in con.execute("SELECT record_id, json FROM llm_facts"):
            self.llm_facts.setdefault(row["record_id"], []).append(json.loads(row["json"]))

    def _day_one_date(self) -> date:
        """Calendar date of day 1, worked out from the data (most common local date of day-1 records)."""
        c = Counter(local_date(r["at"], "Asia/Kolkata") for r in self.records if r["_day"] == 1)
        return c.most_common(1)[0][0] if c else date(2026, 11, 2)

    def day_index(self, d: date) -> int:
        return (d - self.day1).days + 1

    # ---------------------------------------------------------------- people
    def zone(self, pid) -> str:
        return self.people.get(pid, {}).get("zone") or "Asia/Kolkata"

    def name(self, pid) -> str:
        if pid in self.people:
            return self.people[pid]["name"]
        if isinstance(pid, str) and pid.startswith("ext:"):
            return pid[4:].split("@")[0].replace(".", " ").title()
        return str(pid)

    def first(self, pid) -> str:
        return self.name(pid).split()[0]

    def role(self, pid) -> str:
        return (self.people.get(pid, {}).get("role") or "").lower()

    def authority(self, author) -> int:
        """0 = intern (cannot overrule anyone), 1 = everyone else, docs and clients."""
        return 0 if "intern" in self.role(author) else 1

    def people_named(self, word: str, org: str):
        """All people in `org` whose first or last name (or full name) is `word`."""
        w = word.lower().strip()
        out = []
        for p in self.people.values():
            if p["org"] != org:
                continue
            parts = p["name"].lower().split()
            if w == p["name"].lower() or w in parts:
                out.append(p["id"])
        return out

    # ---------------------------------------------------------------- privacy gate
    def can_see(self, person, r) -> bool:
        if r.get("org") != self.org_of.get(person):
            return False                                  # other company: never
        vis = r.get("visibility", "team")
        if vis == "team":
            return True
        if vis == "private":
            return r.get("author") == person
        if vis == "restricted":
            return person in (r.get("readers") or [])
        return False                                      # unknown visibility: fail closed

    def forgotten_terms(self, person):
        """Content words of everything `person` asked us to forget."""
        out = []
        for f in self.forgets:
            if f["person"] == person:
                out.append(forget_terms(f["words"]))
        for fb in self.feedback:
            if fb.get("type") == "forget" and fb.get("person") == person:
                out.append(forget_terms(fb.get("words", "")))
        return [t for t in out if t]

    def is_forgotten(self, r, terms_list) -> bool:
        text = record_text(r).lower()
        return any(all(t in text for t in terms) for terms in terms_list)

    def visible(self, person, until_ts=None):
        """THE gate: records `person` may see, ingested so far, up to `until_ts`, minus forgotten ones."""
        until = parse_ts(until_ts) if until_ts else None
        forgotten = self.forgotten_terms(person)
        out = []
        for r in self.records:
            if not self.can_see(person, r):
                continue
            if until and parse_ts(r["at"]) > until:
                continue
            if forgotten and r.get("author") == person and self.is_forgotten(r, forgotten):
                continue
            out.append(r)
        return out

    # ---------------------------------------------------------------- clients
    def client_aliases(self, recs):
        """Short names for clients learned from the data the asker can see.

        - the client id and every word of its name ('Cobaltline', 'Northbeam', 'Retail' is skipped if shared)
        - prefixes of 4+ letters of the id ('Cobalt')
        - initials of the name ('CL' for Cobaltline Logistics)
        - all-caps tokens that only ever appear in records tagged with one client ('NB')
        """
        aliases = {}
        for c in self.clients:
            cid, name = c["id"], c.get("name", c["id"])
            aliases[cid.lower()] = cid
            aliases[name.lower()] = cid
            aliases[name.split()[0].lower()] = cid
            initials = "".join(w[0] for w in name.split()).lower()
            if len(initials) >= 2:
                aliases[initials] = cid
        seen = {}
        for r in recs:
            if not r.get("client"):
                continue
            for tok in re.findall(r"\b[A-Z]{2,4}\b", record_text(r)):
                seen.setdefault(tok.lower(), Counter())[r["client"]] += 1
        for tok, cnt in seen.items():
            if tok in aliases or len(cnt) != 1:
                continue
            cid, n = cnt.most_common(1)[0]
            if n >= 1:
                aliases[tok] = cid
        return aliases

    def find_client(self, text, recs):
        """Return (client_id, phrase) for the first client mentioned in `text`, or (None, None)."""
        aliases = self.client_aliases(recs)
        low = text.lower()
        best = None
        for alias, cid in aliases.items():
            m = re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", low)
            if m and (best is None or len(alias) > len(best[2])):
                best = (cid, text[m.start():m.end()], alias)
        if best:
            return best[0], best[1]
        # prefix of an id, e.g. 'Cobalt' for cobaltline
        for tok in re.findall(r"[A-Za-z]{4,}", text):
            for c in self.clients:
                if c["id"].lower().startswith(tok.lower()) and tok.lower() != c["id"].lower():
                    return c["id"], tok
        # an acronym whose letters run through the client name in order, e.g. 'NB' -> NorthBeam
        for tok in re.findall(r"\b[A-Z]{2,4}\b", text):
            hits = [c["id"] for c in self.clients if _subseq(tok.lower(), c.get("name", c["id"]).lower())]
            if len(hits) == 1:
                return hits[0], tok
        return None, None

    def client_profile(self, cid, org="org_kp"):
        """Short facts about a client, used only when the model must resolve a nickname."""
        domains, team = Counter(), Counter()
        for r in self.records:
            if r.get("client") != cid or r.get("org") != org:
                continue
            for who in [r.get("author")] + list(r.get("to") or []) + list(r.get("attendees") or []):
                if isinstance(who, str) and who.startswith("ext:") and "@" in who:
                    domains[who.split("@")[1]] += 1
                elif who in self.people:
                    team[who] += 1
        zones = Counter(self.zone(p) for p, _ in team.most_common(8))
        return {"id": cid, "name": self.client_name.get(cid, cid),
                "client_email_domains": [d for d, _ in domains.most_common(3)],
                "our_team_zones": dict(zones),
                "our_team": [f"{self.name(p)} ({self.zone(p)})" for p, _ in team.most_common(5)]}


def _subseq(short, long):
    if not short or short[0] != long[0]:
        return False
    it = iter(long)
    return all(ch in it for ch in short)


def record_text(r) -> str:
    parts = [r.get("title") or "", r.get("subject") or "", r.get("name") or "", r.get("body") or ""]
    return " ".join(p for p in parts if p)


STOPWORDS = set("""a an the and or but of to in on at for with from by about is are was were be been
i me my we our you your he she it they them this that these those what said say forget please
anything something all any have has had do did not no yes so as just""".split())


def forget_terms(words: str):
    """'Forget what I said about the Dubai trip.' -> ['dubai', 'trip']"""
    w = re.sub(r"(?i)^\s*forget\s+(what|everything|anything)?\s*(i|we)?\s*(said|told you|mentioned)?\s*(about)?", "", words)
    toks = [t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", w)]
    toks = [t for t in toks if t not in STOPWORDS]
    # the most specific word(s): keep up to 2, prefer capitalised words in the original
    caps = [t.lower() for t in re.findall(r"\b[A-Z][a-z]{2,}\b", w)]
    keep = [t for t in toks if t in caps] or toks
    return keep[:2]

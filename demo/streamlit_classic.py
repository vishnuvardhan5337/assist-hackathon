"""Meeting board: a Streamlit + shadcn demo UI for the video. Not part of the graded solution.

  pip install streamlit streamlit-shadcn-ui
  streamlit run demo/streamlit_app.py

Every action runs the SAME `main.py` the organisers' harness runs, so what you see is what is scored.
It keeps its own memory in .demo_store/ and never touches harness runs. Opening a source record goes
through the same privacy gate: it shows only if the chosen person may see it.
Look and reusable pieces: demo/ui_kit.py.
"""
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit_shadcn_ui as ui

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("R3_STORE", os.path.join(ROOT, ".demo_store"))
from assist import store  # noqa: E402
from assist.world import World  # noqa: E402
import ui_kit as K  # noqa: E402

st.set_page_config(page_title="Meeting board", layout="wide")
K.inject_theme()


# ------------------------------------------------------------------ data helpers
def run_main(*args):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), *args], capture_output=True, text=True,
                       cwd=ROOT, env=os.environ.copy(), timeout=120)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"error": (p.stderr or p.stdout)[-400:]}


def pack_json(*parts):
    with open(os.path.join(store.pack_root(), "data", *parts), encoding="utf-8") as f:
        return json.load(f)


def fmt_day(d):
    try:
        x = date.fromisoformat(str(d)[:10])
        return f"{x.day} {x.strftime('%b')}"
    except ValueError:
        return str(d)


def as_date(v, fallback):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return fallback


people = {p["id"]: p for v in pack_json("people.json").values() for p in v["people"]}
ids = list(people)
first = lambda pid: people.get(pid, {}).get("name", pid).split()[0]          # noqa: E731
days_dir = os.path.join(store.pack_root(), "data", "days")
available = sorted(int(f[4:6]) for f in os.listdir(days_dir) if f.startswith("day-"))
w = World(store.connect())
loaded = w.ingested_day
day1 = w.day1 if loaded else date(2026, 11, 2)

# ------------------------------------------------------------------ header
head_l, head_r = st.columns([3, 2], vertical_alignment="bottom")
with head_l:
    K.page_header("Meeting board", f"Memory loaded up to day {loaded}" if loaded else "No memory loaded yet")
with head_r:
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    with c1:
        upto = ui.select("Memory up to", available, value=loaded if loaded in available else available[-1],
                         format_func=lambda d: f"Day {d}", key="upto")
    with c2:
        if ui.button("Load", key="load", width="stretch"):
            with st.spinner(f"Loading days 1 to {upto}"):
                for d in range(1, int(upto) + 1):
                    run_main("ingest", "--day", str(d))
            st.session_state.pop("answer", None)
            st.rerun()

st.markdown('<div class="k-gap"></div>', unsafe_allow_html=True)
if not loaded:
    K.card("Get started", K.empty("Choose how many days to load, then press Load."))
    st.stop()

# ------------------------------------------------------------------ filters
f1, f2, _ = st.columns([2, 1.2, 1.8], vertical_alignment="bottom")
with f1:
    person = ui.select("Person", ids, value="p02" if "p02" in ids else ids[0],
                       format_func=lambda i: f"{people[i]['name']} · {people[i]['role']}", key="person")
with f2:
    default_day = day1 + timedelta(days=max(loaded, 1))
    when = as_date(ui.date_picker("Meetings on", value=default_day, key="when"), default_day)

briefs = run_main("brief", "--person", person, "--date", when.isoformat())
briefs = briefs if isinstance(briefs, list) else []
events = {r["event_id"]: r for r in w.records if r.get("type") == "event"}
zone = w.zone(person)

# ------------------------------------------------------------------ summary metrics
n_changes = sum(len(b["changed"]) for b in briefs)
n_prom = len({p["sources"][0] for b in briefs for p in b["promises"]})
tokens = sum((b.get("usage") or {}).get("input_tokens", 0) for b in briefs)
m1, m2, m3, m4 = st.columns(4)
with m1:
    ui.metric_card("Meetings", str(len(briefs)), description=when.strftime("%A, %d %b"), key="m1")
with m2:
    ui.metric_card("Changes", str(n_changes), description="since each last meeting", key="m2")
with m3:
    ui.metric_card("Open promises", str(n_prom), description="not yet delivered", key="m3")
with m4:
    ui.metric_card("Model tokens", str(tokens), description="for these briefs", key="m4")

st.markdown('<div class="k-gap"></div>', unsafe_allow_html=True)
main, side = st.columns([2, 1], gap="large")

# ------------------------------------------------------------------ main: the brief
with main:
    K.section_label("Brief")
    if not briefs:
        K.card("No meetings", K.empty(f"{first(person)} has no meetings on {fmt_day(when.isoformat())}."))
    else:
        def title_of(mid):
            ev = events.get(mid, {})
            t = ev.get("title", mid)
            if ev.get("start"):
                t += " · " + datetime.fromisoformat(ev["start"].replace("Z", "+00:00")).astimezone(ZoneInfo(zone)).strftime("%H:%M")
            return t
        mids = [b["meeting_id"] for b in briefs]
        sel = ui.tabs(mids, format_func=title_of, key=f"tabs_{person}_{when}") if len(mids) > 1 else mids[0]
        b = next(x for x in briefs if x["meeting_id"] == sel)
        u = b.get("usage") or {}
        meta = f"{b['meeting_id']} · {u.get('input_tokens', 0)} tokens · {u.get('ms', 0)} ms"
        if b["status"] != "brief":
            ui.badges([ui.BadgeItem("Nothing new", "secondary")], key="st_badge")
            K.card(events.get(sel, {}).get("title", sel),
                   K.empty("Nothing changed since this person's last meeting with this client."), meta)
        else:
            ui.badges([ui.BadgeItem(f"{len(b['changed'])} changes", "default"),
                       ui.BadgeItem(f"{len(b['promises'])} open promises", "outline")], key="st_badge")
            K.card("What changed", K.change_list(b["changed"]))
            if b["promises"]:
                K.section_label("Open promises")
                K.promise_table(b["promises"], when.isoformat(), first, fmt_day, key=f"prom_{sel}")
            if b.get("question_to_ask"):
                q = b["question_to_ask"]
                K.card("Question to ask", K.rows([(q["text"], K.sources(q["sources"]))]), meta)

# ------------------------------------------------------------------ side: quick question and tools
with side:
    K.section_label("Quick question")
    asker = ui.select("Ask as", ids, value=person, format_func=lambda i: people[i]["name"], key=f"asker_{person}")
    question = ui.input("Question", value="Who owns billing for NB?", key="question")
    at_default = datetime.combine(day1 + timedelta(days=loaded - 1), datetime.min.time()) + timedelta(hours=18)
    at_iso = at_default.strftime("%Y-%m-%dT%H:%M:%SZ")
    if ui.button("Ask", key="ask", width="stretch") and question.strip():
        st.session_state["answer"] = (asker, question, run_main("ask", "--person", asker, "--at", at_iso, "--ask", question))
    st.markdown(f'<div class="k-meta">Asked as of {at_iso}</div>', unsafe_allow_html=True)
    if "answer" in st.session_state:
        who, qq, res = st.session_state["answer"]
        with st.container(border=True):
            K.answer_view(people[who]["name"], qq, res)

    st.markdown('<div class="k-gap"></div>', unsafe_allow_html=True)
    tool = ui.tabs(["Forget", "Record", "Memory"], key="tools", variant="line")
    if tool == "Forget":
        what = ui.input("What to forget", value="Forget what I said about the Dubai trip.", key="what")
        if ui.button("Forget", key="forget_btn", variant="outline", width="stretch"):
            run_main("forget", "--person", asker, "--what", what)
            st.markdown(K.empty(f"Forgotten for {first(asker)}. Ask again to check."), unsafe_allow_html=True)
    elif tool == "Record":
        rid = ui.input("Record id", placeholder="e.g. rgg1ex2g", key="rid").strip()
        if rid:
            r = w.by_id.get(rid)
            if not r:
                st.markdown(K.empty("No such record."), unsafe_allow_html=True)
            elif not w.can_see(asker, r):
                ui.badges([ui.BadgeItem("Not allowed", "outline")], key="rec_badge")
                st.markdown(K.empty(f"{people[asker]['name']} may not see this record."), unsafe_allow_html=True)
            else:
                body = (r.get("subject") + ": " if r.get("subject") else "") + (r.get("body") or "")[:500]
                K.card(w.name(r.get("author")), f'<div style="font-size:14px;margin-top:8px">{K.e(body)}</div>',
                       f"{r.get('type')} · {r.get('at', '')[:10]} · {rid}")
    else:
        told = [(f"{first(f.get('person'))} · {f.get('type')}", f.get("words", "")) for f in w.feedback] + \
               [(f"{first(f['person'])} · forget", f["words"]) for f in w.forgets]
        K.card("What it has been told", K.rows([(b, a) for a, b in told]) if told else K.empty("Nothing yet."))

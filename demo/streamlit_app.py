"""Work assistant demo UI (Streamlit). Not part of the graded solution.

  streamlit run demo/streamlit_app.py

Layout: top bar, left navigation, page in the middle, "viewing as" panel on the right.
Every action runs the SAME `main.py` the organisers' harness runs, so what you see is what is scored.
It keeps its own memory in .demo_store/. Opening a record goes through the same privacy gate.
(The previous meeting-board layout is kept in demo/streamlit_classic.py.)
"""
import html
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("R3_STORE", os.path.join(ROOT, ".demo_store"))
from assist import store  # noqa: E402
from assist.world import World  # noqa: E402

TEAL, TEAL_BG, PLUM, SAND, INK, MUTED, LINE = "#2F7F86", "#E3F1F1", "#5B2149", "#F8F4EF", "#1F2328", "#6B7280", "#E6E2DC"
st.set_page_config(page_title="Work assistant", layout="wide", initial_sidebar_state="collapsed")
st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
html, body, [class*="css"], .stApp {{font-family:'Inter',sans-serif;color:{INK}}}
.stApp {{background:#FFFFFF}}
header[data-testid="stHeader"] {{display:none}}
.block-container {{padding-top:0 !important;max-width:1320px}}
.topbar {{display:flex;align-items:center;gap:28px;border-bottom:1px solid {LINE};padding:14px 4px;margin:0 0 22px}}
.brand {{display:flex;align-items:center;gap:10px;font-weight:600;font-size:17px}}
.logo {{width:28px;height:28px;border-radius:50%;background:{INK};color:#fff;display:flex;align-items:center;justify-content:center;font-size:14px}}
.toptab {{font-size:14px;color:{MUTED};padding-bottom:4px}}
.toptab.on {{color:{INK};border-bottom:2px solid #D9534F}}
.spacer {{flex:1}}
.pill {{font-size:12px;border:1px solid {LINE};border-radius:6px;padding:4px 10px;color:{MUTED}}}
.pill.teal {{background:{TEAL_BG};color:{TEAL};border-color:{TEAL_BG}}}
h1.page {{font-size:32px;font-weight:400;margin:0 0 4px;letter-spacing:-0.3px}}
.sub {{color:{MUTED};font-size:14px;margin:0 0 18px}}
/* left navigation: radio styled as menu items */
div[data-testid="stRadio"] > label {{display:none}}
div[data-testid="stRadio"] div[role="radiogroup"] {{gap:2px}}
div[data-testid="stRadio"] div[role="radiogroup"] > label {{padding:10px 12px;border-radius:8px;margin:0;width:100%;align-items:flex-start}}
div[data-testid="stRadio"] div[role="radiogroup"] > label:hover {{background:#F3F4F6}}
div[data-testid="stRadio"] div[role="radiogroup"] > label:has(input:checked) {{background:{TEAL_BG}}}
div[data-testid="stRadio"] div[role="radiogroup"] > label > div:first-child {{display:none}}
div[data-testid="stRadio"] p {{font-size:15px;font-weight:500}}
div[data-testid="stRadio"] [data-testid="stCaptionContainer"] p, div[data-testid="stRadio"] small {{font-size:12.5px;font-weight:400;color:{MUTED}}}
/* expanders as accordion sections */
div[data-testid="stExpander"] details {{border:1px solid {LINE};border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
div[data-testid="stExpander"] details[open] {{background:{SAND}}}
div[data-testid="stExpander"] summary p {{font-size:16px;font-weight:500}}
/* buttons */
.stButton > button[kind="primary"] {{background:{PLUM};border-color:{PLUM};color:#fff;border-radius:6px}}
.stButton > button[kind="primary"]:hover {{background:#47173A;border-color:#47173A}}
.stButton > button[kind="secondary"] {{border-radius:20px;font-size:13px;padding:2px 12px;min-height:32px}}
.sect {{font-size:12px;font-weight:600;color:{PLUM};margin:6px 0 8px;text-transform:none}}
.row {{display:flex;justify-content:space-between;gap:16px;padding:9px 0;border-bottom:1px solid {LINE};font-size:14.5px}}
.row:last-child {{border-bottom:0}}
.src {{font-family:ui-monospace,monospace;font-size:12px;color:{TEAL};white-space:nowrap}}
.card {{background:#fff;border:1px solid {LINE};border-radius:8px;padding:14px 16px;margin-bottom:12px}}
.badge {{display:inline-block;font-size:12px;padding:2px 9px;border-radius:10px;margin-right:6px}}
.b-teal {{background:{TEAL_BG};color:{TEAL}}} .b-plum {{background:#F3E8EF;color:{PLUM}}} .b-gray {{background:#F1F1EF;color:{MUTED}}}
.answer {{font-size:16px;line-height:1.5;margin:6px 0}}
.meta {{font-size:12px;color:{MUTED}}}
.rnav {{border:1px solid {LINE};border-radius:6px;overflow:hidden;margin-bottom:18px}}
.rnav .hd {{background:#F7F7F7;padding:10px 12px;font-weight:600;font-size:14px;border-bottom:1px solid {LINE}}}
.rnav .it {{padding:8px 12px;font-size:13px;color:{MUTED};border-bottom:1px solid #F0F0F0}}
.rnav .it.on {{color:{TEAL};border-left:3px solid {TEAL};padding-left:9px}}
.avatar {{width:78px;height:78px;border-radius:50%;background:{TEAL};color:#fff;font-size:28px;display:flex;align-items:center;
  justify-content:center;margin:6px auto 8px;box-shadow:0 0 0 4px #fff,0 0 0 7px {TEAL_BG}}}
.who {{text-align:center;font-weight:600;font-size:15px}} .role {{text-align:center;font-size:12.5px;color:{MUTED}}}
.meter {{height:6px;background:#EEE;border-radius:4px;overflow:hidden;margin:6px 0 4px}} .meter > div {{height:6px;background:{TEAL}}}
</style>""", unsafe_allow_html=True)

e = html.escape


# ------------------------------------------------------------------ data helpers
def run_main(*args):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), *args], capture_output=True, text=True,
                       cwd=ROOT, env=os.environ.copy(), timeout=180)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"error": (p.stderr or p.stdout)[-400:]}


def pack_json(*parts):
    with open(os.path.join(store.pack_root(), "data", *parts), encoding="utf-8") as f:
        return json.load(f)


def fmt_day(d):
    x = date.fromisoformat(str(d)[:10])
    return f"{x.day} {x.strftime('%b')}"


def srcs(ids):
    return f'<span class="src">{" · ".join(e(i) for i in ids or [])}</span>'


def rows(items):
    return "".join(f'<div class="row"><span>{a}</span>{b}</div>' for a, b in items)


def answer_card(name, q, res):
    u = res.get("usage") or {}
    out = f'<div class="card"><div class="meta">{e(name)} asked “{e(q)}”</div>'
    if res.get("refused"):
        out += f'<span class="badge b-plum">Not shared</span><div class="answer">{e(res["refused"]["reason"])}</div>'
    elif res.get("clarify"):
        c = res["clarify"]
        out += f'<span class="badge b-gray">Needs one detail</span><div class="answer">{e(c["question"])}</div>'
        out += rows([(e(o["text"]), srcs([o["id"]])) for o in c.get("options", [])])
    elif res.get("answer"):
        a = res["answer"]
        out += f'<div class="answer">{e(a["text"])}</div><div>{srcs(a.get("sources"))}</div>'
    elif res.get("error"):
        out += f'<div class="answer">Something went wrong: {e(res["error"])}</div>'
    out += f'<div class="meta" style="margin-top:8px">{u.get("input_tokens", 0)} tokens · {u.get("ms", 0)} ms</div></div>'
    st.markdown(out, unsafe_allow_html=True)


people = {p["id"]: p for v in pack_json("people.json").values() for p in v["people"]}
ids = list(people)
first = lambda pid: people.get(pid, {}).get("name", pid).split()[0]          # noqa: E731
days_dir = os.path.join(store.pack_root(), "data", "days")
available = sorted(int(f[4:6]) for f in os.listdir(days_dir) if f.startswith("day-"))
w = World(store.connect())
loaded = w.ingested_day
day1 = w.day1 if loaded else date(2026, 11, 2)
at_iso = (datetime.combine(day1 + timedelta(days=max(loaded, 1) - 1), datetime.min.time())
          + timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M:%SZ")

PAGES = {"Meeting brief": "What changed since the last meeting", "Ask": "Questions answered with sources",
         "Privacy check": "Same question, two people", "Memory": "Feedback, corrections, forget",
         "Records": "Open a source record"}

# ------------------------------------------------------------------ top bar
st.markdown(f"""<div class="topbar"><div class="brand"><div class="logo">A</div>Work assistant</div>
<span class="toptab on">Kitepoint Studio</span><span class="toptab">Briefs</span><span class="toptab">Questions</span>
<span class="toptab">Memory</span><div class="spacer"></div>
<span class="pill">{"Memory to day " + str(loaded) if loaded else "No memory yet"}</span>
<span class="pill teal">Same engine as the judges' harness</span></div>""", unsafe_allow_html=True)

nav, main, right = st.columns([1.15, 3.2, 1.15], gap="large")

# ------------------------------------------------------------------ right panel: viewing as + memory loader
with right:
    st.markdown('<div class="rnav"><div class="hd">Memory</div></div>', unsafe_allow_html=True)
    upto = st.selectbox("Load days 1 to", available, index=(available.index(loaded) if loaded in available else
                        min(2, len(available) - 1)), format_func=lambda d: f"Day {d}")
    if st.button("Load memory", type="primary", use_container_width=True):
        with st.spinner(f"Reading days 1 to {upto}"):
            for d in range(1, int(upto) + 1):
                run_main("ingest", "--day", str(d))
        for k in ("answer", "split"):
            st.session_state.pop(k, None)
        st.rerun()
    pct = int(100 * loaded / max(available)) if loaded else 0
    st.markdown(f'<div class="meta" style="text-align:center;margin-top:10px">Memory loaded</div>'
                f'<div class="meter"><div style="width:{pct}%"></div></div>'
                f'<div class="meta" style="text-align:center"><b style="color:{TEAL}">Day {loaded}</b> / {max(available)}</div>',
                unsafe_allow_html=True)
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    person = st.selectbox("Viewing as", ids, index=ids.index("p02") if "p02" in ids else 0,
                          format_func=lambda i: people[i]["name"])
    p = people[person]
    initials = "".join(x[0] for x in p["name"].split()[:2])
    st.markdown(f'<div class="avatar">{e(initials)}</div><div class="who">{e(p["name"])}</div>'
                f'<div class="role">{e(p["role"])} · {e(p.get("team", ""))}</div>'
                f'<div class="role">{e(p.get("zone", ""))}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="meta" style="text-align:center;margin-top:10px">Everything on this page is limited to '
                f'what {e(first(person))} may see.</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ left navigation
with nav:
    page = st.radio("Section", list(PAGES), captions=list(PAGES.values()), key="page")
    st.markdown(f'<div style="border-top:1px solid {LINE};margin:18px 0 10px"></div>'
                f'<div class="meta">Four commands underneath:<br>ingest · brief · ask · forget</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ pages
with main:
    if not loaded:
        st.markdown('<h1 class="page">Welcome</h1><p class="sub">Choose how many days to load on the right, '
                    'then press Load memory.</p>', unsafe_allow_html=True)
        st.stop()

    if page == "Meeting brief":
        zone = w.zone(person)
        events = {}
        for r in w.visible(person):
            if r.get("type") == "event" and r.get("event_id"):
                events[r["event_id"]] = r
        mine = [ev for ev in events.values() if person in (ev.get("attendees") or []) and ev.get("start")]
        mdays = sorted({datetime.fromisoformat(ev["start"].replace("Z", "+00:00")).astimezone(ZoneInfo(zone)).date()
                        for ev in mine})
        after = day1 + timedelta(days=loaded)              # the day after the loaded memory
        default = next((d for d in mdays if d >= after), mdays[-1] if mdays else after)
        h1, h2 = st.columns([3, 1.3], vertical_alignment="bottom")
        with h1:
            st.markdown('<h1 class="page">Meeting brief</h1>'
                        f'<p class="sub">What {e(first(person))} should know before each meeting.</p>', unsafe_allow_html=True)
        with h2:
            when = st.selectbox("Meetings on", mdays or [after], index=(mdays.index(default) if default in mdays else 0),
                                format_func=lambda d: d.strftime("%a %d %b"))
        briefs = run_main("brief", "--person", person, "--date", when.isoformat())
        briefs = briefs if isinstance(briefs, list) else []
        if not briefs:
            st.markdown(f'<div class="card">{e(first(person))} has no meetings on {e(fmt_day(when.isoformat()))}.</div>',
                        unsafe_allow_html=True)
        for n, b in enumerate(briefs):
            ev = events.get(b["meeting_id"], {})
            t = ev.get("title", b["meeting_id"])
            if ev.get("start"):
                t += "  ·  " + datetime.fromisoformat(ev["start"].replace("Z", "+00:00")).astimezone(ZoneInfo(zone)).strftime("%H:%M")
            label = f"{t}  —  {len(b['changed'])} changes" if b["status"] == "brief" else f"{t}  —  nothing new"
            with st.expander(label, expanded=(n == 0)):
                u = b.get("usage") or {}
                if b["status"] != "brief":
                    st.markdown('<span class="badge b-gray">Nothing new</span><p class="sub" style="margin-top:8px">'
                                'Nothing changed since the last meeting with this client, so the brief says so '
                                'instead of adding filler.</p>', unsafe_allow_html=True)
                    continue
                st.markdown(f'<span class="badge b-teal">{len(b["changed"])} changes</span>'
                            f'<span class="badge b-plum">{len(b["promises"])} open promises</span>', unsafe_allow_html=True)
                st.markdown('<div class="sect">What changed</div><div class="card">'
                            + rows([(e(c["text"]), srcs(c["sources"])) for c in b["changed"]]) + '</div>',
                            unsafe_allow_html=True)
                if b["promises"]:
                    today = when.isoformat()
                    st.markdown('<div class="sect">Open promises</div><div class="card">' + rows([
                        (f'<b>{e(first(pr["owner"]))}</b> · {e(pr["what"])}',
                         f'<span class="meta">due {e(fmt_day(pr["due"]))}'
                         f'{" · <span style=color:#B42318>overdue</span>" if pr["due"] < today else ""}</span> {srcs(pr["sources"])}')
                        for pr in b["promises"]]) + '</div>', unsafe_allow_html=True)
                if b.get("question_to_ask"):
                    q = b["question_to_ask"]
                    st.markdown('<div class="sect">Question to ask</div><div class="card">'
                                + rows([(e(q["text"]), srcs(q["sources"]))]) + '</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="meta">{e(b["meeting_id"])} · {u.get("input_tokens", 0)} tokens · '
                            f'{u.get("ms", 0)} ms</div>', unsafe_allow_html=True)

    elif page == "Ask":
        st.markdown('<h1 class="page">Ask</h1>'
                    f'<p class="sub">Answers come only from records {e(first(person))} may see, as of {e(at_iso)}.</p>',
                    unsafe_allow_html=True)
        examples = ["Who owns billing for NB?", "Who owns billing for the Mumbai client?", "Is the Friday thing done?",
                    "When is my Dubai trip?", "Is Kavya on leave?"]
        cols = st.columns(len(examples))
        for c, ex in zip(cols, examples):
            if c.button(ex, key="ex_" + ex, use_container_width=True):
                st.session_state["q"] = ex
        q = st.text_input("Question", key="q", placeholder="Who owns billing for NB?")
        if st.button("Ask", type="primary") and q.strip():
            st.session_state["answer"] = (person, q, run_main("ask", "--person", person, "--at", at_iso, "--ask", q))
        if "answer" in st.session_state:
            who, qq, res = st.session_state["answer"]
            answer_card(people[who]["name"], qq, res)

    elif page == "Privacy check":
        st.markdown('<h1 class="page">Privacy check</h1><p class="sub">The same question from two people. '
                    'Each answer uses only what that person may see.</p>', unsafe_allow_html=True)
        q = st.text_input("Question", value="What did Meera say about Dubai?", key="pq")
        c1, c2 = st.columns(2)
        a = c1.selectbox("Person A", ids, index=ids.index("p02") if "p02" in ids else 0, format_func=lambda i: people[i]["name"])
        b_ = c2.selectbox("Person B", ids, index=ids.index("p03") if "p03" in ids else 1, format_func=lambda i: people[i]["name"])
        if st.button("Ask both", type="primary") and q.strip():
            st.session_state["split"] = (q, a, run_main("ask", "--person", a, "--at", at_iso, "--ask", q),
                                         b_, run_main("ask", "--person", b_, "--at", at_iso, "--ask", q))
        if "split" in st.session_state:
            qq, pa, ra, pb, rb = st.session_state["split"]
            l, r = st.columns(2)
            with l:
                answer_card(people[pa]["name"], qq, ra)
            with r:
                answer_card(people[pb]["name"], qq, rb)

    elif page == "Memory":
        st.markdown('<h1 class="page">Memory</h1><p class="sub">What the assistant has been told. '
                    'Each item belongs to the person who said it.</p>', unsafe_allow_html=True)
        told = [(f'<b>{e(first(f.get("person")))}</b> · {e(f.get("type", ""))}', e(f.get("words", ""))) for f in w.feedback] + \
               [(f'<b>{e(first(f["person"]))}</b> · forget', e(f["words"])) for f in w.forgets]
        st.markdown('<div class="sect">Told so far</div><div class="card">' +
                    (rows(told) if told else '<span class="meta">Nothing yet.</span>') + '</div>', unsafe_allow_html=True)
        with st.expander(f"Forget something for {first(person)}", expanded=True):
            what = st.text_input("What to forget", value="Forget what I said about the Dubai trip.")
            if st.button("Forget", type="primary"):
                run_main("forget", "--person", person, "--what", what)
                st.success(f"Forgotten for {first(person)}. Ask again to check.")

    else:
        st.markdown('<h1 class="page">Records</h1><p class="sub">Open the record behind any source id. '
                    f'It opens only if {e(first(person))} may see it.</p>', unsafe_allow_html=True)
        rid = st.text_input("Record id", placeholder="rgg1ex2g").strip()
        if rid:
            r = w.by_id.get(rid)
            if not r:
                st.markdown('<div class="card">No such record.</div>', unsafe_allow_html=True)
            elif not w.can_see(person, r):
                st.markdown(f'<div class="card"><span class="badge b-plum">Not allowed</span>'
                            f'<div class="answer">{e(people[person]["name"])} may not see this record.</div></div>',
                            unsafe_allow_html=True)
            else:
                body = (r.get("subject") + ": " if r.get("subject") else "") + (r.get("body") or "")[:700]
                st.markdown(f'<div class="card"><div class="meta">{e(r.get("type", ""))} · {e(w.name(r.get("author")))} · '
                            f'{e(r.get("at", "")[:10])} · {e(r.get("visibility", "team"))}</div>'
                            f'<div class="answer">{e(body)}</div></div>', unsafe_allow_html=True)

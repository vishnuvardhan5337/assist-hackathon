#!/usr/bin/env python3
"""A small local demo UI for the video. Not part of the graded solution.

  python3 demo/app.py            then open http://127.0.0.1:8000

- Standard library only (no new packages).
- Every button runs the SAME `main.py` the organisers' harness runs, so what you see is what is scored.
- Uses its own memory folder (.demo_store) so it never touches harness runs.
- Clicking a source shows that record ONLY if the selected person may see it (same privacy gate).
"""
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from assist import store  # noqa: E402

os.environ.setdefault("R3_STORE", os.path.join(ROOT, ".demo_store"))
PORT = int(os.environ.get("PORT", "8000"))


def run_main(*args):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), *args], capture_output=True, text=True,
                       cwd=ROOT, env=os.environ.copy(), timeout=120)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"error": p.stdout[-500:] or p.stderr[-500:]}


def pack_json(*parts):
    with open(os.path.join(store.pack_root(), "data", *parts), encoding="utf-8") as f:
        return json.load(f)


def state():
    try:
        con = store.connect()
        day = store.get_meta(con, "ingested_day", 0) or 0
        forgets = [dict(r) for r in con.execute("SELECT person, words FROM forgets")]
        fb = [json.loads(r["json"]) for r in con.execute("SELECT json FROM feedback ORDER BY day")]
    except Exception:
        day, forgets, fb = 0, [], []
    days_dir = os.path.join(store.pack_root(), "data", "days")
    available = sorted(int(f[4:6]) for f in os.listdir(days_dir) if f.startswith("day-")) if os.path.isdir(days_dir) else []
    return {"ingested_day": day, "available_days": available, "forgets": forgets, "feedback": fb}


def record_for(rid, person):
    from assist.world import World
    w = World(store.connect())
    r = w.by_id.get(rid)
    if not r:
        return 404, {"error": "no such record"}
    if not w.can_see(person, r):
        return 403, {"error": f"{w.name(person)} is not allowed to see this record."}
    r = {k: v for k, v in r.items() if not k.startswith("_")}
    r["author_name"] = w.name(r.get("author"))
    return 200, r


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj, ctype="application/json"):
        data = obj.encode() if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/":
            return self.send(200, PAGE, "text/html")
        if u.path == "/api/meta":
            people = pack_json("people.json")
            return self.send(200, {"people": people, "clients": pack_json("clients.json"), "state": state()})
        if u.path == "/api/state":
            return self.send(200, state())
        if u.path == "/api/record":
            code, obj = record_for(q.get("id", ""), q.get("person", ""))
            return self.send(code, obj)
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        p = urlparse(self.path).path
        if p == "/api/ingest":
            upto = int(body.get("upto", 1))
            log = [run_main("ingest", "--day", str(d)) for d in range(1, upto + 1)]
            return self.send(200, {"log": log, "state": state()})
        if p == "/api/brief":
            return self.send(200, run_main("brief", "--person", body["person"], "--date", body["date"]))
        if p == "/api/ask":
            return self.send(200, run_main("ask", "--person", body["person"], "--at", body["at"], "--ask", body["ask"]))
        if p == "/api/forget":
            return self.send(200, {"result": run_main("forget", "--person", body["person"], "--what", body["what"]),
                                   "state": state()})
        return self.send(404, {"error": "not found"})


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Work Assistant That Remembers</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e4e2dc;--accent:#2f5d50;--accent2:#e8f0ec;
--warn:#8a4b00;--warnbg:#fdf1e2;--bad:#9b1c1c;--badbg:#fbeaea;--chip:#eef0f3}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:22px 32px 14px;border-bottom:1px solid var(--line);background:var(--card);display:flex;align-items:center;gap:24px;flex-wrap:wrap}
h1{font-size:19px;margin:0;letter-spacing:-.01em}h1 span{color:var(--muted);font-weight:400}
.daybar{margin-left:auto;display:flex;align-items:center;gap:10px;color:var(--muted);font-size:14px}
main{max-width:1180px;margin:0 auto;padding:24px 32px 60px;display:grid;grid-template-columns:330px 1fr;gap:24px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
.tabs{display:flex;gap:4px;margin-bottom:14px;background:var(--chip);padding:4px;border-radius:9px}
.tabs button{flex:1;border:0;background:transparent;padding:7px 0;border-radius:6px;font:inherit;font-size:14px;color:var(--muted);cursor:pointer}
.tabs button.on{background:var(--card);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.08)}
label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:12px 0 5px}
select,input,textarea{width:100%;font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:#fff}
textarea{min-height:70px;resize:vertical}
.go{margin-top:14px;width:100%;padding:10px;border:0;border-radius:8px;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}
.go:disabled{opacity:.5}.small{font-size:13px;color:var(--muted)}
.ex{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.ex button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:4px 10px;font-size:12.5px;cursor:pointer;color:var(--ink)}
.ex button:hover{border-color:var(--accent)}
.out{display:flex;flex-direction:column;gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px}
.card h2{margin:0 0 2px;font-size:16px}.meta{color:var(--muted);font-size:13px}
.badge{display:inline-block;font-size:12px;font-weight:600;padding:2px 9px;border-radius:999px;margin-left:8px;vertical-align:2px}
.b-brief{background:var(--accent2);color:var(--accent)}.b-none{background:var(--chip);color:var(--muted)}
.b-clar{background:var(--warnbg);color:var(--warn)}.b-ref{background:var(--badbg);color:var(--bad)}
h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:16px 0 6px}
ul{margin:0;padding-left:0;list-style:none}li{padding:7px 0;border-top:1px solid var(--line);display:flex;gap:10px;justify-content:space-between;align-items:baseline}
li:first-child{border-top:0}.src{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end}
.chip{font:12px ui-monospace,Menlo,monospace;background:var(--chip);border:0;border-radius:5px;padding:2px 6px;cursor:pointer;color:#33363d;white-space:nowrap}
.chip:hover{background:#dfe3ea}.answer{font-size:18px;margin:6px 0 4px}
.usage{margin-top:14px;font-size:12.5px;color:var(--muted);display:flex;gap:16px}
.empty{color:var(--muted);text-align:center;padding:50px 20px}
.rec{position:fixed;right:24px;bottom:24px;width:420px;max-height:60vh;overflow:auto;background:var(--card);border:1px solid var(--line);
border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.12);padding:16px 18px;display:none}
.rec pre{white-space:pre-wrap;font:13px/1.45 ui-monospace,Menlo,monospace;background:var(--bg);padding:10px;border-radius:8px;margin:8px 0 0}
.rec .x{float:right;border:0;background:none;font-size:18px;cursor:pointer;color:var(--muted)}
.mem{margin-top:16px;font-size:13px}.mem div{padding:4px 0;border-top:1px dashed var(--line)}
@media(max-width:860px){main{grid-template-columns:1fr}}
</style></head><body>
<header><h1>Work Assistant That Remembers <span>· Kitepoint Studio</span></h1>
<div class="daybar">Memory loaded up to <b id="dayNow">–</b>
<select id="dayPick" style="width:auto"></select><button class="go" style="width:auto;margin:0;padding:7px 14px" onclick="ingest()">Load days</button></div></header>
<main>
<section>
<div class="panel">
 <div class="tabs"><button id="t-ask" class="on" onclick="tab('ask')">Ask</button><button id="t-brief" onclick="tab('brief')">Brief</button><button id="t-forget" onclick="tab('forget')">Forget</button></div>
 <label>Person</label><select id="person"></select>
 <div id="p-ask">
  <label>Asked at (UTC)</label><input id="at" type="datetime-local">
  <label>Question</label><textarea id="q" placeholder="Who owns billing for NB?"></textarea>
  <div class="ex" id="exAsk"></div>
  <button class="go" onclick="doAsk()">Ask</button>
 </div>
 <div id="p-brief" style="display:none">
  <label>Meetings on (person's local date)</label><input id="date" type="date">
  <div class="ex" id="exBrief"></div>
  <button class="go" onclick="doBrief()">Prepare brief</button>
 </div>
 <div id="p-forget" style="display:none">
  <label>What to forget</label><input id="what" value="Forget what I said about the Dubai trip.">
  <p class="small">After this, the thing must not appear in this person's briefs or answers.</p>
  <button class="go" onclick="doForget()">Forget it</button>
 </div>
 <div class="mem" id="mem"></div>
</div>
<p class="small" style="padding:0 4px">Every button runs the same <code>main.py</code> the organisers' harness runs. Click a record id to open it — only if the selected person is allowed to see it.</p>
</section>
<section class="out" id="out"><div class="card empty">Load days, pick a person, then ask a question or prepare a brief.</div></section>
</main>
<div class="rec" id="rec"><button class="x" onclick="rec.style.display='none'">×</button><div id="recBody"></div></div>
<script>
const $=id=>document.getElementById(id);let META=null,NAMES={};
const EX_ASK=[["p02","Who owns billing for the Mumbai client?"],["p02","Is the Friday thing done?"],["p02","Send the NB deck to Arjun. Which Arjun is that?"],
["p03","What did Meera say about me?"],["p02","When is my Dubai trip?"],["p03","Is Meera travelling anywhere this month?"],["p25","Who owns the Cobalt rate card update?"],["q02","Who owns billing for Northbeam?"]];
const EX_BRIEF=[["p02","2026-11-05"],["p12","2026-11-06"],["p03","2026-11-06"],["p25","2026-11-04"]];
function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function nm(id){return NAMES[id]?`${NAMES[id]} (${id})`:id}
function chips(ids){return `<span class="src">${(ids||[]).map(i=>`<button class="chip" onclick="openRec('${esc(i)}')">${esc(i)}</button>`).join("")}</span>`}
function usage(u){return u?`<div class="usage"><span>${u.input_tokens} in / ${u.output_tokens} out tokens</span><span>$${u.cost_usd}</span><span>${u.ms} ms</span></div>`:""}
function tab(t){for(const k of["ask","brief","forget"]){$("p-"+k).style.display=k==t?"":"none";$("t-"+k).classList.toggle("on",k==t)}}
async function api(p,b){const r=await fetch(p,b?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}:{});return r.json()}
async function load(){META=await api("/api/meta");const sel=$("person");sel.innerHTML="";
 for(const [org,v] of Object.entries(META.people)){const g=document.createElement("optgroup");g.label=v.name;
  for(const p of v.people){NAMES[p.id]=p.name;const o=document.createElement("option");o.value=p.id;o.textContent=`${p.name} · ${p.role} (${p.id})`;g.appendChild(o)}sel.appendChild(g)}
 sel.value="p02";$("exAsk").innerHTML=EX_ASK.map((e,i)=>`<button onclick="exA(${i})">${esc(NAMES[e[0]]?.split(" ")[0])}: ${esc(e[1])}</button>`).join("");
 $("exBrief").innerHTML=EX_BRIEF.map((e,i)=>`<button onclick="exB(${i})">${esc(NAMES[e[0]]?.split(" ")[0])} · ${e[1]}</button>`).join("");
 const dp=$("dayPick");dp.innerHTML=META.state.available_days.map(d=>`<option value="${d}">day ${d}</option>`).join("");dp.value=Math.max(...META.state.available_days);
 showState(META.state)}
function showState(s){$("dayNow").textContent=s.ingested_day?("day "+s.ingested_day):"nothing yet";
 const d=s.ingested_day||1;const dt=new Date(Date.UTC(2026,10,1+d,18,0));$("at").value=dt.toISOString().slice(0,16);
 const next=new Date(Date.UTC(2026,10,2+d));$("date").value=next.toISOString().slice(0,10);
 const lines=[...s.feedback.map(f=>`<div><b>${esc(nm(f.person))}</b> · ${esc(f.type)} (day ${f.day}): ${esc(f.words)}</div>`),
  ...s.forgets.map(f=>`<div><b>${esc(nm(f.person))}</b> · forget: ${esc(f.words)}</div>`)];
 $("mem").innerHTML=lines.length?`<h3 style="margin-top:4px">What it has been told</h3>${lines.join("")}`:""}
async function ingest(){$("out").innerHTML=`<div class="card empty">Loading days 1–${$("dayPick").value}…</div>`;
 const r=await api("/api/ingest",{upto:+$("dayPick").value});showState(r.state);
 $("out").innerHTML=`<div class="card"><h2>Memory loaded</h2><div class="meta">${r.log.map(l=>`day ${l.day}: ${l.records} records, ${l.feedback} feedback lines`).join(" · ")}</div></div>`}
function exA(i){$("person").value=EX_ASK[i][0];$("q").value=EX_ASK[i][1];tab("ask")}
function exB(i){$("person").value=EX_BRIEF[i][0];$("date").value=EX_BRIEF[i][1];tab("brief")}
async function doAsk(){$("rec").style.display="none";const person=$("person").value,q=$("q").value.trim();if(!q)return;const at=$("at").value+":00Z";
 $("out").innerHTML=`<div class="card empty">Thinking…</div>`;const r=await api("/api/ask",{person,at,ask:q});
 let badge,body="";
 if(r.refused){badge=`<span class="badge b-ref">refused</span>`;body=`<p class="answer">${esc(r.refused.reason)}</p>`}
 else if(r.clarify){badge=`<span class="badge b-clar">asks back</span>`;body=`<p class="answer">${esc(r.clarify.question)}</p><ul>${(r.clarify.options||[]).map(o=>`<li><span>${esc(o.text)}</span><span class="chip">${esc(o.id)}</span></li>`).join("")}</ul>`}
 else{badge=`<span class="badge b-brief">answer</span>`;body=`<li style="border:0;padding:0"><p class="answer">${esc(r.answer?.text)}</p>${chips(r.answer?.sources)}</li>`}
 const m=(r.matched||[]).length?`<h3>Understood</h3><ul>${r.matched.map(x=>`<li><span>“${esc(x.phrase)}” → <b>${esc(nm(x.id))}</b></span>${chips(x.sources)}</li>`).join("")}</ul>`:"";
 $("out").innerHTML=`<div class="card"><div class="meta">${esc(nm(person))} asked at ${esc(at)}</div><h2>“${esc(q)}”${badge}</h2>${body}${m}${usage(r.usage)}</div>`}
async function doBrief(){$("rec").style.display="none";const person=$("person").value,date=$("date").value;$("out").innerHTML=`<div class="card empty">Preparing…</div>`;
 const r=await api("/api/brief",{person,date});
 if(!Array.isArray(r)||!r.length){$("out").innerHTML=`<div class="card empty">${esc(nm(person))} has no meetings on ${esc(date)}.</div>`;return}
 $("out").innerHTML=r.map(b=>{const nn=b.status!=="brief";
  return `<div class="card"><div class="meta">Brief for ${esc(nm(person))} · ${esc(date)}</div><h2>${esc(b.meeting_id)}<span class="badge ${nn?"b-none":"b-brief"}">${nn?"nothing new":"brief"}</span></h2>
  ${nn?`<p class="small">Nothing changed since this person's last meeting with this client. No filler.</p>`:""}
  ${b.changed?.length?`<h3>What changed</h3><ul>${b.changed.map(c=>`<li><span>${esc(c.text)}</span>${chips(c.sources)}</li>`).join("")}</ul>`:""}
  ${b.promises?.length?`<h3>Open promises</h3><ul>${b.promises.map(p=>`<li><span><b>${esc(nm(p.owner))}</b> · ${esc(p.what)} · due ${esc(p.due)}</span>${chips(p.sources)}</li>`).join("")}</ul>`:""}
  ${b.open_these?.length?`<h3>Open these</h3><ul>${b.open_these.map(o=>`<li><span>${esc(o.why)}</span>${chips([o.record])}</li>`).join("")}</ul>`:""}
  ${b.question_to_ask?`<h3>Question to ask</h3><ul><li><span>${esc(b.question_to_ask.text)}</span>${chips(b.question_to_ask.sources)}</li></ul>`:""}
  ${usage(b.usage)}</div>`}).join("")}
async function doForget(){const person=$("person").value,what=$("what").value;const r=await api("/api/forget",{person,what});showState(r.state);
 $("out").innerHTML=`<div class="card"><h2>Forgotten<span class="badge b-ref">forget</span></h2><p>${esc(nm(person))}: “${esc(what)}”</p><p class="small">Now ask the same question again — it should be gone.</p></div>`}
async function openRec(id){const person=$("person").value;const r=await fetch(`/api/record?id=${encodeURIComponent(id)}&person=${person}`);const j=await r.json();
 $("recBody").innerHTML=r.ok?`<b>${esc(j.id)}</b> · ${esc(j.type)} · ${esc(j.visibility)}<div class="meta">${esc(j.author_name)} · ${esc(j.at)}${j.client?" · "+esc(j.client):""}</div><pre>${esc(j.subject?j.subject+"\n\n":"")}${esc(j.title?j.title+"\n\n":"")}${esc(j.body)}</pre>`
 :`<b>${esc(id)}</b><p style="color:var(--bad)">${esc(j.error)}</p>`;$("rec").style.display="block"}
load();
</script></body></html>"""


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Demo UI: http://127.0.0.1:{PORT}   (pack: {store.pack_root()}, memory: {os.environ['R3_STORE']})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

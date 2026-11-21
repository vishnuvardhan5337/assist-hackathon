"""Reusable UI pieces for the Streamlit demo. One look, defined once.

Design rules (kept here so every screen follows them):
  Inter · 1100px content width · soft gray borders · 8px radius · one primary color
  no emojis · no gradients · no glass effects · whitespace over decoration
"""
import html

import streamlit as st
import streamlit_shadcn_ui as ui

PRIMARY = "#4F46E5"      # the only accent color
INK = "#111827"
MUTED = "#6B7280"
BORDER = "#E5E7EB"
SURFACE = "#FAFAFA"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

/* shadcn components live in a shadow DOM; their theme is CSS variables set on the host element */
.stApp * {{
  --primary: {PRIMARY}; --ring: {PRIMARY}; --primary-foreground: #FFFFFF;
  --border: {BORDER}; --input: {BORDER}; --radius: 8px;
  --font-sans: 'Inter', sans-serif;
}}
html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp button, .stApp textarea {{
  font-family: 'Inter', sans-serif !important; color: {INK};
}}
.stApp {{ background: #FFFFFF; }}
.block-container {{ max-width: 1100px; padding: 56px 24px 96px; }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stAppDeployButton"], [data-testid="stMainMenu"], [data-testid="stToolbar"] {{ display: none; }}
[data-testid="stVerticalBlock"] {{ gap: 16px; }}

/* native widgets match the shadcn ones */
.stSelectbox div[data-baseweb="select"] > div, .stDateInput input, .stTextInput input {{
  border-radius: 8px !important; border-color: {BORDER} !important; font-size: 14px;
}}
.stSelectbox label, .stDateInput label, .stTextInput label {{ font-size: 13px; font-weight: 500; color: {MUTED}; }}

/* type scale (scoped so Streamlit's markdown styles do not win) */
.stMarkdown .k-title {{ font-size: 26px; font-weight: 600; letter-spacing: -0.02em; line-height: 1.2; }}
.stMarkdown .k-sub {{ font-size: 14px; color: #6B7280; margin-top: 6px; }}
.stMarkdown .k-label {{ font-size: 12px; font-weight: 500; color: #6B7280; text-transform: uppercase; letter-spacing: 0.06em; margin: 8px 0 4px; }}
.stMarkdown .k-meta {{ font-size: 12px; color: #6B7280; }}

/* cards */
.stMarkdown .k-card {{ border: 1px solid #E5E7EB; border-radius: 8px; background: #FFFFFF; padding: 20px 24px; }}
.stMarkdown .k-card-title {{ font-size: 15px; font-weight: 600; letter-spacing: -0.01em; margin-bottom: 8px; }}
.stMarkdown .k-row {{ display: flex; justify-content: space-between; align-items: baseline; gap: 16px; padding: 12px 0; border-top: 1px solid #E5E7EB; font-size: 14px; line-height: 1.5; }}
.stMarkdown .k-row:first-of-type {{ border-top: 0; }}
.stMarkdown .k-src {{ font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; color: #6B7280; white-space: nowrap; }}
.stMarkdown .k-empty {{ font-size: 14px; color: #6B7280; padding: 4px 0; }}
.stMarkdown .k-answer {{ font-size: 16px; line-height: 1.55; margin: 8px 0; }}
.stMarkdown .k-q {{ font-size: 13px; color: #6B7280; }}
.stMarkdown .k-gap {{ height: 8px; }}
</style>
"""


def inject_theme():
    st.markdown(CSS, unsafe_allow_html=True)


def e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def page_header(title: str, subtitle: str):
    st.markdown(f'<div class="k-title">{e(title)}</div><div class="k-sub">{e(subtitle)}</div>', unsafe_allow_html=True)


def section_label(text: str):
    st.markdown(f'<div class="k-label">{e(text)}</div>', unsafe_allow_html=True)


def sources(ids) -> str:
    return " ".join(e(i) for i in (ids or []))


def card(title: str, body_html: str, meta: str = ""):
    """A clean bordered card: title, rows of content, optional footer meta line."""
    foot = f'<div class="k-gap"></div><div class="k-meta">{e(meta)}</div>' if meta else ""
    st.markdown(f'<div class="k-card"><div class="k-card-title">{e(title)}</div>{body_html}{foot}</div>', unsafe_allow_html=True)


def rows(items) -> str:
    """items: list of (left_text, right_text)."""
    return "".join(f'<div class="k-row"><span>{e(a)}</span><span class="k-src">{e(b)}</span></div>' for a, b in items)


def change_list(changes) -> str:
    return rows([(c["text"], sources(c["sources"])) for c in changes])


def empty(text: str) -> str:
    return f'<div class="k-empty">{e(text)}</div>'


def promise_table(promises, today_iso, name_of, fmt_day, key):
    data = [{"who": name_of(p["owner"]), "what": p["what"],
             "due": fmt_day(p["due"]) + ("  ·  overdue" if p["due"] < today_iso else "")} for p in promises]
    ui.table(data, columns=[ui.TableColumn("who", "Owner"), ui.TableColumn("what", "Promise"),
                            ui.TableColumn("due", "Due", align="right")], key=key)


def answer_view(asker_name: str, question: str, result: dict):
    """One answer, a clarifying question or a refusal, in the same card shape."""
    st.markdown(f'<div class="k-q">{e(asker_name)} asked “{e(question)}”</div>', unsafe_allow_html=True)
    if result.get("refused"):
        ui.badges([ui.BadgeItem("Not shared", "outline")], key="ans_badge")
        st.markdown(f'<div class="k-answer">{e(result["refused"]["reason"])}</p>', unsafe_allow_html=True)
    elif result.get("clarify"):
        ui.badges([ui.BadgeItem("Needs one detail", "secondary")], key="ans_badge")
        st.markdown(f'<div class="k-answer">{e(result["clarify"]["question"])}</p>', unsafe_allow_html=True)
        st.markdown(rows([(o["text"], o["id"]) for o in result["clarify"].get("options", [])]), unsafe_allow_html=True)
    elif result.get("answer"):
        a = result["answer"]
        st.markdown(f'<div class="k-answer">{e(a["text"])}</div><div class="k-src">{sources(a.get("sources"))}</div>',
                    unsafe_allow_html=True)
    u = result.get("usage") or {}
    st.markdown(f'<div class="k-meta">{u.get("input_tokens", 0)} tokens · {u.get("ms", 0)} ms</div>', unsafe_allow_html=True)

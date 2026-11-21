# Work assistant that remembers (BuildSprint Round 3)

Meeting briefs and short answers for Kitepoint Studio. Every line comes from the data with its record id, it improves from feedback, and it never shows one person's private things to another.
Design note: [`docs/design_note.pdf`](docs/design_note.pdf) (one page, LaTeX source in `docs/design_note.tex`; the same content as text is in [`DESIGN.md`](DESIGN.md)).

## Run it (one command)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                                   # installs openai + python-dotenv only
cp .env.example .env                               # add your Groq key (optional, see below)
export R3_PACK=/path/to/BuildSprint-R3-candidate   # the unzipped data pack
python3 "$R3_PACK/tools/run_days.py" --cmd ".venv/bin/python main.py" --days 1-5 --out runs/mine
python3 "$R3_PACK/tools/score.py" --run runs/mine --days 1-5
```

Or simply `./run_eval.sh` (runs the harness, the scorer, the output validator, our stricter leak check and the tests).

**Overfitting stress test.** `tests/paraphrase_pack.py --variant a|b` rewords days 1 to 5 (same records, ids and official keys). B is a held-out set written before any fix. Blind first score: **64.0/85**. After general fixes: original 85.0, A 85.0, B 80.8 without the model, and **84.05 with the model** (real Groq run). The model fallback recovered ownership changes and promise wordings that no rule caught. Privacy and honesty stayed at 1.00 throughout. Details are in `DESIGN.md`.

**Score guards.** `tests/test_scoring.py` runs the organisers' own harness and scorer and fails if any area drops below its floor. It runs on days 1 to 5 with the official keys, and on days 1 to 7 using `tests/hidden_pack.py`: two simulated hidden days with hand-written answer keys in the official format. Their scorer then measures *Getting better* too, which needs 6+ days. Current result: **87.5/90** on days 1 to 7, with every day at 1.00. Getting better is 2.5/5 because the scores are flat at 1.00 from day 1, so there is nothing to climb. To build that pack yourself: `python3 tests/hidden_pack.py --out /tmp/hidden_pack`.

**Validation.** `eval/validate_outputs.py --run <dir>` checks every output against the contract in DATA_FORMAT: exact keys, status rules, exactly one of answer/clarify/refused, `{id, text}` options, YYYY-MM-DD dates, person-id owners and token limits. It also checks that every cited id exists, had been ingested by that day, and is visible to the person. `pytest tests` (107 tests) runs the validator on a full replay, on 24 messy inputs (bad dates, unknown people, injection text) and on two simulated hidden days (restricted records, another company's records, an injection, an intern contradiction, leave that expires, forget, new promise wording). It also feeds the validator broken outputs to prove it catches each one.

The harness calls `python3 main.py ingest|brief|ask|forget ...`. Output is JSON on stdout only, and the exit code is always 0. Memory lives in `./.assist_store/` (override with `R3_STORE`). `ingest --day 1` starts a fresh store.

## Demo UI (for the video, not graded)
`python3 demo/app.py` and open http://127.0.0.1:8000. It's one file with no extra packages, and every button runs the same `main.py`. It uses its own memory in `.demo_store/`, and clicking a record id opens it only if the selected person may see it.

**Meeting board (Streamlit + shadcn):** `pip install streamlit streamlit-shadcn-ui && streamlit run demo/streamlit_app.py`. Summary cards, a tab per meeting with its brief, and a quick-question panel. Its look (Inter, one primary colour, 8px radius, soft borders, 1100px width) is defined once in `demo/ui_kit.py` and `.streamlit/config.toml`. It runs the same `main.py`.

## Model and tokens
- **Model:** `openai/gpt-oss-120b` on **Groq** (OpenAI-compatible API, free tier), set in `.env`. Network access is needed only to `api.groq.com`.
- **When it is used:** only for client nicknames that code cannot resolve ("the London account"), open questions answered from a few retrieved records, and preferences or corrections in unusual wording. On days 1 to 5 almost every output uses **0 tokens**.
- **Token counts:** taken from the API's `usage` field. Before sending, a prompt is checked against our own budget, estimated at characters / 4 (at most 3,500 for a question, well under the 4,000 and 8,000 limits). Cached replays report 0, because no tokens are spent. `cost_usd` is 0 because Groq's free tier costs nothing.
- **No key, or rate-limited?** Every model call has a code-only fallback, so the program still answers. `LLM_ENABLED=0` turns the model off completely.

## Scores (our own run, days 1 to 5)
Official scorer: **85.0 / 85** (+ design note /10 by judges), with privacy 1.00 and cost 1.00. The same score holds with the model off, and with a deliberately wrong fake model (tests the fallbacks). Our stricter text-level leak check (`eval/leak_check.py`) also finds no leaks. See `eval/worst.md` for the weakest outputs and why.

## Layout
| file | what it does |
|---|---|
| `docs/design_note.pdf` | the one-page design note (LaTeX source alongside) |
| `main.py` | entry point for the harness |
| `assist/cli.py` | the four commands; `ingest` stores day N and feedback from day N-1 |
| `assist/store.py` | SQLite memory, pack and store paths |
| `assist/world.py` | loads data; **`visible()` is the privacy gate**; dates, people, client nicknames |
| `assist/facts.py` | tasks, client facts and their resolver, decisions, deliveries, promises, leave |
| `assist/memory.py` | feedback becomes preferences, ignored topics and corrections (per person) |
| `assist/brief.py` | brief builder |
| `assist/ask.py` | question answering: clarify, refuse, answer |
| `assist/llm.py` | the only model call: budget, cache, retries, untrusted-data wrapping |
| `eval/validate_outputs.py` | output contract + citation integrity validator (used by tests and `run_eval.sh`) |
| `eval/leak_check.py` | text-level privacy check, stricter than the official scorer |
| `demo/app.py` | local demo UI for the video (standard library only) |
| `demo/streamlit_app.py` | Meeting board demo (optional: `pip install streamlit streamlit-shadcn-ui`) |
| `demo/ui_kit.py` | reusable UI components and the theme for the demo |
| `tests/` | 107 pytest tests: privacy, facts, asks, validation, score guards, overfitting, extraction checks. Model off |
| `tests/paraphrase_pack.py` | reworded copies of days 1 to 5 (A, and held-out B) to measure overfitting |
| `assist/extract.py` | model fallback for facts the rules miss, every result checked by code |
| `tests/hidden_pack.py` | builds 2 simulated hidden days with official-format answer keys |

## AI assistants used
Claude (Anthropic) helped plan the architecture, write the code and tests, and review the outputs against the official scorer. I reviewed every file, and I can explain and change all of it.

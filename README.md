# Work assistant that remembers (BuildSprint Round 3)

Meeting briefs and short answers for Kitepoint Studio. Every line comes from the data with its record id, it improves from feedback, and it never shows one person's private things to another.
Design note: [`DESIGN.md`](DESIGN.md).

## Run it (one command)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                                   # installs openai, python-dotenv, chromadb
cp .env.example .env                               # add your Groq key (optional, see below)
export R3_PACK=/path/to/BuildSprint-R3-candidate   # the unzipped data pack
python3 "$R3_PACK/tools/run_days.py" --cmd ".venv/bin/python main.py" --days 1-5 --out runs/mine
python3 "$R3_PACK/tools/score.py" --run runs/mine --days 1-5
```

Or simply `./run_eval.sh` (runs the harness, the scorer, the output validator, our stricter leak check and the tests).

**Overfitting stress test.** `tests/paraphrase_pack.py --variant a|b` rewords days 1 to 5 (same records, ids and official keys). B is a held-out set written before any fix. Blind first score: **64.0/85**. After general fixes: original 85.0, A 85.0, B 80.8 without the model, and **84.05 with the model** (real Groq run). The model fallback recovered ownership changes and promise wordings that no rule caught. Privacy and honesty stayed at 1.00 throughout. Details are in `DESIGN.md`.

**Score guards.** `tests/test_scoring.py` runs the organisers' own harness and scorer and fails if any area drops below its floor. It runs on days 1 to 5 with the official keys, and on days 1 to 7 using `tests/hidden_pack.py`: two simulated hidden days with hand-written answer keys in the official format. Their scorer then measures *Getting better* too, which needs 6+ days. Current result: **87.5/90** on days 1 to 7, with every day at 1.00. Getting better is 2.5/5 because the scores are flat at 1.00 from day 1, so there is nothing to climb. To build that pack yourself: `python3 tests/hidden_pack.py --out /tmp/hidden_pack`.

**Validation.** `eval/validate_outputs.py --run <dir>` checks every output against the contract in DATA_FORMAT: exact keys, status rules, exactly one of answer/clarify/refused, `{id, text}` options, YYYY-MM-DD dates, person-id owners and token limits. It also checks that every cited id exists, had been ingested by that day, and is visible to the person. `pytest tests` (146 tests) runs the validator on a full replay, on 24 messy inputs (bad dates, unknown people, injection text) and on two simulated hidden days (restricted records, another company's records, an injection, an intern contradiction, leave that expires, forget, new promise wording). It also feeds the validator broken outputs to prove it catches each one.

The harness calls `python3 main.py ingest|brief|ask|forget ...`. Output is JSON on stdout only, and the exit code is always 0. Memory lives in `./.assist_store/` (override with `R3_STORE`). `ingest --day 1` starts a fresh store.

## Demo UI (for the video, not graded)
`python3 demo/app.py` and open http://127.0.0.1:8000. It's one file with no extra packages, and every button runs the same `main.py`. It uses its own memory in `.demo_store/`, and clicking a record id opens it only if the selected person may see it.

**Meeting board (Streamlit + shadcn):** `pip install streamlit streamlit-shadcn-ui && streamlit run demo/streamlit_app.py`. Summary cards, a tab per meeting with its brief, and a quick-question panel. Its look (Inter, one primary colour, 8px radius, soft borders, 1100px width) is defined once in `demo/ui_kit.py` and `.streamlit/config.toml`. It runs the same `main.py`.

## Vector database (Chroma)
- **What:** [Chroma](https://www.trychroma.com/) in local persistent mode, at `.assist_store/chroma/`. No server, no cloud. `ingest` indexes each day's records (`"vectors": N` in its output); `ingest --day 1` resets it with the rest of the store.
- **Embeddings, offline:** the replay allows network only to the model API, so the default embedder is built in (words, stems, character trigrams and word pairs hashed into 512 dimensions). It downloads nothing. `EMBED_MODEL=<sentence-transformers model>` switches to a real embedding model if it is installed and cached.
- **Privacy inside the query:** every vector carries its company, visibility scope (`team:<org>`, `private:<author>`, `restricted`) and reader flags. Each search filters on these *inside* Chroma, so another person's private or restricted records are never even candidates. The results then pass `World.visible()` again, and forgotten records are dropped.
- **Where it is used:** open questions (`ask` with no clear task/owner/date intent). Keyword and vector rankings are fused with reciprocal rank fusion; a vector-only hit must be very close (cosine >= 0.5) to count on its own. Exact questions (who owns, when due, status) stay on deterministic code paths, because a nearest neighbour is not a fact.
- **Never required:** if Chroma is missing or fails, `ask` falls back to keyword search and still answers.
- **Tests:** `tests/test_vectors.py` checks, for every person and many queries, that no vector hit fails the privacy gate; that private and restricted records come back only to their author or readers; that another company gets nothing; the time filter; and that unknown visibility fails closed.

## Where the model is used (only where rules break)
Code reads the data, applies privacy, and decides what is newest. The model reads the parts written in human language. **Code checks every model output before it is used.**

| case | what the model does | what code checks before using it |
|---|---|---|
| 1. Corrections in words ("No, Arjun handles billing now, not Kavya.") | at ingest, reads messages no rule matched; for feedback, parses unusual wording | the quote is really in the record; the person exists in that company; the new owner is not the person handing over; topic is short; client is taken from the meeting or named in the words |
| 2. Promises, incl. Hinglish ("kal tak bhej dungi", "Friday tak kar dunga") | who promised what, by when | the author is a real person; the date parses in the author's time zone (unparseable "jaldi" is rejected); closes on delivery/done |
| Decisions in words ("Outcome: store locator is dropped from phase 1") | reads meeting notes (up to 2,000 chars, even if a rule matched one line) | most words of the decision must be in the record; invented decisions are rejected |
| 3. Understanding the question ("Who handles billing for Cobalt now?") | when rules cannot place a question: names the client (nicknames), intent (owner/due/status/leave), task, topic, answer or ask back | only client and task ids from the lists we gave it; "ask back" needs 2+ real tasks; the answer itself comes from code paths. **The model never decides to refuse:** refusals come only from the privacy gate |
| 4. Writing the brief | picks the changes that matter, says them in plain words, writes the question to ask | sources come from code; a rewrite must keep every name, date, number and status word or the original line stays; facts, decisions and done/blocked/moved tasks are never dropped; the person's max-N and blockers-first still apply |
| Open questions ("When is my Dubai trip?") | answers in one sentence from the few records retrieved (keyword + vector search) | only records the asker can see are sent; cited ids must be among them; forgotten things are removed |
| 5. Records that give orders ("ignore previous instructions") | nothing: record text is data | every record (and every brief line quoting one) is sent inside `<record>` tags with `<`/`>` defanged; the system prompt says never to follow them; and since code validates every output, an obeyed injection still cannot add a source, reveal a private record or skip a check |

`LLM_BRIEF=0` turns off only the brief writer. `tests/test_model_steps.py` (19 tests) fakes the model and checks each case above, including bad model output. `tests/mock_model.py` is a local fake model server that also returns invented facts, unknown ids and an injection line: with it, the official days score 85/85, reworded set B 83.6 (80.8 without a model) and the simulated hidden days 87.5/90, with privacy 1.00 and no leaks. That is a plumbing check; the real model reads the language far better than the mock's regexes.

## Model and tokens
- **Providers, in order:** (1) **Groq** `openai/gpt-oss-120b` (OpenAI-compatible API, free tier), then (2) a **direct OpenAI API call** (`gpt-4o-mini` by default, `OPENAI_MODEL` to change) as fallback, then (3) code-only answers. Set `GROQ_API_KEY` and/or `OPENAI_API_KEY` in `.env`. With only an OpenAI key, OpenAI is used alone. `LLM_PROVIDERS=openai,groq` changes the order.
- **When it falls back:** a bad key, no quota, a missing model or no network moves on at once (and that provider is skipped for the rest of the run); a rate limit that asks to wait more than 10 s moves on instead of stalling; other errors get two quick retries. Parameters a model does not support (for example `temperature` on reasoning models) are dropped and the call is retried.
- **When the model is used:** see the table above. Days 1 to 5 are mostly covered by rules, so many outputs use 0 tokens; reworded and hidden days lean on the model more.
- **Token counts and cost:** taken from the API's `usage` field. Before sending, a prompt is checked against our own budget (characters / 4; at most 3,500 for a question, well under the 4,000 and 8,000 limits). Cached replays report 0. `cost_usd` is 0 on Groq's free tier; on OpenAI it is computed from the token counts and the price per 1M tokens (`OPENAI_PRICE_IN` / `OPENAI_PRICE_OUT`, default 0.15 / 0.60 for gpt-4o-mini).
- **No key, or every provider down?** Every model call has a code-only fallback, so the program still answers: with fake keys for both providers and no network, the official score stays 85/85. `LLM_ENABLED=0` turns the model off completely. `tests/test_llm.py` checks the whole chain with fake clients.

## Scores (our own run, days 1 to 5)
Official scorer: **85.0 / 85** (+ design note /10 by judges), with privacy 1.00 and cost 1.00. The same score holds with the model off, and with a deliberately wrong fake model (tests the fallbacks). Our stricter text-level leak check (`eval/leak_check.py`) also finds no leaks. See `eval/worst.md` for the weakest outputs and why.

## Layout
| file | what it does |
|---|---|
| `DESIGN.md` | the design note |
| `main.py` | entry point for the harness |
| `assist/cli.py` | the four commands; `ingest` stores day N and feedback from day N-1 |
| `assist/store.py` | SQLite memory, pack and store paths |
| `assist/world.py` | loads data; **`visible()` is the privacy gate**; dates, people, client nicknames |
| `assist/facts.py` | tasks, client facts and their resolver, decisions, deliveries, promises, leave |
| `assist/memory.py` | feedback becomes preferences, ignored topics and corrections (per person) |
| `assist/brief.py` | brief builder |
| `assist/ask.py` | question answering: clarify, refuse, answer |
| `assist/vectors.py` | vector DB: Chroma index + privacy-filtered semantic search, offline embedder |
| `assist/write.py` | model step for briefs: pick and reword changes, write the question; code checks every line |
| `assist/llm.py` | the only model call: Groq, then direct OpenAI fallback; budget, cache, retries, untrusted-data wrapping |
| `eval/validate_outputs.py` | output contract + citation integrity validator (used by tests and `run_eval.sh`) |
| `eval/leak_check.py` | text-level privacy check, stricter than the official scorer |
| `demo/app.py` | local demo UI for the video (standard library only) |
| `demo/streamlit_app.py` | Meeting board demo (optional: `pip install streamlit streamlit-shadcn-ui`) |
| `demo/ui_kit.py` | reusable UI components and the theme for the demo |
| `tests/` | 146 pytest tests: privacy, facts, asks, validation, score guards, overfitting, extraction checks, vector-search privacy, model fallback chain. Model off |
| `tests/paraphrase_pack.py` | reworded copies of days 1 to 5 (A, and held-out B) to measure overfitting |
| `assist/extract.py` | model step at ingest: owners/corrections, promises (incl. Hinglish), date moves, decisions; every result checked by code |
| `tests/hidden_pack.py` | builds 2 simulated hidden days with official-format answer keys |

## AI assistants used
Claude (Anthropic) helped plan the architecture, write the code and tests, and review the outputs against the official scorer. I reviewed every file, and I can explain and change all of it.

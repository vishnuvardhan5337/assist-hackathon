# Our 3 weakest outputs (days 1 to 5)

The official scorer gives every item 1.00 on days 1 to 5 (`runs/llm`, real Groq key, 85.0/85), so no output *failed* there.
These are the three outputs we trust least, why, and what we did or would do about each.

## 1. `q01-04` / `q04-06`: "the London account" depends on a model guess
**Output:** correct (Cobaltline), but only because the model inferred "London" from the client's `.co.uk` email domain and Liam's Europe/London time zone. No record in the data says "London".
**Risk:** on a hidden day a nickname with weaker clues could be resolved to the wrong client.
**What we did:** the model's choice is only a *hint*. If the named task or fact does not exist for that client, the code searches every client and keeps the unique match. With a deliberately wrong fake model, the score still stayed at 85/85.
**Earlier failure this caught:** before the fix, a wrong guess made the whole answer wrong (score fell to 76.6 in the fake-model test).

## 2. `q03-01`: "When is my Dubai trip?" is worded by the model
**Output:** "Your Dubai trip is planned for 20–24 November." This is correct and cites the asker's own private note.
**Risk:** this is the only answer whose *wording* comes from the model, so it is the least predictable. Without the model, the fallback quotes the raw note, which is correct but less natural.
**Guardrails:** the model only sees the 1 to 6 records the asker may see. It must return ids from that set, or we print "I have nothing on that". After a `forget`, the record is removed at the privacy gate and any answer mentioning the topic is replaced. This is tested in `tests/test_privacy.py`.

## 3. Brief `ev-nb-me-04` (p02, 5 Nov): the question to ask was weak
**Earlier output:** "Meera promised the checkout copy by 3 Nov. Is it still coming?" This brief was *for Meera herself*, so she would be asking about her own promise.
**Why the scorer missed it:** `question_to_ask` is not scored, only checked for honesty.
**Fix:** questions now skip the person's own promises. It now reads: "Launch moved from 10 Nov to 14 Nov: does anything else in the plan need to move with it?" The question is still a template and could be sharper. With more time we would let the model pick the question from the brief's own lines, at a cost of about 300 tokens.

## Bugs found by our own checks (not visible in the official score)
- **Cross-company leak in corrections:** Meera's billing correction was applied when a Brightwave person (q02) asked about Northbeam. The official scorer only checks cited ids, and this answer cited none. Corrections now apply only inside the corrector's own company. Tested.
- **Same-day meetings:** p03 has two Cobaltline calls on 4 Nov. The earlier one was treated as "the last meeting", so the brief wrongly said `nothing_new`. Only meetings on earlier dates count now.
- **Duplicate change lines:** a note's "Decision: store locator dropped" and the task's "cancelled" were two lines. Under a "max 5 changes" preference, that wastes a slot. They are now merged into one line that cites both records.

## The biggest weakness we found: wording we had not seen
A held-out reworded copy of days 1 to 5 (`tests/paraphrase_pack.py --variant b`, written before any fix) scored **64.0/85** the first time, against 85/85 on the original wording:
- **Questions (0.35):** "Where are we with X?", "Who's on the X?" and "Who does billing for NB?" matched no question type and fell through to a weak search.
- **Memory (0.50):** "billing moved to Arjun" and "five changes" were not understood without the model.
- **Briefs (0.81):** new ownership wording ("handing over", "going forward") and new promise wording.

Privacy and honesty stayed at **1.00**, so the failures were misses, never leaks or invented facts.
General fixes (not tuned to B's sentences) brought B to 80.8/85 without the model. A checked model fallback adds the ownership and promise facts that no rule can catch. B reaches **84.05/85** with the real model. That run exposed one more failure, now fixed and tested: the model named the person *handing over* billing (Kavya) as its new owner.

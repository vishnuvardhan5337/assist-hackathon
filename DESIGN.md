# Design note: the work assistant that remembers

**Principle: code decides, the model only reads language.** Who may see what, which value is newest, what has expired and what was forgotten are all decided by plain, testable Python. A small model (Groq, free tier) is called only where code cannot read the language: nicknames such as "the London account", open questions such as "When is my Dubai trip?", and feedback in unusual wording. Every printed line cites record ids, and code checks those ids before printing. On days 1 to 5 almost no output needs the model, so most cost 0 tokens.

## 1. How do we pick the records for a brief, and what goes in it?
1. **Privacy gate first.** `World.visible(person)` keeps only the person's own company, team records, their own private notes and restricted records that list them. It also drops anything they asked us to forget. Nothing outside this set is read again.
2. **Meetings.** These are the person's calendar events whose start falls on the date in *their* time zone. For each one, the window is everything since their previous meeting with the same client (by date; a later meeting on the same day has not happened yet). A first meeting gets everything so far.
3. **Changed**, most important first:
   - client facts that changed, such as the launch date or the billing owner: "moved from 10 Nov to 14 Nov", with both the old and the new record cited;
   - `Decision:` lines in meeting notes;
   - blocked tasks;
   - tasks marked done, re-dated, re-owned or cancelled (the net change per task);
   - deliveries.
4. **Promises.** We detect "kal tak bhej dunga", "next Monday tak", "by EOD", "by Thursday" and similar phrasings, with the due date worked out in the author's time zone. A promise stays open until a "Sharing the X as promised" message or the matching task being done.
   A model fallback runs once per day at ingest. It reads only the messages that look like an ownership change, a date move or a promise but match no rule. Code keeps a model fact only if its quote is really in that record, the person exists and the date parses. A made-up quote is rejected (tested).
5. **The person's memory**: max N changes, blockers first, skipped topics, topics they marked "ignored", forgotten things. Nothing left after that means `nothing_new`, with empty lists and no question. We never write filler.

## 2. How do we save memory, and who may see it?
SQLite file `.assist_store/assist.db`: raw records, feedback (read one day late, as the harness requires), forget requests and an LLM cache. Facts are **rebuilt from visible records on every call**, never stored in a shared summary. Two results follow:
- What one person may not see can never shape another person's output.
- "Forget" works everywhere at once: the forgotten record is filtered out at the gate, and lines that mention it are dropped.

Preferences and ignored topics belong only to the person who gave them. Corrections fix a team fact, but only for people in the corrector's own company.

## 3. How do we know which information is newest?
Each client fact keeps a timeline of claims, and the resolver applies four rules:
- The newest claim wins.
- An intern's claim cannot overrule anyone else's. A question ("Kavya owns billing for NB right?") is never a claim.
- A new version of a document counts only for the fields it actually changes, so a stale copy that still says "Billing: Kavya" changes nothing.
- A correction from the person we brief beats older claims, but not genuinely newer news.

Task state comes from real status, owner and due changes only, never from comments. Leave facts carry an end date and stop counting after it, judged by the asker's own date and time zone.

## How we checked for overfitting
The rules were written after reading days 1 to 5, so we measured how much they depend on that exact wording. `tests/paraphrase_pack.py` keeps the same records, ids and official keys but rewords the messages, feedback and questions. Set A was used while fixing. Set B was written *before* any fix and scored once, blind: **64.0/85**, against 85/85 on the original. Privacy and honesty stayed at 1.00, so unfamiliar wording made the program miss things, never leak or invent. We then made only general fixes:
- task answers lead with the asked-for fact and add owner, due date and status;
- promises are matched by grammar (promise verb, thing, time phrase), not by exact sentences;
- a client-fact fallback;
- number words in preferences;
- looser correction shapes;
- the checked model fallback above.

Results: original 85.0, A 85.0, and B 80.8 without the model or **84.05 with it** (real Groq run; B is no longer blind). That real run caught one model mistake: the person *handing over* billing was read as the new owner. Code now rejects an owner named as the giver (tested).

## 4. What will we not do?
- We never obey text inside a record. Record text reaches the model only inside `<record>` tags marked as untrusted data, and no output action depends on it.
- We never cite or hint at another company's record, someone else's private note, or a restricted record not addressed to the asker. When the answer exists only in such records, we refuse.
- We never print a line without a supporting source. If unsure, we leave it out.
- We never send a whole day to the model. Prompts stay under 1,500 to 3,500 tokens, well below the 8k and 4k limits.
- We never hardcode names, nicknames, ids or answers from days 1 to 5. Nicknames come from the data (initials, prefixes, co-occurrence), or from the model as a last resort, and a model guess is only a hint that code checks.
- We never let the model decide privacy, recency or forgetting.

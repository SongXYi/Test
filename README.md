# DealKeeper

A terminal application that keeps track of the deals, vouchers and student
discounts you collect, so you stop losing them. You paste a promo message or
point it at a photo of a voucher; a Groq model reads it and turns it into
structured data; the app then tells you what you actually qualify for, what
dies this week, what you already used, and how much you have saved.

100% procedural Python: functions only, no type definitions anywhere.

## Pipeline

```
io_manager.py   ->   ai_manager.py   ->   logic_manager.py   ->   data_manager.py
collect input        extract fields       apply the rules         remember it
(all print/input)    (Groq API)           (domain brain)          (flat JSON file)
```

`main.py` only moves data between these four modules. Delete `ai_manager.py`
and the application cannot start, let alone accept a deal: the structured
payload the model returns *is* the record everything else works on.

## The four managers

| File | Responsibility | Key functions |
|---|---|---|
| `io_manager.py` | The only module that talks to the user. Validates every value (type, range, required) and re-prompts instead of crashing. | `ask_text`, `ask_int`, `ask_float`, `ask_yes_no`, `ask_choice`, `ask_date`, `ask_image_path`, `prompt_new_deal`, `prompt_profile`, `prompt_missing_fields`, `display_record`, `display_list`, `display_result`, `display_summary` |
| `ai_manager.py` | Core engine. Every record passes through the Groq API. No domain rules here. | `build_prompt`, `call_api`, `parse_response`, `validate_response`, `process` |
| `logic_manager.py` | Domain brain: expiry, eligibility, duplicates, review queue, minimum spend, usage and savings. | `evaluate`, `score`, `route`, `check_expiry`, `check_eligibility`, `check_needs_review`, `find_duplicate`, `check_min_spend`, `mark_used`, `compute_summary`, `refresh_statuses`, `rank` |
| `data_manager.py` | Memory. Flat JSON file, atomic writes, stable ordering, graceful failure. | `save`, `load`, `query`, `update_record`, `delete_record`, `load_profile`, `save_profile` |

`config.py` holds shared constants (categories, thresholds, paths) and sends
all diagnostics to `data/app.log` — never to the terminal.

## Business rules implemented

1. **Expiry is re-checked on every run.** Expiry within `EXPIRING_SOON_DAYS`
   (7) is tagged `expiring_soon`; a date in the past becomes `expired` and is
   hidden from the active list.
2. **Category whitelist.** The model must answer with one of the ten allowed
   categories. Anything else is rejected and the model is asked again with the
   error attached — the app never accepts an invented category.
3. **Eligibility against your saved profile.** A students-only deal is only
   `eligible` when your profile says you are a student; age limits and
   school-specific offers are checked the same way. Deals you do not qualify
   for are still kept and shown, clearly tagged `not eligible`.
4. **Duplicate guard.** A new deal with a fuzzy-matching shop name *and* a
   similar discount is flagged as a possible duplicate of the earlier deal
   instead of being saved twice. You decide whether to keep it.
5. **Needs review.** If a required field is missing (no expiry date, no
   merchant, no discount amount, unclear eligibility) or the model's confidence
   is below `MIN_CONFIDENCE` (0.6), the deal goes to a separate needs-review
   list, never the active one. You can fill in the missing fields by hand, and
   the record remembers which fields you corrected.
6. **Minimum spend.** A deal with a minimum spend can only be marked as used
   when your basket meets it, and menu option 9 shows exactly which deals a
   given basket amount can redeem.
7. **Single-use tracking.** Marking a single-use voucher as used moves it to
   the used list and it is never recommended again. Reusable deals stay active
   and accumulate savings.
8. **Totals are computed by the app.** Savings, used and unused counts come
   from what you logged, not from the model.

### Multi-condition rules

* `act_now` — needs **five** things to agree: you are eligible, the deal
  expires within 7 days, the discount is large (>= 30% or >= RM10 off), model
  confidence is >= 0.7, and the record is complete. Only then is it routed to
  the "USE IT NOW" queue.
* `min_spend_above_habit` — combines the deal's minimum spend with the typical
  spend in your profile and warns (and lowers the score) when the deal would
  push you well past your normal basket.

`score(record)` blends discount strength, urgency, eligibility, model
confidence and minimum-spend practicality into a 0-100 number used to rank
lists; an expired deal always scores 0.

## Prompt design

`ai_manager.SYSTEM_PROMPT` pins down an exact 14-key JSON schema (merchant,
category, discount type/value, prices, description, expiry, eligibility object,
minimum spend, single use, terms, confidence, missing fields), asks for JSON
only, and adds hard rules: never invent values, use `null` plus
`missing_fields` when a value is unreadable, resolve relative dates against
today, prefer day-first for ambiguous dates, strip currency symbols, and lower
`confidence` when guessing. The request also sets
`response_format={"type":"json_object"}`. When validation still fails, the
error list is sent back to the model in the next attempt.

## Setup

Python 3.9+ and no third-party packages (the API call uses the standard
library).

```bash
cp .env.example .env        # then put your key in it: GROQ_API_KEY=gsk_...
python3 main.py
```

`GROQ_API_KEY` can also be exported as an environment variable. Keys are never
committed: `.env` is git-ignored.

## Menu

```
 1  Add a deal (scan an image, paste text, or type it in)
 2  My deals (ready to use)          7  Expired deals
 3  Expiring soon                    8  Mark a deal as used
 4  Needs review                     9  What can I use for a basket amount?
 5  Possible duplicates             10  Search my deals
 6  Used deals                      11  Savings summary
                                    12  My profile
 0  Exit
```

## Data files

* `data/deals.json` — every deal: the raw input, the AI payload under `ai`,
  your usage under `user`, and the decision fields (`status`, `queue`,
  `eligible`, `score`, `flags`, `review_reasons`, `duplicate_of`).
* `data/profile.json` — student status, school, age, typical spend.
* `data/app.log` — API errors, rejected payloads, file problems.

Records are written sorted by id with sorted keys, so the same input produces a
byte-identical file across separate runs.

## Tests

84 assertions covering the business rules with hardcoded AI responses, the
schema validation, the persistence layer, and guard tests for the "no types"
and "terminal access only in io_manager" constraints. No network needed — the
API call is stubbed where it matters.

```bash
python3 test_dealkeeper.py        # built-in runner
python3 -m pytest -q test_dealkeeper.py
```

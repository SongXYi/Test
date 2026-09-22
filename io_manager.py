"""Input/output layer -- the only module allowed to talk to the human.

Every print() and every input() call in DealKeeper lives in this file. The
other three managers are silent: they return data, and this module decides how
it is asked for and how it is shown.

Input rules: nothing leaves this module as a raw string. Values are validated
for type, range and presence, bad input is re-prompted, and the caller receives
a clean typed dict.

Mistyped an answer? Every question accepts "b" (or "back") to step back to the
previous question, and multi-question screens end with a draft you can edit
answer by answer before anything is saved. Backing out of the first question
cancels the screen and returns None to the caller.
"""

from datetime import datetime

import config

WIDTH = 72
DATE_FORMAT = "%Y-%m-%d"

# Sentinel returned by every collector when the user asks to step back.
BACK = "__back__"
BACK_WORDS = ("b", "back")

# The ways a deal can be added. Image scanning is not offered yet.
ADD_METHODS = (
    "Paste the promo text or chat message",
    "Type the details in myself",
)

MENU_OPTIONS = (
    ("1", "Add a deal (paste the promo text, or type it in)"),
    ("2", "My deals (ready to use)"),
    ("3", "Expiring soon"),
    ("4", "Needs review"),
    ("5", "Possible duplicates"),
    ("6", "Used deals"),
    ("7", "Expired deals"),
    ("8", "Mark a deal as used"),
    ("9", "What can I use for a basket amount?"),
    ("10", "Search my deals"),
    ("11", "Savings summary"),
    ("12", "My profile"),
    ("0", "Exit"),
)

STATUS_LABELS = {
    "active": "ACTIVE",
    "expiring_soon": "EXPIRING SOON",
    "expired": "EXPIRED",
    "unknown_expiry": "NO EXPIRY FOUND",
}

QUEUE_LABELS = {
    "active": "Ready to use",
    "act_now": "USE IT NOW",
    "watchlist": "Not eligible for you",
    "needs_review": "Needs your review",
    "duplicate_review": "Possible duplicate",
    "expired": "Expired",
    "used": "Used",
}


# ==========================================================================
# Low-level terminal helpers
# ==========================================================================
def _read(prompt_text):
    """Read one line. A closed stdin or Ctrl-C ends the program politely."""
    try:
        return input(prompt_text)
    except EOFError:
        print("\nInput stream closed. Goodbye!")
        raise SystemExit(0)
    except KeyboardInterrupt:
        print("\nCancelled. Goodbye!")
        raise SystemExit(0)


def _line(char="-"):
    print(char * WIDTH)


def _reject(message):
    print("  !! %s" % message)


def _is_back(answer):
    """True when the user typed the go-back keyword."""
    return str(answer or "").strip().lower() in BACK_WORDS


def _back_hint(allow_back):
    return " (b = back)" if allow_back else ""


# ==========================================================================
# Validated input collectors
# ==========================================================================
def ask_text(label, required=True, default="", max_length=2000, allow_back=True):
    """Ask for a string. Re-prompts while a required answer is blank."""
    suffix = " [%s]" % default if default else ""
    while True:
        answer = _read("%s%s%s: " % (label, suffix, _back_hint(allow_back))).strip()
        if allow_back and _is_back(answer):
            return BACK
        if not answer and default:
            return default
        if not answer:
            if not required:
                return ""
            _reject("This field is required, please type something.")
            continue
        if len(answer) > max_length:
            _reject("Please keep it under %d characters." % max_length)
            continue
        return answer


def ask_int(label, minimum=None, maximum=None, allow_blank=False, default=None,
            allow_back=True):
    """Ask for a whole number inside an optional range."""
    suffix = " [%s]" % default if default is not None else ""
    while True:
        answer = _read("%s%s%s: " % (label, suffix, _back_hint(allow_back))).strip()
        if allow_back and _is_back(answer):
            return BACK
        if not answer:
            if default is not None:
                return default
            if allow_blank:
                return None
            _reject("Please enter a number.")
            continue
        try:
            value = int(answer.replace(",", ""))
        except ValueError:
            _reject("'%s' is not a whole number." % answer)
            continue
        if minimum is not None and value < minimum:
            _reject("Please enter %d or more." % minimum)
            continue
        if maximum is not None and value > maximum:
            _reject("Please enter %d or less." % maximum)
            continue
        return value


def ask_float(label, minimum=None, maximum=None, allow_blank=False, default=None,
              allow_back=True):
    """Ask for a decimal number (currency symbols and commas are tolerated)."""
    suffix = " [%s]" % default if default is not None else ""
    while True:
        answer = _read("%s%s%s: " % (label, suffix, _back_hint(allow_back))).strip()
        if allow_back and _is_back(answer):
            return BACK
        if not answer:
            if default is not None:
                return default
            if allow_blank:
                return None
            _reject("Please enter an amount.")
            continue
        cleaned = answer.replace(config.CURRENCY, "").replace("$", "")
        cleaned = cleaned.replace(",", "").replace("%", "").strip()
        try:
            value = float(cleaned)
        except ValueError:
            _reject("'%s' is not a valid amount." % answer)
            continue
        if minimum is not None and value < minimum:
            _reject("Please enter %.2f or more." % minimum)
            continue
        if maximum is not None and value > maximum:
            _reject("Please enter %.2f or less." % maximum)
            continue
        return round(value, 2)


def ask_yes_no(label, default=None, allow_back=True):
    """Ask a yes/no question and return a real boolean."""
    hint = ""
    if default is True:
        hint = " [Y/n]"
    elif default is False:
        hint = " [y/N]"
    else:
        hint = " [y/n]"
    while True:
        answer = _read("%s%s%s: "
                       % (label, hint, _back_hint(allow_back))).strip().lower()
        if allow_back and _is_back(answer):
            return BACK
        if not answer and default is not None:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        _reject("Please answer y or n.")


def ask_choice(label, options, allow_blank=False, allow_back=True):
    """Show a numbered list and return the chosen option value."""
    print("%s:" % label)
    for index, option in enumerate(options, start=1):
        print("   %2d) %s" % (index, option))
    if allow_back:
        print("    b) go back to the previous question")
    while True:
        answer = _read("Choose 1-%d%s%s: "
                       % (len(options),
                          " (blank to skip)" if allow_blank else "",
                          " or b" if allow_back else "")).strip()
        if allow_back and _is_back(answer):
            return BACK
        if not answer and allow_blank:
            return ""
        try:
            index = int(answer)
        except ValueError:
            _reject("Please type the number next to your choice.")
            continue
        if 1 <= index <= len(options):
            return options[index - 1]
        _reject("Please choose a number between 1 and %d." % len(options))


def ask_date(label, allow_blank=True, default=None, allow_back=True):
    """Ask for a YYYY-MM-DD date and return it as a validated string."""
    while True:
        answer = _read("%s (YYYY-MM-DD%s%s)%s: "
                       % (label,
                          ", blank if unknown" if allow_blank else "",
                          ", now %s" % default if default else "",
                          _back_hint(allow_back))).strip()
        if allow_back and _is_back(answer):
            return BACK
        if not answer:
            if default:
                return default
            if allow_blank:
                return None
            _reject("A date is required here.")
            continue
        try:
            return datetime.strptime(answer, DATE_FORMAT).date().isoformat()
        except ValueError:
            _reject("Use the format YYYY-MM-DD, for example 2026-08-31.")


def ask_multiline(label, allow_back=True):
    """Collect pasted text until the user submits an empty line."""
    print("%s" % label)
    print("(paste as many lines as you like, then press Enter on a blank line)")
    if allow_back:
        print("(type 'back' on the first line to go back)")
    lines = []
    while True:
        line = _read("> ")
        if allow_back and not lines and _is_back(line):
            return BACK
        if not line.strip():
            if lines:
                return "\n".join(lines).strip()
            _reject("Nothing pasted yet -- paste the promo text or type it out.")
            continue
        lines.append(line)


# ==========================================================================
# Multi-question screens
#
# A screen is a list of (key, label, ask_function) steps. The driver below
# walks them forwards, steps backwards when a collector returns BACK, and
# cancels when the user backs out of the very first question. Each step
# receives the answers collected so far, so a step can pre-fill the previous
# answer as its default or skip itself entirely.
# ==========================================================================
def _run_steps(steps, values):
    """Walk the steps, honouring BACK. Returns the answers, or None if cancelled."""
    index = 0
    while index < len(steps):
        key, label, ask = steps[index]
        answer = ask(values)
        if answer == BACK:
            index -= 1
            if index < 0:
                return None
            print("  << back to: %s" % steps[index][1])
            continue
        values[key] = answer
        index += 1
    return values


def _format_answer(value):
    if value is None or value == "":
        return "(not given)"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)


def _display_draft(title, steps, values):
    """Show the answers collected so far, before anything is saved."""
    _line()
    print("%s -- check your answers" % title.upper())
    _line()
    for number, (key, label, _) in enumerate(steps, start=1):
        print("  %2d) %-42s %s" % (number, label, _format_answer(values.get(key))))
    _line()


def _collect_with_review(title, steps, values=None):
    """Run a screen, then let the user edit any single answer before saving.

    Returns one of three things, so the caller can tell the two exits apart:
        the answers dict -- the user saved them
        BACK            -- the user stepped back past the very first question
        None            -- the user chose to cancel the whole screen
    """
    answers = _run_steps(steps, dict(values or {}))
    if answers is None:
        return BACK

    while True:
        _display_draft(title, steps, answers)
        action = ask_choice("What would you like to do?", [
            "Save these answers",
            "Change one answer",
            "Start this screen over",
            "Cancel and go back to the menu",
        ], allow_back=False)

        if action.startswith("Save"):
            return answers
        if action.startswith("Change"):
            labels = [label for _, label, _ in steps]
            chosen = ask_choice("Which answer is wrong?", labels, allow_blank=True,
                                allow_back=False)
            if not chosen:
                continue
            key, _, ask = steps[labels.index(chosen)]
            answer = ask(answers)
            if answer != BACK:
                answers[key] = answer
            continue
        if action.startswith("Start"):
            restarted = _run_steps(steps, {})
            if restarted is None:
                return BACK
            answers = restarted
            continue
        return None


# ==========================================================================
# Screens: welcome, menu, profile
# ==========================================================================
def show_welcome(profile, summary):
    _line("=")
    print("  DEALKEEPER -- never waste a voucher again")
    _line("=")
    name = str(profile.get("name") or "").strip()
    print("Welcome back%s!" % (", %s" % name if name else ""))
    print("Saved deals: %d   Ready to use: %d   Expiring soon: %d   "
          % (summary.get("total_deals", 0), summary.get("active", 0),
             summary.get("expiring_soon", 0)))
    print("Needs review: %d   Used: %d   Total saved so far: %s%.2f"
          % (summary.get("needs_review", 0), summary.get("used_count", 0),
             config.CURRENCY, summary.get("total_saved", 0.0)))
    _line("=")


def show_menu():
    """Print the menu and return the chosen option key."""
    print("")
    _line()
    print("MAIN MENU")
    _line()
    for key, label in MENU_OPTIONS:
        print("  %-3s %s" % (key, label))
    valid = [key for key, _ in MENU_OPTIONS]
    while True:
        answer = _read("Your choice: ").strip()
        if answer in valid:
            return answer
        _reject("Please pick one of: %s" % ", ".join(valid))


def prompt_profile(existing=None):
    """Collect the profile used by the eligibility rules.

    Returns a typed dict, or None if the user cancelled.
    """
    current = existing if isinstance(existing, dict) else {}
    _line()
    print("YOUR PROFILE")
    print("Used to decide which deals you actually qualify for.")
    _line()

    steps = [
        ("name", "Your name",
         lambda values: ask_text(
             "Your name", required=False, max_length=60,
             default=str(values.get("name") or current.get("name") or ""))),
        ("is_student", "Are you a student?",
         lambda values: ask_yes_no(
             "Are you a student?",
             default=bool(values.get("is_student",
                                     current.get("is_student"))))),
        ("school", "School / university",
         lambda values: ask_text(
             "School / university name", required=False, max_length=80,
             default=str(values.get("school") or current.get("school") or ""))
         if values.get("is_student") else ""),
        ("age", "Your age",
         lambda values: ask_int(
             "Your age (0 to skip)", minimum=0, maximum=120,
             default=int(values.get("age", current.get("age") or 0) or 0))),
        ("typical_spend", "Typical spend per purchase",
         lambda values: ask_float(
             "Typical amount you spend in one purchase (%s)" % config.CURRENCY,
             minimum=0.0, maximum=1000000.0,
             default=float(values.get("typical_spend",
                                      current.get("typical_spend") or 0.0) or 0.0))),
    ]

    # This is a top-level screen, so backing out and cancelling both mean
    # "leave my profile alone".
    answers = _collect_with_review("Your profile", steps)
    if answers is None or answers == BACK:
        return None

    age = int(answers.get("age") or 0)
    return {
        "name": str(answers.get("name") or ""),
        "is_student": bool(answers.get("is_student")),
        "school": str(answers.get("school") or ""),
        "age": age,
        "is_senior": age >= 60,
        "typical_spend": float(answers.get("typical_spend") or 0.0),
    }


# ==========================================================================
# Screens: deal intake
# ==========================================================================
def prompt_new_deal():
    """Collect one new deal from the user.

    Returns a typed dict for ai_manager, or None when the user backs out:
        {"source_type": "text"|"manual", "raw_text": str, "hints": dict}
    """
    while True:
        _line()
        print("ADD A DEAL")
        _line()
        method = ask_choice("How do you want to add it?", list(ADD_METHODS))
        if method == BACK:
            return None

        if method.startswith("Paste"):
            pasted = ask_multiline("Paste the deal / promo text below")
            if pasted == BACK:
                continue                     # back to the method question
            return {"source_type": "text", "raw_text": pasted, "hints": {}}

        hints = _prompt_manual_details()
        if hints == BACK:
            continue                         # back to the method question
        if hints is None:
            return None                      # cancelled outright
        return {"source_type": "manual", "raw_text": _hints_to_text(hints),
                "hints": hints}


def _prompt_manual_details():
    """Ask for deal details field by field, then let the user fix any answer.

    Returns the typed hints dict, BACK if the user stepped back past the first
    question, or None if the user cancelled the screen.
    """
    print("Type what you know. Leave a field blank if you are not sure --")
    print("the AI will work out the rest and anything still missing goes to review.")
    print("At any question, type 'b' to go back to the one before it.")

    steps = [
        ("merchant", "Store / brand name",
         lambda values: ask_text("Store / brand name", required=True, max_length=120,
                                 default=str(values.get("merchant") or ""))),
        ("category", "Category",
         lambda values: ask_choice("Category", list(config.VALID_CATEGORIES),
                                   allow_blank=True)),
        ("deal_description", "Deal description",
         lambda values: ask_text("Describe the deal (e.g. 50% off 2nd item)",
                                 required=True, max_length=300,
                                 default=str(values.get("deal_description") or ""))),
        ("original_price", "Original price",
         lambda values: ask_float(
             "Original price (%s, blank if unknown)" % config.CURRENCY,
             minimum=0.0, allow_blank=True, default=values.get("original_price"))),
        ("discounted_price", "Price after discount",
         lambda values: ask_float(
             "Price after discount (%s, blank if unknown)" % config.CURRENCY,
             minimum=0.0, allow_blank=True, default=values.get("discounted_price"))),
        ("expiry_date", "Expiry date",
         lambda values: ask_date("Expiry date", default=values.get("expiry_date"))),
        ("min_spend", "Minimum spend",
         lambda values: ask_float(
             "Minimum spend required (%s, blank if none)" % config.CURRENCY,
             minimum=0.0, allow_blank=True, default=values.get("min_spend"))),
        ("conditions", "Conditions / who it is for",
         lambda values: ask_text("Conditions / who it is for (optional)",
                                 required=False, max_length=300,
                                 default=str(values.get("conditions") or ""))),
        ("single_use", "Can only be used once?",
         lambda values: ask_yes_no("Can it only be used once?",
                                   default=bool(values.get("single_use", True)))),
    ]

    answers = _collect_with_review("Deal details", steps)
    if answers is None or answers == BACK:
        return answers

    # Drop the blanks so the AI is only told what the user actually knows.
    return {key: value for key, value in answers.items()
            if value not in (None, "", [], {})}


def _hints_to_text(hints):
    """Fold manually typed fields into a readable block for the prompt."""
    parts = []
    for key in ("merchant", "deal_description", "category", "original_price",
                "discounted_price", "expiry_date", "min_spend", "conditions",
                "single_use"):
        if key in hints and hints[key] not in (None, "", [], {}):
            parts.append("%s: %s" % (key.replace("_", " "), hints[key]))
    return "\n".join(parts)


def prompt_missing_fields(record, review_reasons):
    """Let the user fill in what the AI could not read.

    Only the fields that are actually missing are asked for. Returns a typed
    dict of fixes, or {} when the user skips or cancels the review.
    """
    _line()
    print("REVIEW THIS DEAL")
    for reason in review_reasons or []:
        print("  - %s" % reason)
    _line()
    fill_in = ask_yes_no("Fill in the missing details now?", default=True,
                         allow_back=False)
    if not fill_in:
        return {}

    from_ai = record.get("ai") if isinstance(record.get("ai"), dict) else {}
    steps = []

    if not from_ai.get("merchant"):
        steps.append((
            "merchant", "Store / brand name",
            lambda values: ask_text("Store / brand name", required=True,
                                    max_length=120,
                                    default=str(values.get("merchant") or ""))))
    if not from_ai.get("category") or from_ai.get("category") == "other":
        steps.append((
            "category", "Category",
            lambda values: ask_choice("Category", list(config.VALID_CATEGORIES),
                                      allow_blank=True)))
    if not from_ai.get("expiry_date"):
        steps.append((
            "expiry_date", "Expiry date",
            lambda values: ask_date("Expiry date", allow_blank=False,
                                    default=values.get("expiry_date"))))
    if from_ai.get("discount_value") is None and from_ai.get("discounted_price") is None:
        steps.append((
            "discount_type", "Kind of discount",
            lambda values: ask_choice("What kind of discount is it?",
                                      list(config.VALID_DISCOUNT_TYPES))))
        steps.append((
            "discount_value", "Discount value",
            lambda values: ask_float("Discount value (percent number, or amount off)",
                                     minimum=0.0, allow_blank=True,
                                     default=values.get("discount_value"))))

    steps.append((
        "restricted", "Is the deal restricted?",
        lambda values: ask_yes_no(
            "Is this deal restricted (students, seniors, age, one school)?",
            default=bool(values.get("restricted", False)))))
    steps.append((
        "students_only", "Students only?",
        lambda values: ask_yes_no("  Students only?",
                                  default=bool(values.get("students_only", False)))
        if values.get("restricted") else False))
    steps.append((
        "seniors_only", "Seniors only?",
        lambda values: ask_yes_no("  Seniors only?",
                                  default=bool(values.get("seniors_only", False)))
        if values.get("restricted") else False))
    steps.append((
        "min_age", "Minimum age",
        lambda values: ask_int("  Minimum age (blank if none)", minimum=0,
                               maximum=120, allow_blank=True,
                               default=values.get("min_age"))
        if values.get("restricted") else None))
    steps.append((
        "school", "Limited to one school",
        lambda values: ask_text("  Limited to one school? (blank if not)",
                                required=False, max_length=80,
                                default=str(values.get("school") or ""))
        if values.get("restricted") else ""))

    answers = _collect_with_review("Missing details", steps)
    if answers is None or answers == BACK:
        display_info("Review cancelled -- the deal stays in the needs-review list.")
        return {}

    fixes = {}
    for key in ("merchant", "category", "expiry_date", "discount_type",
                "discount_value"):
        if answers.get(key) not in (None, ""):
            fixes[key] = answers[key]

    restricted = bool(answers.get("restricted"))
    fixes["eligibility"] = {
        "students_only": bool(answers.get("students_only")) if restricted else False,
        "seniors_only": bool(answers.get("seniors_only")) if restricted else False,
        "min_age": answers.get("min_age") if restricted else None,
        "max_age": None,
        "school": (answers.get("school") or None) if restricted else None,
        "raw": "Confirmed by user" if restricted
               else "No restrictions (confirmed by user)",
    }
    return fixes


def prompt_deal_id(records, purpose="use"):
    """Show a short picker and return the chosen deal id, or None to cancel."""
    if not records:
        display_info("There are no deals to %s yet." % purpose)
        return None
    _line()
    print("PICK A DEAL TO %s" % purpose.upper())
    _line()
    valid = []
    for record in records:
        data = record.get("ai") if isinstance(record.get("ai"), dict) else {}
        valid.append(record.get("id"))
        print("  #%-3s %-26s %-18s expires %s"
              % (record.get("id"),
                 _shorten(data.get("merchant") or "unknown", 26),
                 _shorten(format_discount(data), 18),
                 data.get("expiry_date") or "unknown"))
    while True:
        answer = _read("Deal number (0 or b to go back): ").strip()
        if _is_back(answer):
            return None
        try:
            chosen = int(answer)
        except ValueError:
            _reject("Type the number shown after the # sign.")
            continue
        if chosen == 0:
            return None
        if chosen in valid:
            return chosen
        _reject("No deal with number %d in this list." % chosen)


def prompt_usage_details(record):
    """Ask how much was saved (and the basket total when a minimum applies).

    Returns a typed dict, or None if the user backed out.
    """
    data = record.get("ai") if isinstance(record.get("ai"), dict) else {}
    minimum = data.get("min_spend")
    needs_spend = isinstance(minimum, (int, float)) and minimum > 0

    steps = []
    if needs_spend:
        print("This deal needs a minimum spend of %s%.2f."
              % (config.CURRENCY, minimum))
        steps.append((
            "spend_amount", "Total you spent",
            lambda values: ask_float(
                "How much did you spend in total (%s)?" % config.CURRENCY,
                minimum=0.0, default=values.get("spend_amount"))))
    steps.append((
        "amount_saved", "Amount you saved",
        lambda values: ask_float("How much did you save (%s)?" % config.CURRENCY,
                                 minimum=0.0, default=values.get("amount_saved"))))

    answers = _run_steps(steps, {})
    if answers is None:
        return None
    return {"amount_saved": answers.get("amount_saved"),
            "spend_amount": answers.get("spend_amount") if needs_spend else None}


def prompt_spend_amount():
    """Basket amount to test deals against. None means the user backed out."""
    answer = ask_float("Basket amount you plan to spend (%s)" % config.CURRENCY,
                       minimum=0.0)
    return None if answer == BACK else answer


def prompt_search_term():
    """Search term, or None if the user backed out."""
    answer = ask_text("Search for (shop, category or words in the deal)",
                      required=True, max_length=80)
    return None if answer == BACK else answer


def confirm(question, default=False):
    """Yes/no question with no back option -- the caller needs a decision."""
    return ask_yes_no(question, default=default, allow_back=False)


# ==========================================================================
# Formatting helpers
# ==========================================================================
def format_money(value):
    if not isinstance(value, (int, float)):
        return "-"
    return "%s%.2f" % (config.CURRENCY, value)


def format_discount(data):
    """Human wording for the AI's discount fields."""
    data = data if isinstance(data, dict) else {}
    kind = data.get("discount_type")
    value = data.get("discount_value")
    if kind == "percent" and isinstance(value, (int, float)):
        return "%g%% off" % value
    if kind == "fixed" and isinstance(value, (int, float)):
        return "%s off" % format_money(value)
    if kind == "bogo":
        if isinstance(value, (int, float)):
            return "%g%% off 2nd item" % value
        return "buy-one-get-one"
    if kind == "freebie":
        return "free item / freebie"
    original = data.get("original_price")
    discounted = data.get("discounted_price")
    if isinstance(original, (int, float)) and isinstance(discounted, (int, float)):
        return "%s -> %s" % (format_money(original), format_money(discounted))
    return "discount unclear"


def format_expiry(record):
    data = record.get("ai") if isinstance(record.get("ai"), dict) else record
    expiry = data.get("expiry_date") or "unknown"
    days = record.get("days_left")
    status = STATUS_LABELS.get(record.get("status"), "")
    if isinstance(days, int):
        if days < 0:
            return "%s (%s, %d days ago)" % (expiry, status, abs(days))
        if days == 0:
            return "%s (%s, today!)" % (expiry, status)
        return "%s (%s, %d day%s left)" % (expiry, status, days,
                                           "" if days == 1 else "s")
    return "%s (%s)" % (expiry, status or "no expiry")


def _shorten(text, width):
    text = str(text or "")
    return text if len(text) <= width else text[:width - 1] + "~"


# ==========================================================================
# Display functions
# ==========================================================================
def display_record(record, detailed=True):
    """Print one deal in full."""
    if not record:
        display_info("Nothing to show.")
        return
    data = record.get("ai") if isinstance(record.get("ai"), dict) else {}
    usage = record.get("user") if isinstance(record.get("user"), dict) else {}

    _line()
    print("DEAL #%s -- %s" % (record.get("id", "?"),
                              data.get("merchant") or "unknown store"))
    _line()
    print("  Category    : %s" % (data.get("category") or "unknown"))
    print("  Offer       : %s" % format_discount(data))
    if data.get("description"):
        print("  Details     : %s" % data.get("description"))
    if data.get("original_price") is not None or data.get("discounted_price") is not None:
        print("  Price       : %s -> %s" % (format_money(data.get("original_price")),
                                            format_money(data.get("discounted_price"))))
    print("  Expiry      : %s" % format_expiry(record))
    print("  Status      : %s" % QUEUE_LABELS.get(record.get("queue"),
                                                  record.get("queue") or "unknown"))
    print("  Eligibility : %s" % ("eligible for you" if record.get("eligible")
                                  else "NOT eligible for you"))
    if data.get("min_spend"):
        print("  Min spend   : %s" % format_money(data.get("min_spend")))
    print("  Redemption  : %s" % ("single use" if data.get("single_use")
                                  else "reusable"))
    if usage.get("used"):
        print("  Used        : yes, on %s (you logged %s saved)"
              % (usage.get("used_on") or "unknown date",
                 format_money(usage.get("amount_saved"))))
    else:
        print("  Used        : not yet")

    if not detailed:
        return

    print("  Deal score  : %s/100   AI confidence: %s"
          % (record.get("score", "-"), data.get("confidence", "-")))
    for reason in record.get("eligibility_reasons") or []:
        print("    . %s" % reason)
    for term in data.get("terms") or []:
        print("    T&C: %s" % term)
    if record.get("review_reasons"):
        print("  Needs review because:")
        for reason in record["review_reasons"]:
            print("    ! %s" % reason)
    if record.get("duplicate_of") is not None:
        print("  Possible duplicate of deal #%s: %s"
              % (record.get("duplicate_of"), record.get("duplicate_reason") or ""))


def display_list(records, title="DEALS", empty_message="Nothing here yet."):
    """Print a table of deals."""
    _line("=")
    print("%s (%d)" % (title.upper(), len(records or [])))
    _line("=")
    if not records:
        print("  %s" % empty_message)
        return
    print("  %-4s %-24s %-14s %-16s %-12s %s"
          % ("#", "STORE", "CATEGORY", "OFFER", "EXPIRES", "SCORE"))
    _line()
    for record in records:
        data = record.get("ai") if isinstance(record.get("ai"), dict) else {}
        flag = ""
        if record.get("queue") == "act_now":
            flag = " <-- use it now"
        elif not record.get("eligible"):
            flag = " (not eligible)"
        elif record.get("status") == "expiring_soon":
            flag = " (soon)"
        print("  %-4s %-24s %-14s %-16s %-12s %-5s%s"
              % (record.get("id"),
                 _shorten(data.get("merchant") or "unknown", 24),
                 _shorten(data.get("category") or "-", 14),
                 _shorten(format_discount(data), 16),
                 data.get("expiry_date") or "unknown",
                 record.get("score", "-"),
                 flag))
    _line()


def display_result(result):
    """Print the outcome of processing one deal (the decision dict)."""
    if not isinstance(result, dict) or not result:
        display_info("No result to show.")
        return
    _line("=")
    print("RESULT: %s" % QUEUE_LABELS.get(result.get("queue"),
                                          result.get("queue") or "unknown"))
    _line("=")
    print("  Expiry status : %s" % STATUS_LABELS.get(result.get("status"),
                                                     result.get("status")))
    if isinstance(result.get("days_left"), int):
        print("  Days left     : %d" % result["days_left"])
    print("  Eligible      : %s" % ("yes" if result.get("eligible") else "no"))
    print("  Deal score    : %s/100" % result.get("score", "-"))
    print("  Priority      : %s" % result.get("priority", "-"))

    for reason in result.get("eligibility_reasons") or []:
        print("   . %s" % reason)

    if result.get("needs_review"):
        print("  Sent to the NEEDS REVIEW list because:")
        for reason in result.get("review_reasons") or []:
            print("   ! %s" % reason)
    if result.get("duplicate_of") is not None:
        print("  Possible duplicate of deal #%s" % result["duplicate_of"])
        if result.get("duplicate_reason"):
            print("   ! %s" % result["duplicate_reason"])
    if "min_spend_above_habit" in (result.get("flags") or []):
        print("   ! The minimum spend is well above what you usually spend.")
    if "act_now" in (result.get("flags") or []):
        print("  >> Big saving, you qualify, and it expires within a week. Use it!")
    _line("=")


def display_summary(summary):
    """Print the savings/usage dashboard, all counted by the app itself."""
    _line("=")
    print("SAVINGS SUMMARY")
    _line("=")
    print("  Deals saved        : %d" % summary.get("total_deals", 0))
    print("  Ready to use       : %d (of which use-now: %d)"
          % (summary.get("active", 0), summary.get("act_now", 0)))
    print("  Expiring soon      : %d" % summary.get("expiring_soon", 0))
    print("  Not eligible (yet) : %d" % summary.get("watchlist", 0))
    print("  Needs review       : %d" % summary.get("needs_review", 0))
    print("  Possible duplicates: %d" % summary.get("duplicates", 0))
    print("  Expired            : %d" % summary.get("expired", 0))
    _line()
    print("  Used               : %d" % summary.get("used_count", 0))
    print("  Still unused       : %d" % summary.get("unused_count", 0))
    print("  TOTAL SAVED        : %s" % format_money(summary.get("total_saved", 0.0)))
    by_category = summary.get("by_category") or {}
    if by_category:
        _line()
        print("  Deals per category:")
        for category in sorted(by_category):
            print("    %-14s %d" % (category, by_category[category]))
    _line("=")


def display_profile(profile):
    _line()
    print("PROFILE")
    _line()
    print("  Name          : %s" % (profile.get("name") or "-"))
    print("  Student       : %s" % ("yes" if profile.get("is_student") else "no"))
    print("  School        : %s" % (profile.get("school") or "-"))
    print("  Age           : %s" % (profile.get("age") or "not set"))
    print("  Senior        : %s" % ("yes" if profile.get("is_senior") else "no"))
    print("  Typical spend : %s" % format_money(profile.get("typical_spend")))
    _line()


def display_spend_check(record, check):
    """Print the minimum-spend verdict for one deal."""
    data = record.get("ai") if isinstance(record.get("ai"), dict) else {}
    mark = "OK " if check.get("usable") else "NO "
    print("  %s #%-3s %-24s %-16s %s"
          % (mark, record.get("id"),
             _shorten(data.get("merchant") or "unknown", 24),
             _shorten(format_discount(data), 16),
             check.get("reason", "")))


def display_working(message):
    print("  ... %s" % message)


def display_cancelled(what="That"):
    print("  %s was cancelled -- nothing was saved. Back to the menu." % what)


def display_info(message):
    print("  %s" % message)


def display_success(message):
    print("  [OK] %s" % message)


def display_warning(message):
    print("  [!] %s" % message)


def display_error(message):
    print("  [ERROR] %s" % message)


def display_ai_failure(errors):
    """Explain that the core engine could not deliver a usable record."""
    _line("=")
    print("THE AI COULD NOT PROCESS THIS DEAL")
    _line("=")
    for error in errors or ["Unknown error."]:
        print("  ! %s" % error)
    print("  Nothing was saved -- every deal has to come back from the AI as")
    print("  structured data before DealKeeper will store it.")
    print("  Check your internet connection and GROQ_API_KEY, then try again.")
    print("  Details were written to %s" % config.LOG_FILE)
    _line("=")


def display_goodbye(summary):
    _line("=")
    print("  %d deals tracked, %s saved so far. See you next time!"
          % (summary.get("total_deals", 0),
             format_money(summary.get("total_saved", 0.0))))
    _line("=")

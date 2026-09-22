"""Input/output layer -- the only module allowed to talk to the human.

Every print() and every input() call in DealKeeper lives in this file. The
other three managers are silent: they return data, and this module decides how
it is asked for and how it is shown.

Input rules: nothing leaves this module as a raw string. Values are validated
for type, range and presence, bad input is re-prompted, and the caller receives
a clean typed dict.
"""

import os
from datetime import datetime

import config

WIDTH = 72
DATE_FORMAT = "%Y-%m-%d"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

MENU_OPTIONS = (
    ("1", "Add a deal (scan an image, paste text, or type it in)"),
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


# ==========================================================================
# Validated input collectors
# ==========================================================================
def ask_text(label, required=True, default="", max_length=2000):
    """Ask for a string. Re-prompts while a required answer is blank."""
    suffix = " [%s]" % default if default else ""
    while True:
        answer = _read("%s%s: " % (label, suffix)).strip()
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


def ask_int(label, minimum=None, maximum=None, allow_blank=False, default=None):
    """Ask for a whole number inside an optional range."""
    while True:
        answer = _read("%s: " % label).strip()
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


def ask_float(label, minimum=None, maximum=None, allow_blank=False):
    """Ask for a decimal number (currency symbols and commas are tolerated)."""
    while True:
        answer = _read("%s: " % label).strip()
        if not answer:
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


def ask_yes_no(label, default=None):
    """Ask a yes/no question and return a real boolean."""
    hint = ""
    if default is True:
        hint = " [Y/n]"
    elif default is False:
        hint = " [y/N]"
    else:
        hint = " [y/n]"
    while True:
        answer = _read("%s%s: " % (label, hint)).strip().lower()
        if not answer and default is not None:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        _reject("Please answer y or n.")


def ask_choice(label, options, allow_blank=False):
    """Show a numbered list and return the chosen option value."""
    print("%s:" % label)
    for index, option in enumerate(options, start=1):
        print("   %2d) %s" % (index, option))
    while True:
        answer = _read("Choose 1-%d%s: "
                       % (len(options), " (blank to skip)" if allow_blank else "")
                       ).strip()
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


def ask_date(label, allow_blank=True):
    """Ask for a YYYY-MM-DD date and return it as a validated string."""
    while True:
        answer = _read("%s (YYYY-MM-DD%s): "
                       % (label, ", blank if unknown" if allow_blank else "")).strip()
        if not answer:
            if allow_blank:
                return None
            _reject("A date is required here.")
            continue
        try:
            return datetime.strptime(answer, DATE_FORMAT).date().isoformat()
        except ValueError:
            _reject("Use the format YYYY-MM-DD, for example 2026-08-31.")


def ask_multiline(label):
    """Collect pasted text until the user submits an empty line."""
    print("%s" % label)
    print("(paste as many lines as you like, then press Enter on a blank line)")
    lines = []
    while True:
        line = _read("> ")
        if not line.strip():
            if lines:
                return "\n".join(lines).strip()
            _reject("Nothing pasted yet -- paste the promo text or type it out.")
            continue
        lines.append(line)


def ask_image_path(label):
    """Ask for a readable image file and return its path."""
    while True:
        answer = _read("%s: " % label).strip().strip('"').strip("'")
        if not answer:
            _reject("Please give the path to the image file.")
            continue
        path = os.path.expanduser(answer)
        if not os.path.isfile(path):
            _reject("No file found at '%s'." % path)
            continue
        if not path.lower().endswith(IMAGE_EXTENSIONS):
            _reject("Expected an image file (%s)." % ", ".join(IMAGE_EXTENSIONS))
            continue
        if os.path.getsize(path) > config.MAX_IMAGE_BYTES:
            _reject("That image is larger than %d MB."
                    % (config.MAX_IMAGE_BYTES // (1024 * 1024)))
            continue
        return path


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
    """Collect the profile used by the eligibility rules. Returns a typed dict."""
    current = existing if isinstance(existing, dict) else {}
    _line()
    print("YOUR PROFILE")
    print("Used to decide which deals you actually qualify for.")
    _line()

    name = ask_text("Your name", required=False,
                    default=str(current.get("name") or ""), max_length=60)
    is_student = ask_yes_no("Are you a student?",
                            default=bool(current.get("is_student")))
    school = ""
    if is_student:
        school = ask_text("School / university name", required=False,
                          default=str(current.get("school") or ""), max_length=80)
    age = ask_int("Your age (0 to skip)", minimum=0, maximum=120,
                  default=int(current.get("age") or 0))
    is_senior = age >= 60
    typical_spend = ask_float(
        "Typical amount you spend in one purchase (%s, 0 to skip)" % config.CURRENCY,
        minimum=0.0, maximum=1000000.0)

    profile = {
        "name": name,
        "is_student": bool(is_student),
        "school": school,
        "age": int(age),
        "is_senior": bool(is_senior),
        "typical_spend": float(typical_spend or 0.0),
    }
    display_success("Profile saved.")
    return profile


# ==========================================================================
# Screens: deal intake
# ==========================================================================
def prompt_new_deal():
    """Collect one new deal from the user.

    Returns a typed dict for ai_manager:
        {"source_type": "image"|"text"|"manual",
         "raw_text": str, "image_path": str, "hints": dict}
    """
    _line()
    print("ADD A DEAL")
    _line()
    source = ask_choice("How do you want to add it?", [
        "Scan an image of the voucher/poster",
        "Paste the promo text or chat message",
        "Type the details in myself",
    ])

    record = {"source_type": "text", "raw_text": "", "image_path": "", "hints": {}}

    if source.startswith("Scan"):
        record["source_type"] = "image"
        record["image_path"] = ask_image_path("Path to the image file")
        extra = ask_text("Anything to add about this deal? (optional)",
                         required=False, max_length=500)
        record["raw_text"] = extra
        print("Image queued for AI reading: %s" % os.path.basename(record["image_path"]))

    elif source.startswith("Paste"):
        record["source_type"] = "text"
        record["raw_text"] = ask_multiline("Paste the deal / promo text below")

    else:
        record["source_type"] = "manual"
        record["hints"] = _prompt_manual_details()
        record["raw_text"] = _hints_to_text(record["hints"])

    return record


def _prompt_manual_details():
    """Ask for deal details field by field; every value is typed and validated."""
    print("Type what you know. Leave a field blank if you are not sure --")
    print("the AI will work out the rest and anything still missing goes to review.")
    hints = {}
    hints["merchant"] = ask_text("Store / brand name", required=True, max_length=120)
    hints["category"] = ask_choice("Category", list(config.VALID_CATEGORIES),
                                   allow_blank=True)
    hints["deal_description"] = ask_text("Describe the deal (e.g. 50% off 2nd item)",
                                         required=True, max_length=300)
    original = ask_float("Original price (%s, blank if unknown)" % config.CURRENCY,
                         minimum=0.0, allow_blank=True)
    if original is not None:
        hints["original_price"] = original
    discounted = ask_float("Price after discount (%s, blank if unknown)" % config.CURRENCY,
                           minimum=0.0, allow_blank=True)
    if discounted is not None:
        hints["discounted_price"] = discounted
    expiry = ask_date("Expiry date")
    if expiry:
        hints["expiry_date"] = expiry
    minimum = ask_float("Minimum spend required (%s, blank if none)" % config.CURRENCY,
                        minimum=0.0, allow_blank=True)
    if minimum is not None:
        hints["min_spend"] = minimum
    conditions = ask_text("Conditions / who it is for (optional)",
                          required=False, max_length=300)
    if conditions:
        hints["conditions"] = conditions
    hints["single_use"] = ask_yes_no("Can it only be used once?", default=True)
    return hints


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
    """Let the user fill in what the AI could not read. Returns a typed dict."""
    _line()
    print("REVIEW THIS DEAL")
    for reason in review_reasons or []:
        print("  - %s" % reason)
    _line()
    if not ask_yes_no("Fill in the missing details now?", default=True):
        return {}

    from_ai = record.get("ai") if isinstance(record.get("ai"), dict) else {}
    fixes = {}

    if not from_ai.get("merchant"):
        fixes["merchant"] = ask_text("Store / brand name", required=True, max_length=120)
    if not from_ai.get("category") or from_ai.get("category") == "other":
        chosen = ask_choice("Category", list(config.VALID_CATEGORIES), allow_blank=True)
        if chosen:
            fixes["category"] = chosen
    if not from_ai.get("expiry_date"):
        expiry = ask_date("Expiry date", allow_blank=False)
        if expiry:
            fixes["expiry_date"] = expiry
    if from_ai.get("discount_value") is None and from_ai.get("discounted_price") is None:
        discount_type = ask_choice("What kind of discount is it?",
                                   list(config.VALID_DISCOUNT_TYPES))
        fixes["discount_type"] = discount_type
        value = ask_float("Discount value (percent number, or amount off)",
                          minimum=0.0, allow_blank=True)
        if value is not None:
            fixes["discount_value"] = value

    eligibility = {}
    if ask_yes_no("Is this deal restricted (students, seniors, age, one school)?",
                  default=False):
        eligibility["students_only"] = ask_yes_no("  Students only?", default=False)
        eligibility["seniors_only"] = ask_yes_no("  Seniors only?", default=False)
        age_limit = ask_int("  Minimum age (blank if none)", minimum=0, maximum=120,
                            allow_blank=True)
        if age_limit is not None:
            eligibility["min_age"] = age_limit
        school = ask_text("  Limited to one school? (blank if not)",
                          required=False, max_length=80)
        if school:
            eligibility["school"] = school
        eligibility["raw"] = "Confirmed by user"
    else:
        eligibility = {"students_only": False, "seniors_only": False,
                       "min_age": None, "max_age": None, "school": None,
                       "raw": "No restrictions (confirmed by user)"}
    fixes["eligibility"] = eligibility
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
        answer = _read("Deal number (0 to cancel): ").strip()
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
    """Ask how much was saved (and optionally the basket total)."""
    data = record.get("ai") if isinstance(record.get("ai"), dict) else {}
    minimum = data.get("min_spend")
    spend = None
    if isinstance(minimum, (int, float)) and minimum > 0:
        print("This deal needs a minimum spend of %s%.2f."
              % (config.CURRENCY, minimum))
        spend = ask_float("How much did you spend in total (%s)?" % config.CURRENCY,
                          minimum=0.0)
    saved = ask_float("How much did you save (%s)?" % config.CURRENCY, minimum=0.0)
    return {"amount_saved": saved, "spend_amount": spend}


def prompt_spend_amount():
    return ask_float("Basket amount you plan to spend (%s)" % config.CURRENCY,
                     minimum=0.0)


def prompt_search_term():
    return ask_text("Search for (shop, category or words in the deal)",
                    required=True, max_length=80)


def confirm(question, default=False):
    return ask_yes_no(question, default=default)


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

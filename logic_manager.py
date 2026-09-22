"""Logic layer -- DealKeeper's domain brain.

Everything in this module acts on the structured payload that ai_manager
produced. It decides whether a deal is live, expiring, expired, eligible for
this particular user, a duplicate of something already saved, usable against a
minimum spend, or too incomplete to trust.

Two deliberate boundaries:
  * no API calls here -- the AI has already spoken by the time we are called;
  * no print/input here -- io_manager owns every character the user sees.

Money maths (total savings, used vs unused counts) is done here from what the
user logged. The AI never estimates those numbers.
"""

import difflib
import logging
from datetime import date, datetime

import config

LOGGER = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_EXPIRING = "expiring_soon"
STATUS_EXPIRED = "expired"
STATUS_UNKNOWN = "unknown_expiry"

QUEUE_ACTIVE = "active"
QUEUE_ACT_NOW = "act_now"
QUEUE_WATCHLIST = "watchlist"
QUEUE_NEEDS_REVIEW = "needs_review"
QUEUE_DUPLICATE = "duplicate_review"
QUEUE_EXPIRED = "expired"
QUEUE_USED = "used"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def ai_block(record):
    """Return the AI payload of a record, accepting a bare payload too."""
    if not isinstance(record, dict):
        return {}
    inner = record.get("ai")
    if isinstance(inner, dict):
        return inner
    return record


def user_block(record):
    """Return the user-maintained part of a record (usage, savings)."""
    if not isinstance(record, dict):
        return {}
    inner = record.get("user")
    return inner if isinstance(inner, dict) else {}


def today_or(today):
    """Resolve an optional date argument; keeps every rule testable."""
    if isinstance(today, date):
        return today
    if isinstance(today, str) and today:
        try:
            return datetime.strptime(today[:10], "%Y-%m-%d").date()
        except ValueError:
            LOGGER.warning("Bad 'today' value %r; using the system date.", today)
    return date.today()


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) \
        else None


# --------------------------------------------------------------------------
# Rule 1: expiry detection
# --------------------------------------------------------------------------
def check_expiry(expiry_date, today=None):
    """Compare a deal's expiry to today. Returns (status, days_left).

    days_left is None when the AI could not find an expiry date.
    """
    now = today_or(today)
    if not expiry_date:
        return STATUS_UNKNOWN, None
    try:
        expiry = datetime.strptime(str(expiry_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        LOGGER.warning("Unparseable expiry %r treated as unknown.", expiry_date)
        return STATUS_UNKNOWN, None

    days_left = (expiry - now).days
    if days_left < 0:
        return STATUS_EXPIRED, days_left
    if days_left <= config.EXPIRING_SOON_DAYS:
        return STATUS_EXPIRING, days_left
    return STATUS_ACTIVE, days_left


# --------------------------------------------------------------------------
# Rule 2: eligibility against the saved profile
# --------------------------------------------------------------------------
def check_eligibility(eligibility, profile):
    """Turn the AI's eligibility object into a yes/no for this user.

    Returns (eligible, reasons). Not-eligible deals are still kept and shown --
    they are simply tagged so the user is not misled.
    """
    rules = eligibility if isinstance(eligibility, dict) else {}
    person = profile if isinstance(profile, dict) else {}
    reasons = []
    eligible = True

    is_student = bool(person.get("is_student"))
    age = _number(person.get("age")) or 0
    is_senior = bool(person.get("is_senior")) or age >= 60
    school = str(person.get("school") or "").strip()

    if rules.get("students_only"):
        if is_student:
            reasons.append("Student-only deal and your profile says you are a student.")
        else:
            eligible = False
            reasons.append("Students only -- your profile is not marked as a student.")

    if rules.get("seniors_only"):
        if is_senior:
            reasons.append("Senior-only deal and your profile qualifies.")
        else:
            eligible = False
            reasons.append("Seniors only -- your profile does not qualify.")

    required_school = str(rules.get("school") or "").strip()
    if required_school:
        if school and name_similarity(school, required_school) >= config.DUPLICATE_NAME_RATIO:
            reasons.append("Limited to %s, which matches your school." % required_school)
        else:
            eligible = False
            reasons.append("Limited to %s -- your profile says '%s'."
                           % (required_school, school or "no school"))

    min_age = _number(rules.get("min_age"))
    max_age = _number(rules.get("max_age"))
    if min_age is not None or max_age is not None:
        if age <= 0:
            eligible = False
            reasons.append("Age-restricted deal but your profile has no age saved.")
        else:
            if min_age is not None and age < min_age:
                eligible = False
                reasons.append("Minimum age %s, your profile says %s."
                               % (int(min_age), int(age)))
            if max_age is not None and age > max_age:
                eligible = False
                reasons.append("Maximum age %s, your profile says %s."
                               % (int(max_age), int(age)))
            if eligible:
                reasons.append("Your age fits the deal's age limits.")

    # Conditions the profile cannot prove either way: keep the deal eligible
    # but tell the user what to check at the counter.
    if rules.get("members_only"):
        reasons.append("Members only -- check you have the membership.")
    if rules.get("new_customers_only"):
        reasons.append("New customers only -- check this is your first order.")

    if not reasons:
        reasons.append("No eligibility restrictions found.")
    return eligible, reasons


# --------------------------------------------------------------------------
# Rule 3: incomplete extraction -> needs review
# --------------------------------------------------------------------------
def check_needs_review(record):
    """Decide whether a deal is too incomplete for the active list.

    Returns (needs_review, reasons).
    """
    data = ai_block(record)
    reasons = []

    if not data.get("merchant"):
        reasons.append("Store/brand name is missing.")
    if not data.get("category"):
        reasons.append("Category is missing.")
    if not data.get("expiry_date"):
        reasons.append("No expiry date could be read.")

    has_number = _number(data.get("discount_value")) is not None
    has_price_pair = _number(data.get("discounted_price")) is not None
    if not has_number and not has_price_pair \
            and data.get("discount_type") not in ("freebie", "bogo"):
        reasons.append("No discount amount or discounted price could be read.")

    confidence = _number(data.get("confidence"))
    if confidence is None or confidence < config.MIN_CONFIDENCE:
        reasons.append("AI confidence %.2f is below the %.2f threshold."
                       % (confidence or 0.0, config.MIN_CONFIDENCE))

    reported = [str(item).lower() for item in data.get("missing_fields") or []]
    for field in reported:
        if field in ("merchant", "category", "expiry_date", "discount", "eligibility"):
            reasons.append("AI reported '%s' as unreadable." % field)

    # Deduplicate while keeping order stable for reproducible output.
    seen = []
    for reason in reasons:
        if reason not in seen:
            seen.append(reason)
    return bool(seen), seen


def complete_missing_fields(record, manual_fields):
    """Fold user-typed corrections into a needs-review record.

    Only the fields the user actually filled in are overwritten; the AI's other
    findings are kept. The record remembers what was fixed by hand.
    """
    updated = dict(record if isinstance(record, dict) else {})
    data = dict(ai_block(updated))
    applied = []

    for key, value in (manual_fields or {}).items():
        if value in (None, "", [], {}):
            continue
        if key == "eligibility" and isinstance(value, dict):
            merged = dict(data.get("eligibility") or {})
            merged.update(value)
            data["eligibility"] = merged
        else:
            data[key] = value
        applied.append(key)

    if applied:
        # A human confirmed these fields, so the record is trustworthy now.
        data["confidence"] = max(_number(data.get("confidence")) or 0.0,
                                 config.MIN_CONFIDENCE)
        remaining = [item for item in data.get("missing_fields") or []
                     if str(item).lower() not in applied]
        data["missing_fields"] = remaining

    updated["ai"] = data
    history = list(updated.get("manual_overrides") or [])
    updated["manual_overrides"] = history + [item for item in applied
                                             if item not in history]
    return updated


# --------------------------------------------------------------------------
# Rule 4: duplicate detection
# --------------------------------------------------------------------------
def name_similarity(left, right):
    """Similarity (0-1) between two merchant names, ignoring case/punctuation."""
    return difflib.SequenceMatcher(
        None, _normalise_name(left), _normalise_name(right)
    ).ratio()


def _normalise_name(value):
    text = str(value or "").lower()
    return "".join(char for char in text if char.isalnum())


def similar_discount(left, right):
    """True when two AI payloads describe roughly the same offer."""
    if (left.get("discount_type") or "") != (right.get("discount_type") or ""):
        return False

    left_value = _number(left.get("discount_value"))
    right_value = _number(right.get("discount_value"))
    if left_value is None and right_value is None:
        return True
    if left_value is None or right_value is None:
        return False

    tolerance = config.DUPLICATE_PERCENT_TOLERANCE \
        if left.get("discount_type") == "percent" \
        else config.DUPLICATE_FIXED_TOLERANCE
    return abs(left_value - right_value) <= tolerance


def find_duplicate(record, existing_records):
    """Look for an already-saved deal that is effectively the same offer.

    Matching needs the same shop (fuzzy name match) AND a similar discount.
    Expired and used-up deals are ignored -- re-saving those is not a mistake.

    Returns {} when nothing matches, otherwise
    {"duplicate_of", "merchant", "similarity", "reason"}.
    """
    data = ai_block(record)
    merchant = data.get("merchant")
    if not merchant:
        return {}

    record_id = record.get("id") if isinstance(record, dict) else None

    for other in existing_records or []:
        if not isinstance(other, dict):
            continue
        if record_id is not None and other.get("id") == record_id:
            continue
        if other.get("queue") in (QUEUE_EXPIRED, QUEUE_USED):
            continue
        if other.get("status") == STATUS_EXPIRED:
            continue

        other_data = ai_block(other)
        ratio = name_similarity(merchant, other_data.get("merchant"))
        if ratio < config.DUPLICATE_NAME_RATIO:
            continue
        if not similar_discount(data, other_data):
            continue

        reason = "Same shop (%s, %d%% name match) with a similar %s discount." % (
            other_data.get("merchant") or "unknown",
            round(ratio * 100),
            other_data.get("discount_type") or "unknown",
        )
        if data.get("expiry_date") and \
                data.get("expiry_date") == other_data.get("expiry_date"):
            reason += " Same expiry date too."
        LOGGER.info("Deal flagged as possible duplicate of %s", other.get("id"))
        return {
            "duplicate_of": other.get("id"),
            "merchant": other_data.get("merchant"),
            "similarity": round(ratio, 2),
            "reason": reason,
        }
    return {}


# --------------------------------------------------------------------------
# Rule 5: minimum spend
# --------------------------------------------------------------------------
def check_min_spend(record, spend_amount):
    """Can this deal be used for a basket of spend_amount?

    Returns {"usable", "shortfall", "min_spend", "reason"}.
    """
    data = ai_block(record)
    minimum = _number(data.get("min_spend"))
    basket = _number(spend_amount) or 0.0

    if minimum is None or minimum <= 0:
        return {"usable": True, "shortfall": 0.0, "min_spend": None,
                "reason": "No minimum spend on this deal."}
    if basket >= minimum:
        return {"usable": True, "shortfall": 0.0, "min_spend": minimum,
                "reason": "Spend of %s%.2f meets the %s%.2f minimum."
                          % (config.CURRENCY, basket, config.CURRENCY, minimum)}

    shortfall = round(minimum - basket, 2)
    return {"usable": False, "shortfall": shortfall, "min_spend": minimum,
            "reason": "Needs %s%.2f more to reach the %s%.2f minimum spend."
                      % (config.CURRENCY, shortfall, config.CURRENCY, minimum)}


# --------------------------------------------------------------------------
# Scoring -- how much attention does this deal deserve?
# --------------------------------------------------------------------------
def score(record, profile=None, today=None):
    """Numeric 0-100 score built from AI output fields plus the user profile.

    Used for ranking the active list and for the act-now threshold.
    """
    data = ai_block(record)
    person = profile if isinstance(profile, dict) else {}
    total = 0.0

    # Discount strength (max 40)
    discount_type = data.get("discount_type")
    value = _number(data.get("discount_value"))
    original = _number(data.get("original_price"))
    discounted = _number(data.get("discounted_price"))

    if discount_type == "percent" and value is not None:
        total += min(value, 70.0) / 70.0 * 40.0
    elif discount_type == "bogo" and value is not None:
        total += min(value / 2.0, 70.0) / 70.0 * 40.0
    elif discount_type == "fixed" and value is not None:
        if original and original > 0:
            total += min(value / original, 0.7) / 0.7 * 40.0
        else:
            total += min(value, 50.0) / 50.0 * 30.0
    elif original and discounted is not None and original > 0:
        saved_ratio = max(0.0, (original - discounted) / original)
        total += min(saved_ratio, 0.7) / 0.7 * 40.0
    elif discount_type == "freebie":
        total += 20.0

    # Urgency (max 20) -- worthless once expired
    status, days_left = check_expiry(data.get("expiry_date"), today)
    if status == STATUS_EXPIRED:
        return 0.0
    if status == STATUS_EXPIRING:
        total += 20.0
    elif status == STATUS_ACTIVE and days_left is not None:
        total += 12.0 if days_left <= 30 else 6.0
    else:
        total += 4.0

    # Eligibility (max 20)
    eligible, _ = check_eligibility(data.get("eligibility"), person)
    if eligible:
        total += 20.0

    # Trust in the extraction (max 10)
    total += (_number(data.get("confidence")) or 0.0) * 10.0

    # Practicality: a minimum spend far above the user's habits is a burden.
    minimum = _number(data.get("min_spend"))
    typical = _number(person.get("typical_spend")) or 0.0
    if minimum and typical > 0 and minimum > typical * 1.5:
        total -= 10.0
    elif minimum and typical > 0 and minimum <= typical:
        total += 5.0

    # Already-used single-use vouchers should never top the list.
    if user_block(record).get("used") and data.get("single_use"):
        total -= 30.0

    return round(max(0.0, min(100.0, total)), 2)


# --------------------------------------------------------------------------
# evaluate -- run every rule and return one decision
# --------------------------------------------------------------------------
def evaluate(record, profile=None, existing_records=None, today=None):
    """Apply all business rules to an AI-enriched record.

    Returns a decision dict; it never mutates the record it was given.
    """
    data = ai_block(record)
    person = profile if isinstance(profile, dict) else {}
    now = today_or(today)

    status, days_left = check_expiry(data.get("expiry_date"), now)
    eligible, eligibility_reasons = check_eligibility(data.get("eligibility"), person)
    needs_review, review_reasons = check_needs_review(record)
    duplicate = find_duplicate(record, existing_records) if existing_records else {}

    usage = user_block(record)
    used = bool(usage.get("used"))
    single_use = bool(data.get("single_use"))
    value = _number(data.get("discount_value")) or 0.0
    confidence = _number(data.get("confidence")) or 0.0
    minimum = _number(data.get("min_spend"))
    typical = _number(person.get("typical_spend")) or 0.0

    flags = []

    # --- multi-condition rule A: act now -------------------------------------
    # Four AI fields plus the profile must agree: the deal is eligible for this
    # user, it dies within the week, the saving is worth the trip, and the
    # extraction is trustworthy enough to act on without re-reading the
    # voucher.
    strong_discount = (
        (data.get("discount_type") == "percent" and value >= config.STRONG_PERCENT_DISCOUNT)
        or (data.get("discount_type") == "bogo" and value >= config.STRONG_PERCENT_DISCOUNT)
        or (data.get("discount_type") == "fixed" and value >= config.STRONG_FIXED_DISCOUNT)
    )
    act_now = (
        eligible
        and status == STATUS_EXPIRING
        and strong_discount
        and confidence >= config.HIGH_CONFIDENCE
        and not needs_review
        and not (used and single_use)
    )
    if act_now:
        flags.append("act_now")

    # --- multi-condition rule B: minimum spend vs spending habits -----------
    if minimum and typical > 0 and minimum > typical * 1.5:
        flags.append("min_spend_above_habit")
    if minimum and eligible and status != STATUS_EXPIRED:
        flags.append("min_spend_required")

    # --- other flags --------------------------------------------------------
    if used and single_use:
        flags.append("single_use_spent")
    elif used:
        flags.append("used_reusable")
    if status == STATUS_EXPIRED:
        flags.append("hide_from_active")
    if not eligible:
        flags.append("not_eligible")
    if duplicate:
        flags.append("possible_duplicate")

    decision = {
        "deal_id": record.get("id") if isinstance(record, dict) else None,
        "status": status,
        "days_left": days_left,
        "eligible": eligible,
        "eligibility_reasons": eligibility_reasons,
        "needs_review": needs_review,
        "review_reasons": review_reasons,
        "duplicate_of": duplicate.get("duplicate_of"),
        "duplicate_reason": duplicate.get("reason", ""),
        "duplicate_similarity": duplicate.get("similarity"),
        "used": used,
        "single_use": single_use,
        "min_spend": minimum,
        "score": score(record, person, now),
        "flags": flags,
        "priority": "act_now" if act_now else ("low" if not eligible else "normal"),
        "evaluated_on": now.isoformat(),
    }
    decision["queue"] = route(record, decision)
    LOGGER.info("Evaluated deal %s -> queue=%s status=%s score=%s",
                decision["deal_id"], decision["queue"], status, decision["score"])
    return decision


# --------------------------------------------------------------------------
# route -- where does this record belong?
# --------------------------------------------------------------------------
def route(record, decision=None, profile=None, existing_records=None, today=None):
    """Assign a record to exactly one queue based on its evaluation."""
    verdict = decision if isinstance(decision, dict) else evaluate(
        record, profile, existing_records, today)

    if verdict.get("used") and verdict.get("single_use"):
        return QUEUE_USED
    if verdict.get("status") == STATUS_EXPIRED:
        return QUEUE_EXPIRED
    if verdict.get("duplicate_of") is not None:
        return QUEUE_DUPLICATE
    if verdict.get("needs_review"):
        return QUEUE_NEEDS_REVIEW
    if not verdict.get("eligible"):
        return QUEUE_WATCHLIST
    if "act_now" in (verdict.get("flags") or []):
        return QUEUE_ACT_NOW
    return QUEUE_ACTIVE


def apply_decision(record, decision):
    """Return a copy of record with the decision fields written onto it."""
    updated = dict(record if isinstance(record, dict) else {})
    verdict = decision if isinstance(decision, dict) else {}
    for key in ("status", "days_left", "eligible", "eligibility_reasons",
                "needs_review", "review_reasons", "duplicate_of",
                "duplicate_reason", "score", "flags", "priority", "queue",
                "evaluated_on"):
        updated[key] = verdict.get(key)
    return updated


# --------------------------------------------------------------------------
# Usage and savings -- calculated by the app, never by the AI
# --------------------------------------------------------------------------
def mark_used(record, amount_saved=0.0, today=None, spend_amount=None):
    """Mark a deal as used and log how much the user saved.

    Refuses when the deal is expired, already spent, or when a provided basket
    amount does not meet the deal's minimum spend. Returns (ok, record, message).
    """
    if not isinstance(record, dict) or not record:
        return False, {}, "That deal could not be found."

    data = ai_block(record)
    usage = dict(user_block(record))
    now = today_or(today)

    status, _ = check_expiry(data.get("expiry_date"), now)
    if status == STATUS_EXPIRED:
        return False, record, "This deal expired on %s and cannot be used." % \
            data.get("expiry_date")
    if usage.get("used") and data.get("single_use"):
        return False, record, "This single-use deal was already marked as used."

    if spend_amount is not None:
        spend_check = check_min_spend(record, spend_amount)
        if not spend_check["usable"]:
            return False, record, spend_check["reason"]

    saved = _number(amount_saved) or 0.0
    if saved < 0:
        return False, record, "Saved amount cannot be negative."

    usage["used"] = True
    usage["used_on"] = now.isoformat()
    usage["times_used"] = int(usage.get("times_used") or 0) + 1
    usage["amount_saved"] = round((_number(usage.get("amount_saved")) or 0.0) + saved, 2)
    if spend_amount is not None:
        usage["last_spend"] = round(_number(spend_amount) or 0.0, 2)

    updated = dict(record)
    updated["user"] = usage
    message = "Marked as used. %s%.2f added to your savings." % (config.CURRENCY, saved)
    LOGGER.info("Deal %s marked used (+%.2f)", record.get("id"), saved)
    return True, updated, message


def compute_summary(records):
    """Totals derived from the user's own log -- no AI estimates involved."""
    summary = {
        "total_deals": 0,
        "active": 0,
        "act_now": 0,
        "expiring_soon": 0,
        "expired": 0,
        "needs_review": 0,
        "duplicates": 0,
        "watchlist": 0,
        "used_count": 0,
        "unused_count": 0,
        "eligible_count": 0,
        "total_saved": 0.0,
        "by_category": {},
    }

    for record in records or []:
        if not isinstance(record, dict):
            continue
        summary["total_deals"] += 1
        data = ai_block(record)
        usage = user_block(record)
        queue = record.get("queue")
        status = record.get("status")

        if queue == QUEUE_ACTIVE:
            summary["active"] += 1
        elif queue == QUEUE_ACT_NOW:
            summary["active"] += 1
            summary["act_now"] += 1
        elif queue == QUEUE_NEEDS_REVIEW:
            summary["needs_review"] += 1
        elif queue == QUEUE_DUPLICATE:
            summary["duplicates"] += 1
        elif queue == QUEUE_WATCHLIST:
            summary["watchlist"] += 1
        elif queue == QUEUE_EXPIRED:
            summary["expired"] += 1

        if status == STATUS_EXPIRING:
            summary["expiring_soon"] += 1
        if record.get("eligible"):
            summary["eligible_count"] += 1

        if usage.get("used"):
            summary["used_count"] += 1
        else:
            summary["unused_count"] += 1
        summary["total_saved"] += _number(usage.get("amount_saved")) or 0.0

        category = data.get("category") or "other"
        summary["by_category"][category] = summary["by_category"].get(category, 0) + 1

    summary["total_saved"] = round(summary["total_saved"], 2)
    return summary


def refresh_statuses(records, profile=None, today=None):
    """Re-run expiry/eligibility/scoring over every stored deal.

    Called on startup so "expiring soon" and "expired" are always measured
    against the current date rather than the date the deal was saved.
    Duplicate checking is skipped here -- that is an intake-time rule.
    Returns (updated_records, changed_count).
    """
    now = today_or(today)
    updated = []
    changed = 0

    for record in records or []:
        if not isinstance(record, dict):
            continue
        before = (record.get("status"), record.get("queue"), record.get("score"))
        keep_duplicate = record.get("duplicate_of")
        decision = evaluate(record, profile, None, now)

        if keep_duplicate is not None:
            # A deal the user already acknowledged as a duplicate stays there
            # until they resolve it.
            decision["duplicate_of"] = keep_duplicate
            decision["duplicate_reason"] = record.get("duplicate_reason", "")
            decision["queue"] = route(record, decision)

        fresh = apply_decision(record, decision)
        if (fresh.get("status"), fresh.get("queue"), fresh.get("score")) != before:
            changed += 1
        updated.append(fresh)

    return updated, changed


def rank(records):
    """Sort deals by score (high first), then soonest expiry, then id."""
    def key(record):
        score_value = _number(record.get("score"))
        if score_value is None:
            score_value = score(record)
        days = record.get("days_left")
        days = days if isinstance(days, int) else 9999
        deal_id = record.get("id") if isinstance(record.get("id"), int) else 0
        return (-score_value, days, deal_id)
    return sorted([item for item in records or [] if isinstance(item, dict)], key=key)


def filter_by_queue(queue_name):
    """Build a filter function for data_manager.query()."""
    def matcher(record):
        return record.get("queue") == queue_name
    return matcher


def filter_search(term):
    """Build a filter function matching merchant, category or description."""
    needle = str(term or "").strip().lower()

    def matcher(record):
        data = ai_block(record)
        haystack = " ".join([
            str(data.get("merchant") or ""),
            str(data.get("category") or ""),
            str(data.get("description") or ""),
        ]).lower()
        return needle in haystack
    return matcher


def filter_usable_for_spend(spend_amount):
    """Build a filter for deals a given basket amount can actually redeem."""
    def matcher(record):
        if record.get("queue") in (QUEUE_EXPIRED, QUEUE_USED, QUEUE_NEEDS_REVIEW):
            return False
        if not record.get("eligible"):
            return False
        return check_min_spend(record, spend_amount)["usable"]
    return matcher

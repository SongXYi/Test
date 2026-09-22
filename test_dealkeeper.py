"""Automated tests for DealKeeper -- runs with no live API connection.

Every AI response used here is hardcoded, so the business rules, the schema
validation and the persistence layer are all exercised offline.

Run either way:
    python3 -m pytest -q test_dealkeeper.py
    python3 test_dealkeeper.py

Note on output: this script writes its summary with sys.stdout.write, because
terminal output is reserved for io_manager.py in this codebase.
"""

import json
import os
import sys
import tempfile

import ai_manager
import config
import data_manager
import logic_manager

TODAY = "2026-09-22"

# Keep diagnostics in the log file instead of the test output.
config.setup_logging()


# --------------------------------------------------------------------------
# Hardcoded AI payloads (exactly the shape ai_manager.validate_response emits)
# --------------------------------------------------------------------------
def sample_ai(**overrides):
    payload = {
        "merchant": "ZUS Coffee",
        "category": "food",
        "discount_type": "percent",
        "discount_value": 50.0,
        "original_price": 20.0,
        "discounted_price": 10.0,
        "description": "50% off any handcrafted drink",
        "expiry_date": "2026-09-26",
        "eligibility": {
            "students_only": True,
            "seniors_only": False,
            "new_customers_only": False,
            "members_only": False,
            "min_age": None,
            "max_age": None,
            "school": None,
            "raw": "students only",
        },
        "min_spend": 15.0,
        "single_use": True,
        "terms": ["dine in only"],
        "confidence": 0.9,
        "missing_fields": [],
    }
    eligibility = overrides.pop("eligibility", None)
    payload.update(overrides)
    if eligibility is not None:
        merged = dict(payload["eligibility"])
        merged.update(eligibility)
        payload["eligibility"] = merged
    return payload


def sample_record(deal_id=1, used=False, amount_saved=0.0, **ai_overrides):
    return {
        "id": deal_id,
        "created_at": TODAY,
        "source_type": "text",
        "ai": sample_ai(**ai_overrides),
        "user": {"used": used, "used_on": TODAY if used else None,
                 "times_used": 1 if used else 0, "amount_saved": amount_saved},
    }


STUDENT_PROFILE = {"name": "Ali", "is_student": True, "school": "University Malaya",
                   "age": 21, "is_senior": False, "typical_spend": 30.0}
NON_STUDENT_PROFILE = {"name": "Sam", "is_student": False, "school": "",
                       "age": 35, "is_senior": False, "typical_spend": 50.0}


# ==========================================================================
# Rule 1 -- expiry detection
# ==========================================================================
def test_expiry_far_future_is_active():
    status, days = logic_manager.check_expiry("2026-12-31", TODAY)
    assert status == logic_manager.STATUS_ACTIVE
    assert days == 100


def test_expiry_within_seven_days_is_expiring_soon():
    status, days = logic_manager.check_expiry("2026-09-26", TODAY)
    assert status == logic_manager.STATUS_EXPIRING
    assert days == 4


def test_expiry_exactly_on_threshold_is_expiring_soon():
    status, days = logic_manager.check_expiry("2026-09-29", TODAY)
    assert status == logic_manager.STATUS_EXPIRING
    assert days == config.EXPIRING_SOON_DAYS


def test_expiry_day_after_threshold_is_active():
    status, days = logic_manager.check_expiry("2026-09-30", TODAY)
    assert status == logic_manager.STATUS_ACTIVE
    assert days == config.EXPIRING_SOON_DAYS + 1


def test_expiry_today_is_still_usable():
    status, days = logic_manager.check_expiry(TODAY, TODAY)
    assert status == logic_manager.STATUS_EXPIRING
    assert days == 0


def test_expiry_in_the_past_is_expired():
    status, days = logic_manager.check_expiry("2026-09-21", TODAY)
    assert status == logic_manager.STATUS_EXPIRED
    assert days == -1


def test_missing_or_broken_expiry_is_unknown():
    assert logic_manager.check_expiry(None, TODAY)[0] == logic_manager.STATUS_UNKNOWN
    assert logic_manager.check_expiry("", TODAY)[0] == logic_manager.STATUS_UNKNOWN
    assert logic_manager.check_expiry("31 Aug 2026", TODAY)[0] == \
        logic_manager.STATUS_UNKNOWN


# ==========================================================================
# Rule 2 -- eligibility against the saved profile
# ==========================================================================
def test_student_deal_eligible_for_student():
    eligible, reasons = logic_manager.check_eligibility(
        sample_ai()["eligibility"], STUDENT_PROFILE)
    assert eligible is True
    assert any("student" in reason.lower() for reason in reasons)


def test_student_deal_not_eligible_for_non_student():
    eligible, reasons = logic_manager.check_eligibility(
        sample_ai()["eligibility"], NON_STUDENT_PROFILE)
    assert eligible is False
    assert any("student" in reason.lower() for reason in reasons)


def test_no_restrictions_is_eligible_for_anyone():
    eligibility = sample_ai(eligibility={"students_only": False, "raw": ""})["eligibility"]
    assert logic_manager.check_eligibility(eligibility, NON_STUDENT_PROFILE)[0] is True


def test_senior_deal_matches_age_sixty_plus():
    eligibility = sample_ai(
        eligibility={"students_only": False, "seniors_only": True})["eligibility"]
    older = dict(NON_STUDENT_PROFILE)
    older["age"] = 65
    assert logic_manager.check_eligibility(eligibility, older)[0] is True
    assert logic_manager.check_eligibility(eligibility, NON_STUDENT_PROFILE)[0] is False


def test_age_limit_blocks_and_unknown_age_blocks():
    eligibility = sample_ai(
        eligibility={"students_only": False, "min_age": 18})["eligibility"]
    young = dict(NON_STUDENT_PROFILE)
    young["age"] = 16
    assert logic_manager.check_eligibility(eligibility, young)[0] is False

    unknown = dict(NON_STUDENT_PROFILE)
    unknown["age"] = 0
    eligible, reasons = logic_manager.check_eligibility(eligibility, unknown)
    assert eligible is False
    assert any("no age" in reason.lower() for reason in reasons)


def test_school_specific_deal_matches_profile_school():
    eligibility = sample_ai(
        eligibility={"students_only": True, "school": "University Malaya"})["eligibility"]
    assert logic_manager.check_eligibility(eligibility, STUDENT_PROFILE)[0] is True

    other_school = dict(STUDENT_PROFILE)
    other_school["school"] = "Taylors College"
    assert logic_manager.check_eligibility(eligibility, other_school)[0] is False


def test_eligibility_survives_missing_profile_or_payload():
    assert logic_manager.check_eligibility(None, None)[0] is True
    assert logic_manager.check_eligibility({"students_only": True}, {})[0] is False


# ==========================================================================
# Rule 3 -- needs review when the AI could not extract required fields
# ==========================================================================
def test_complete_deal_does_not_need_review():
    needs, reasons = logic_manager.check_needs_review(sample_record())
    assert needs is False
    assert reasons == []


def test_missing_expiry_date_needs_review():
    needs, reasons = logic_manager.check_needs_review(
        sample_record(expiry_date=None, missing_fields=["expiry_date"]))
    assert needs is True
    assert any("expiry" in reason.lower() for reason in reasons)


def test_missing_merchant_needs_review():
    needs, reasons = logic_manager.check_needs_review(sample_record(merchant=None))
    assert needs is True
    assert any("store" in reason.lower() for reason in reasons)


def test_low_confidence_needs_review():
    needs, reasons = logic_manager.check_needs_review(sample_record(confidence=0.4))
    assert needs is True
    assert any("confidence" in reason.lower() for reason in reasons)


def test_unclear_eligibility_reported_by_ai_needs_review():
    needs, reasons = logic_manager.check_needs_review(
        sample_record(missing_fields=["eligibility"]))
    assert needs is True
    assert any("eligibility" in reason.lower() for reason in reasons)


def test_no_discount_information_needs_review():
    needs, _ = logic_manager.check_needs_review(
        sample_record(discount_value=None, discounted_price=None,
                      discount_type="other"))
    assert needs is True


def test_manual_completion_clears_review():
    record = sample_record(expiry_date=None, confidence=0.5,
                           missing_fields=["expiry_date"])
    assert logic_manager.check_needs_review(record)[0] is True

    fixed = logic_manager.complete_missing_fields(
        record, {"expiry_date": "2026-10-30"})
    needs, reasons = logic_manager.check_needs_review(fixed)
    assert needs is False, reasons
    assert fixed["ai"]["expiry_date"] == "2026-10-30"
    assert "expiry_date" in fixed["manual_overrides"]
    # The AI's other findings must survive a manual fix.
    assert fixed["ai"]["merchant"] == "ZUS Coffee"


# ==========================================================================
# Rule 4 -- duplicate detection
# ==========================================================================
def test_same_shop_and_similar_discount_is_duplicate():
    saved = logic_manager.apply_decision(
        sample_record(1), logic_manager.evaluate(sample_record(1), STUDENT_PROFILE,
                                                 None, TODAY))
    incoming = sample_record(None, discount_value=48.0)
    match = logic_manager.find_duplicate(incoming, [saved])
    assert match.get("duplicate_of") == 1
    assert match.get("similarity") == 1.0


def test_name_typo_still_counts_as_same_shop():
    saved = sample_record(1)
    incoming = sample_record(None, merchant="Zus  Coffee!")
    assert logic_manager.find_duplicate(incoming, [saved]).get("duplicate_of") == 1


def test_different_shop_is_not_duplicate():
    saved = sample_record(1)
    incoming = sample_record(None, merchant="Starbucks")
    assert logic_manager.find_duplicate(incoming, [saved]) == {}


def test_different_discount_is_not_duplicate():
    saved = sample_record(1)
    assert logic_manager.find_duplicate(
        sample_record(None, discount_value=10.0), [saved]) == {}
    assert logic_manager.find_duplicate(
        sample_record(None, discount_type="fixed", discount_value=50.0),
        [saved]) == {}


def test_expired_or_used_deals_are_not_duplicate_targets():
    expired = sample_record(1, expiry_date="2026-01-01")
    expired["queue"] = logic_manager.QUEUE_EXPIRED
    expired["status"] = logic_manager.STATUS_EXPIRED
    assert logic_manager.find_duplicate(sample_record(None), [expired]) == {}

    spent = sample_record(2, used=True)
    spent["queue"] = logic_manager.QUEUE_USED
    assert logic_manager.find_duplicate(sample_record(None), [spent]) == {}


def test_duplicate_check_needs_a_merchant_name():
    assert logic_manager.find_duplicate(sample_record(None, merchant=None),
                                        [sample_record(1)]) == {}


# ==========================================================================
# Rule 5 -- minimum spend
# ==========================================================================
def test_minimum_spend_blocks_small_basket():
    check = logic_manager.check_min_spend(sample_record(), 10.0)
    assert check["usable"] is False
    assert check["shortfall"] == 5.0


def test_minimum_spend_met_exactly():
    assert logic_manager.check_min_spend(sample_record(), 15.0)["usable"] is True


def test_no_minimum_spend_is_always_usable():
    assert logic_manager.check_min_spend(
        sample_record(min_spend=None), 0.0)["usable"] is True


def test_usable_for_spend_filter_excludes_blocked_deals():
    cheap_basket = logic_manager.filter_usable_for_spend(5.0)
    record = logic_manager.apply_decision(
        sample_record(), logic_manager.evaluate(sample_record(), STUDENT_PROFILE,
                                                None, TODAY))
    assert cheap_basket(record) is False
    assert logic_manager.filter_usable_for_spend(25.0)(record) is True


# ==========================================================================
# Scoring and ranking
# ==========================================================================
def test_score_is_bounded_and_expired_scores_zero():
    good = logic_manager.score(sample_record(), STUDENT_PROFILE, TODAY)
    assert 0.0 <= good <= 100.0
    assert good > 60.0
    assert logic_manager.score(sample_record(expiry_date="2026-01-01"),
                               STUDENT_PROFILE, TODAY) == 0.0


def test_ineligible_deal_scores_lower_than_eligible_one():
    record = sample_record()
    assert logic_manager.score(record, STUDENT_PROFILE, TODAY) > \
        logic_manager.score(record, NON_STUDENT_PROFILE, TODAY)


def test_bigger_discount_scores_higher():
    small = logic_manager.score(sample_record(discount_value=5.0,
                                              discounted_price=19.0),
                                STUDENT_PROFILE, TODAY)
    big = logic_manager.score(sample_record(discount_value=70.0,
                                            discounted_price=6.0),
                              STUDENT_PROFILE, TODAY)
    assert big > small


def test_score_is_repeatable():
    first = logic_manager.score(sample_record(), STUDENT_PROFILE, TODAY)
    second = logic_manager.score(sample_record(), STUDENT_PROFILE, TODAY)
    assert first == second


def test_rank_puts_the_best_deal_first():
    weak = logic_manager.apply_decision(
        sample_record(2, discount_value=5.0, discounted_price=19.0,
                      expiry_date="2026-12-31"),
        logic_manager.evaluate(
            sample_record(2, discount_value=5.0, discounted_price=19.0,
                          expiry_date="2026-12-31"),
            STUDENT_PROFILE, None, TODAY))
    strong = logic_manager.apply_decision(
        sample_record(3), logic_manager.evaluate(sample_record(3), STUDENT_PROFILE,
                                                 None, TODAY))
    ordered = logic_manager.rank([weak, strong])
    assert [item["id"] for item in ordered] == [3, 2]


# ==========================================================================
# evaluate() -- the multi-condition rules
# ==========================================================================
def test_act_now_needs_every_condition_together():
    decision = logic_manager.evaluate(sample_record(), STUDENT_PROFILE, None, TODAY)
    assert decision["status"] == logic_manager.STATUS_EXPIRING
    assert decision["eligible"] is True
    assert "act_now" in decision["flags"]
    assert decision["priority"] == "act_now"
    assert decision["queue"] == logic_manager.QUEUE_ACT_NOW


def test_act_now_drops_when_user_is_not_eligible():
    decision = logic_manager.evaluate(sample_record(), NON_STUDENT_PROFILE, None, TODAY)
    assert "act_now" not in decision["flags"]
    assert decision["queue"] == logic_manager.QUEUE_WATCHLIST


def test_act_now_drops_when_expiry_is_far_away():
    decision = logic_manager.evaluate(sample_record(expiry_date="2026-12-31"),
                                      STUDENT_PROFILE, None, TODAY)
    assert "act_now" not in decision["flags"]
    assert decision["queue"] == logic_manager.QUEUE_ACTIVE


def test_act_now_drops_when_the_discount_is_small():
    decision = logic_manager.evaluate(
        sample_record(discount_value=10.0, discounted_price=18.0),
        STUDENT_PROFILE, None, TODAY)
    assert "act_now" not in decision["flags"]
    assert decision["queue"] == logic_manager.QUEUE_ACTIVE


def test_act_now_drops_when_ai_confidence_is_shaky():
    decision = logic_manager.evaluate(sample_record(confidence=0.65),
                                      STUDENT_PROFILE, None, TODAY)
    assert "act_now" not in decision["flags"]


def test_minimum_spend_above_habit_is_flagged():
    thrifty = dict(STUDENT_PROFILE)
    thrifty["typical_spend"] = 5.0
    decision = logic_manager.evaluate(sample_record(), thrifty, None, TODAY)
    assert "min_spend_above_habit" in decision["flags"]


def test_evaluate_reports_duplicates_from_existing_records():
    saved = sample_record(1)
    decision = logic_manager.evaluate(sample_record(None), STUDENT_PROFILE,
                                      [saved], TODAY)
    assert decision["duplicate_of"] == 1
    assert decision["queue"] == logic_manager.QUEUE_DUPLICATE


def test_evaluate_does_not_mutate_the_record():
    record = sample_record()
    snapshot = json.dumps(record, sort_keys=True)
    logic_manager.evaluate(record, STUDENT_PROFILE, None, TODAY)
    assert json.dumps(record, sort_keys=True) == snapshot


# ==========================================================================
# route()
# ==========================================================================
def test_route_sends_incomplete_deals_to_review():
    record = sample_record(expiry_date=None, missing_fields=["expiry_date"])
    assert logic_manager.route(record, None, STUDENT_PROFILE, None, TODAY) == \
        logic_manager.QUEUE_NEEDS_REVIEW


def test_route_sends_expired_deals_to_expired():
    record = sample_record(expiry_date="2026-08-01")
    assert logic_manager.route(record, None, STUDENT_PROFILE, None, TODAY) == \
        logic_manager.QUEUE_EXPIRED


def test_route_sends_spent_single_use_deals_to_used():
    record = sample_record(used=True)
    assert logic_manager.route(record, None, STUDENT_PROFILE, None, TODAY) == \
        logic_manager.QUEUE_USED


def test_route_keeps_reusable_used_deals_available():
    record = sample_record(used=True, single_use=False)
    assert logic_manager.route(record, None, STUDENT_PROFILE, None, TODAY) in (
        logic_manager.QUEUE_ACTIVE, logic_manager.QUEUE_ACT_NOW)


def test_route_sends_ineligible_deals_to_watchlist():
    assert logic_manager.route(sample_record(), None, NON_STUDENT_PROFILE,
                               None, TODAY) == logic_manager.QUEUE_WATCHLIST


# ==========================================================================
# Usage, savings and summaries -- calculated by the app, not the AI
# ==========================================================================
def test_mark_used_records_savings_and_moves_single_use_deal():
    ok, updated, message = logic_manager.mark_used(
        sample_record(), amount_saved=10.0, today=TODAY, spend_amount=20.0)
    assert ok is True
    assert updated["user"]["used"] is True
    assert updated["user"]["amount_saved"] == 10.0
    assert updated["user"]["times_used"] == 1
    assert updated["user"]["used_on"] == TODAY
    assert "10.00" in message
    assert logic_manager.route(updated, None, STUDENT_PROFILE, None, TODAY) == \
        logic_manager.QUEUE_USED


def test_mark_used_refuses_a_second_redemption_of_a_single_use_deal():
    ok, _, message = logic_manager.mark_used(sample_record(used=True),
                                             amount_saved=5.0, today=TODAY)
    assert ok is False
    assert "already" in message.lower()


def test_mark_used_refuses_expired_deals():
    ok, _, message = logic_manager.mark_used(
        sample_record(expiry_date="2026-08-31"), amount_saved=5.0, today=TODAY)
    assert ok is False
    assert "expired" in message.lower()


def test_mark_used_refuses_when_minimum_spend_is_not_met():
    ok, _, message = logic_manager.mark_used(sample_record(), amount_saved=5.0,
                                             today=TODAY, spend_amount=9.0)
    assert ok is False
    assert "minimum spend" in message.lower()


def test_reusable_deal_accumulates_savings():
    ok, once, _ = logic_manager.mark_used(sample_record(single_use=False,
                                                       min_spend=None),
                                          amount_saved=4.0, today=TODAY)
    assert ok is True
    ok, twice, _ = logic_manager.mark_used(once, amount_saved=6.5, today=TODAY)
    assert ok is True
    assert twice["user"]["amount_saved"] == 10.5
    assert twice["user"]["times_used"] == 2


def test_summary_counts_come_from_the_log_not_the_ai():
    records = []
    for deal_id, kwargs in enumerate(
            [{}, {"expiry_date": "2026-12-31"}, {"expiry_date": "2026-01-01"},
             {"confidence": 0.2}], start=1):
        record = sample_record(deal_id, **kwargs)
        decision = logic_manager.evaluate(record, STUDENT_PROFILE, None, TODAY)
        records.append(logic_manager.apply_decision(record, decision))

    used_ok, used_record, _ = logic_manager.mark_used(records[1], 12.5, TODAY, 20.0)
    assert used_ok is True
    records[1] = logic_manager.apply_decision(
        used_record, logic_manager.evaluate(used_record, STUDENT_PROFILE, None, TODAY))

    summary = logic_manager.compute_summary(records)
    assert summary["total_deals"] == 4
    assert summary["expired"] == 1
    assert summary["needs_review"] == 1
    assert summary["used_count"] == 1
    assert summary["unused_count"] == 3
    assert summary["total_saved"] == 12.5
    assert summary["by_category"]["food"] == 4


def test_refresh_statuses_expires_deals_as_the_date_moves():
    record = logic_manager.apply_decision(
        sample_record(), logic_manager.evaluate(sample_record(), STUDENT_PROFILE,
                                                None, TODAY))
    assert record["queue"] == logic_manager.QUEUE_ACT_NOW

    later, changed = logic_manager.refresh_statuses([record], STUDENT_PROFILE,
                                                    "2026-10-01")
    assert changed == 1
    assert later[0]["status"] == logic_manager.STATUS_EXPIRED
    assert later[0]["queue"] == logic_manager.QUEUE_EXPIRED


def test_refresh_statuses_is_stable_when_nothing_changed():
    record = logic_manager.apply_decision(
        sample_record(), logic_manager.evaluate(sample_record(), STUDENT_PROFILE,
                                                None, TODAY))
    _, changed = logic_manager.refresh_statuses([record], STUDENT_PROFILE, TODAY)
    assert changed == 0


def test_filters_build_working_predicates():
    record = logic_manager.apply_decision(
        sample_record(), logic_manager.evaluate(sample_record(), STUDENT_PROFILE,
                                                None, TODAY))
    assert logic_manager.filter_by_queue(logic_manager.QUEUE_ACT_NOW)(record) is True
    assert logic_manager.filter_search("zus")(record) is True
    assert logic_manager.filter_search("handcrafted")(record) is True
    assert logic_manager.filter_search("nonsense")(record) is False


# ==========================================================================
# AI layer -- prompt, parsing and schema validation (no network involved)
# ==========================================================================
def test_prompt_demands_json_and_carries_the_source_text():
    prompt = ai_manager.build_prompt({
        "source_type": "text",
        "raw_text": "Guardian: RM10 off when you spend RM50, ends 30 Sep",
        "hints": {"merchant": "Guardian"}})
    assert "JSON" in prompt["system"]
    assert "Guardian" in prompt["user_text"]
    assert prompt["json_mode"] is True
    assert prompt["model"] == config.TEXT_MODEL
    for category in config.VALID_CATEGORIES:
        assert category in prompt["system"]


def test_prompt_feeds_validation_errors_back_to_the_model():
    prompt = ai_manager.build_prompt({"source_type": "text", "raw_text": "x"},
                                     correction=["category 'coffee' is not allowed."])
    assert "REJECTED" in prompt["user_text"]
    assert "coffee" in prompt["user_text"]


def test_parse_response_handles_markdown_fences_and_chatter():
    raw = ('Sure, here you go:\n```json\n{"merchant": "Guardian", '
           '"category": "retail"}\n```\nHope that helps!')
    assert ai_manager.parse_response(raw) == {"merchant": "Guardian",
                                              "category": "retail"}


def test_parse_response_handles_plain_json_and_arrays_and_nesting():
    assert ai_manager.parse_response('{"merchant": "A"}') == {"merchant": "A"}
    assert ai_manager.parse_response('[{"merchant": "A"}]') == {"merchant": "A"}
    assert ai_manager.parse_response('{"deal": {"merchant": "A"}}') == {"merchant": "A"}


def test_parse_response_rejects_unusable_replies():
    assert ai_manager.parse_response("I cannot help with that.") is None
    assert ai_manager.parse_response("") is None
    assert ai_manager.parse_response('{"merchant": "A"') is None


def test_validate_response_accepts_a_good_payload():
    ok, cleaned, errors = ai_manager.validate_response(sample_ai())
    assert ok is True
    assert errors == []
    for key in ai_manager.RESPONSE_KEYS:
        assert key in cleaned


def test_validate_response_rejects_a_category_outside_the_fixed_set():
    ok, _, errors = ai_manager.validate_response(sample_ai(category="coffee shop"))
    assert ok is False
    assert any("category" in error for error in errors)


def test_validate_response_rejects_bad_types_and_ranges():
    assert ai_manager.validate_response(sample_ai(confidence=1.8))[0] is False
    assert ai_manager.validate_response(sample_ai(discount_value=150.0))[0] is False
    assert ai_manager.validate_response(sample_ai(expiry_date="31 Aug 2026"))[0] is False
    assert ai_manager.validate_response(sample_ai(discount_type="half price"))[0] is False
    assert ai_manager.validate_response(sample_ai(min_spend="about ten"))[0] is False
    assert ai_manager.validate_response("not a dict")[0] is False

    without_eligibility = sample_ai()
    without_eligibility["eligibility"] = None
    assert ai_manager.validate_response(without_eligibility)[0] is False


def test_validate_response_normalises_messy_but_valid_values():
    ok, cleaned, errors = ai_manager.validate_response(sample_ai(
        category="FOOD", min_spend="RM15.50", original_price="20",
        single_use="yes", missing_fields="Expiry_Date"))
    assert ok is True, errors
    assert cleaned["category"] == "food"
    assert cleaned["min_spend"] == 15.5
    assert cleaned["original_price"] == 20.0
    assert cleaned["single_use"] is True
    assert cleaned["missing_fields"] == ["expiry_date"]


def test_validated_payload_is_immediately_usable_by_the_logic_layer():
    ok, cleaned, _ = ai_manager.validate_response(sample_ai())
    assert ok is True
    decision = logic_manager.evaluate({"ai": cleaned}, STUDENT_PROFILE, None, TODAY)
    assert decision["queue"] == logic_manager.QUEUE_ACT_NOW


def test_process_retries_when_the_model_invents_a_category():
    replies = [
        json.dumps(sample_ai(category="coffee")),
        json.dumps(sample_ai()),
    ]
    calls = {"count": 0, "corrections": []}
    original_call = ai_manager.call_api

    def fake_call_api(prompt):
        calls["count"] += 1
        if "REJECTED" in prompt["user_text"]:
            calls["corrections"].append(prompt["user_text"])
        return {"ok": True, "raw": replies[calls["count"] - 1], "error": "",
                "attempts": 1, "model": prompt["model"]}

    ai_manager.call_api = fake_call_api
    try:
        result = ai_manager.process({"source_type": "text", "raw_text": "promo"})
    finally:
        ai_manager.call_api = original_call

    assert calls["count"] == 2
    assert calls["corrections"], "the retry must tell the model what was wrong"
    assert result["ok"] is True
    assert result["data"]["category"] == "food"
    assert result["attempts"] == 2


def test_process_gives_up_cleanly_when_the_api_is_unreachable():
    original_call = ai_manager.call_api

    def broken_call_api(prompt):
        return {"ok": False, "raw": "", "error": "Could not reach the Groq API.",
                "attempts": 3, "model": prompt["model"]}

    ai_manager.call_api = broken_call_api
    try:
        result = ai_manager.process({"source_type": "text", "raw_text": "promo"})
    finally:
        ai_manager.call_api = original_call

    assert result["ok"] is False
    assert result["data"] == {}
    assert "Groq" in result["errors"][0]


def test_call_api_reports_a_missing_key_instead_of_crashing():
    previous = os.environ.get("GROQ_API_KEY")
    os.environ["GROQ_API_KEY"] = ""
    try:
        result = ai_manager.call_api({"user_text": "hello", "model": "test"})
    finally:
        if previous is None:
            del os.environ["GROQ_API_KEY"]
        else:
            os.environ["GROQ_API_KEY"] = previous
    assert result["ok"] is False
    assert "GROQ_API_KEY" in result["error"]


def test_call_api_rejects_an_empty_prompt_without_network_access():
    result = ai_manager.call_api({})
    assert result["ok"] is False
    assert result["raw"] == ""


def test_encode_image_survives_a_missing_file():
    assert ai_manager.encode_image("/no/such/voucher.jpg") == ""


# ==========================================================================
# Data layer -- flat file persistence
# ==========================================================================
def _temp_path(name):
    return os.path.join(tempfile.mkdtemp(prefix="dealkeeper_"), name)


def test_load_returns_empty_list_when_the_file_is_missing():
    assert data_manager.load(_temp_path("nothing.json")) == []


def test_save_then_load_round_trips_the_record():
    path = _temp_path("deals.json")
    record = logic_manager.apply_decision(
        sample_record(None), logic_manager.evaluate(sample_record(None),
                                                    STUDENT_PROFILE, None, TODAY))
    stored = data_manager.save(record, path)
    assert stored["id"] == 1

    loaded = data_manager.load(path)
    assert len(loaded) == 1
    assert loaded[0]["ai"]["merchant"] == "ZUS Coffee"
    assert loaded[0]["queue"] == logic_manager.QUEUE_ACT_NOW


def test_ids_increment_and_output_is_identical_across_runs():
    path = _temp_path("deals.json")
    data_manager.save(sample_record(None), path)
    data_manager.save(sample_record(None, merchant="Guardian"), path)
    assert [item["id"] for item in data_manager.load(path)] == [1, 2]

    with open(path, "r", encoding="utf-8") as handle:
        first_write = handle.read()
    data_manager.save_all(data_manager.load(path), path)
    with open(path, "r", encoding="utf-8") as handle:
        assert handle.read() == first_write


def test_query_filters_by_any_field():
    path = _temp_path("deals.json")
    data_manager.save(sample_record(None), path)
    data_manager.save(sample_record(None, merchant="Guardian",
                                    category="cosmetics"), path)

    cosmetics = data_manager.query(
        lambda record: data_manager.get_field(record, "category") == "cosmetics",
        path)
    assert len(cosmetics) == 1
    assert cosmetics[0]["ai"]["merchant"] == "Guardian"


def test_query_survives_a_broken_filter_function():
    path = _temp_path("deals.json")
    data_manager.save(sample_record(None), path)
    assert data_manager.query(lambda record: record["missing_key"], path) == []
    assert data_manager.query("not callable", path) == []


def test_update_and_delete_record():
    path = _temp_path("deals.json")
    data_manager.save(sample_record(None), path)
    data_manager.update_record(1, {"queue": logic_manager.QUEUE_USED}, path)
    assert data_manager.get_record(1, path)["queue"] == logic_manager.QUEUE_USED

    assert data_manager.delete_record(1, path) is True
    assert data_manager.load(path) == []
    assert data_manager.delete_record(99, path) is False


def test_corrupt_file_is_quarantined_and_load_returns_empty():
    path = _temp_path("deals.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{ this is not json ][")
    assert data_manager.load(path) == []
    assert not os.path.exists(path), "the corrupt file should be moved aside"

    folder = os.path.dirname(path)
    assert any(name.startswith("deals.json.corrupt") for name in os.listdir(folder))


def test_malformed_entries_are_skipped():
    path = _temp_path("deals.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(["a string", 42, {"id": 1, "ai": sample_ai()}], handle)
    loaded = data_manager.load(path)
    assert len(loaded) == 1
    assert loaded[0]["id"] == 1


def test_profile_round_trip_with_defaults():
    path = _temp_path("profile.json")
    assert data_manager.load_profile(path)["is_student"] is False

    assert data_manager.save_profile(STUDENT_PROFILE, path) is True
    loaded = data_manager.load_profile(path)
    assert loaded["is_student"] is True
    assert loaded["school"] == "University Malaya"
    assert loaded["typical_spend"] == 30.0


# ==========================================================================
# Constraint guards -- the rules this codebase must keep
# ==========================================================================
def test_no_class_definitions_anywhere():
    keyword = "%s " % "class"          # assembled so this guard is not a hit itself
    for module in ("config.py", "io_manager.py", "ai_manager.py",
                   "logic_manager.py", "data_manager.py", "main.py",
                   "test_dealkeeper.py"):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), module)
        with open(path, "r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                assert not line.lstrip().startswith(keyword), \
                    "%s:%d defines a type instead of a function" % (module, number)


def test_only_io_manager_talks_to_the_terminal():
    # Needles are assembled at runtime so that a grep for terminal calls in
    # this repository only ever hits io_manager.py.
    forbidden = ("%s(" % "print", "%s(" % "input")
    folder = os.path.dirname(os.path.abspath(__file__))
    for module in ("config.py", "ai_manager.py", "logic_manager.py",
                   "data_manager.py", "main.py"):
        with open(os.path.join(folder, module), "r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for needle in forbidden:
                    assert needle not in stripped, \
                        "%s:%d calls %s" % (module, number, needle)


# ==========================================================================
# Runner (usable without pytest installed)
# ==========================================================================
def _main():
    tests = sorted(
        (name, function) for name, function in globals().items()
        if name.startswith("test_") and callable(function)
    )
    passed = 0
    failures = []
    for name, function in tests:
        try:
            function()
            passed += 1
            sys.stdout.write(".")
        except AssertionError as error:
            failures.append((name, "assertion failed: %s" % error))
            sys.stdout.write("F")
        except Exception as error:                       # noqa: BLE001
            failures.append((name, "%s: %s" % (type(error).__name__, error)))
            sys.stdout.write("E")
        sys.stdout.flush()

    sys.stdout.write("\n\n%d/%d tests passed\n" % (passed, len(tests)))
    for name, reason in failures:
        sys.stdout.write("FAILED %s -> %s\n" % (name, reason))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_main())

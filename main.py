"""DealKeeper -- entry point and pipeline.

The one job of this file is to move data along the pipeline:

    io_manager  ->  ai_manager  ->  logic_manager  ->  data_manager
    (collect)       (extract)       (decide)           (remember)

It writes nothing to the terminal, reads nothing from the keyboard, calls no
API and holds no business rules -- it only calls the managers in the right
order.
"""

import logging
from datetime import date

import ai_manager
import config
import data_manager
import io_manager
import logic_manager

LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------
def startup():
    """Load the store, re-check every expiry against today, load the profile."""
    config.setup_logging()
    config.ensure_data_dir()
    LOGGER.info("DealKeeper starting up.")

    profile = data_manager.load_profile()
    records = data_manager.load()

    # Business rule: expiry is measured on every run, not when the deal was saved.
    refreshed, changed = logic_manager.refresh_statuses(records, profile)
    if changed:
        data_manager.save_all(refreshed)
        LOGGER.info("Refreshed status of %d deals on startup.", changed)

    if not data_manager.profile_exists():
        io_manager.display_info(
            "First run -- let's set up your profile so eligibility works.")
        profile = io_manager.prompt_profile(profile)
        data_manager.save_profile(profile)

    return profile, refreshed


# --------------------------------------------------------------------------
# Pipeline: add a deal
# --------------------------------------------------------------------------
def add_deal(profile):
    """The full pipeline for one new deal.

    io_manager collects it, ai_manager turns it into structured data,
    logic_manager decides what it means, data_manager stores the outcome.
    """
    raw_input_record = io_manager.prompt_new_deal()

    # ---- AI layer: nothing continues without a valid structured payload ----
    io_manager.display_working("Sending the deal to the AI for extraction...")
    ai_result = ai_manager.process(raw_input_record)
    if not ai_result.get("ok"):
        io_manager.display_ai_failure(ai_result.get("errors"))
        return

    io_manager.display_success(
        "AI extracted the deal (model: %s, attempt %d)."
        % (ai_result.get("model"), ai_result.get("attempts", 1)))

    record = {
        "created_at": date.today().isoformat(),
        "source_type": raw_input_record.get("source_type"),
        "raw_input": (raw_input_record.get("raw_text") or "")[:2000],
        "image_path": raw_input_record.get("image_path") or "",
        "ai": ai_result["data"],
        "ai_model": ai_result.get("model"),
        "user": {"used": False, "used_on": None, "times_used": 0,
                 "amount_saved": 0.0},
        "manual_overrides": [],
    }

    # ---- Logic layer: business rules, including the duplicate check --------
    existing = data_manager.load()
    decision = logic_manager.evaluate(record, profile, existing)

    # Rule: incomplete extraction goes to review, and the user may complete it.
    if decision["needs_review"]:
        fixes = io_manager.prompt_missing_fields(record, decision["review_reasons"])
        if fixes:
            record = logic_manager.complete_missing_fields(record, fixes)
            decision = logic_manager.evaluate(record, profile, existing)
            if decision["needs_review"]:
                io_manager.display_warning(
                    "Still incomplete, so it stays in the needs-review list.")
            else:
                io_manager.display_success("Details completed -- deal is now active.")

    # Rule: a near-identical deal is flagged, never silently duplicated.
    if decision["duplicate_of"] is not None:
        io_manager.display_warning(
            "Looks like deal #%s that you already saved." % decision["duplicate_of"])
        io_manager.display_info(decision["duplicate_reason"])
        if not io_manager.confirm("Save it anyway as a separate deal?", default=False):
            io_manager.display_info("Discarded. Nothing was saved.")
            return

    record = logic_manager.apply_decision(record, decision)

    # ---- Data layer -------------------------------------------------------
    stored = data_manager.save(record)
    if not stored:
        io_manager.display_error("The deal could not be saved to disk. See the log.")
        return

    io_manager.display_result(decision)
    io_manager.display_record(stored)


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------
def show_ready_deals():
    """Deals the user can actually use right now, best first."""
    usable = data_manager.query(
        lambda record: record.get("queue") in (logic_manager.QUEUE_ACTIVE,
                                               logic_manager.QUEUE_ACT_NOW))
    watchlist = data_manager.query(
        logic_manager.filter_by_queue(logic_manager.QUEUE_WATCHLIST))

    io_manager.display_list(logic_manager.rank(usable), "Deals ready to use",
                            "No active deals yet -- add one from the menu.")
    if watchlist:
        io_manager.display_list(
            logic_manager.rank(watchlist), "Saved but not eligible for you",
            "None.")


def show_expiring_soon():
    deals = data_manager.query(
        lambda record: record.get("status") == logic_manager.STATUS_EXPIRING
        and record.get("queue") != logic_manager.QUEUE_USED)
    io_manager.display_list(logic_manager.rank(deals),
                            "Expiring within %d days" % config.EXPIRING_SOON_DAYS,
                            "Nothing expires in the next week.")


def show_queue(queue_name, title, empty_message):
    deals = data_manager.query(logic_manager.filter_by_queue(queue_name))
    io_manager.display_list(logic_manager.rank(deals), title, empty_message)
    return deals


def show_needs_review(profile):
    """List incomplete deals and offer to complete one of them."""
    deals = show_queue(logic_manager.QUEUE_NEEDS_REVIEW, "Needs review",
                       "Nothing needs reviewing. Nice.")
    if not deals:
        return
    if not io_manager.confirm("Complete one of these now?", default=False):
        return

    deal_id = io_manager.prompt_deal_id(deals, "review")
    if deal_id is None:
        return
    record = data_manager.get_record(deal_id)
    io_manager.display_record(record)

    fixes = io_manager.prompt_missing_fields(record, record.get("review_reasons"))
    if not fixes:
        return
    record = logic_manager.complete_missing_fields(record, fixes)
    decision = logic_manager.evaluate(record, profile, None)
    record = logic_manager.apply_decision(record, decision)
    stored = data_manager.save(record)
    io_manager.display_result(decision)
    io_manager.display_record(stored)


def show_duplicates(profile):
    """List flagged duplicates and let the user keep or delete one."""
    deals = show_queue(logic_manager.QUEUE_DUPLICATE, "Possible duplicates",
                       "No duplicates flagged.")
    if not deals:
        return
    if not io_manager.confirm("Resolve one of these now?", default=False):
        return

    deal_id = io_manager.prompt_deal_id(deals, "resolve")
    if deal_id is None:
        return
    record = data_manager.get_record(deal_id)
    io_manager.display_record(record)

    if io_manager.confirm("Delete this deal as a duplicate?", default=False):
        if data_manager.delete_record(deal_id):
            io_manager.display_success("Deleted deal #%s." % deal_id)
        else:
            io_manager.display_error("Could not delete deal #%s." % deal_id)
        return

    record["duplicate_of"] = None
    record["duplicate_reason"] = ""
    decision = logic_manager.evaluate(record, profile, None)
    record = logic_manager.apply_decision(record, decision)
    data_manager.save(record)
    io_manager.display_success("Kept as its own deal.")
    io_manager.display_result(decision)


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
def mark_deal_used(profile):
    """Log a redemption: usage counts and savings are calculated by the app."""
    candidates = data_manager.query(
        lambda record: record.get("queue") in (
            logic_manager.QUEUE_ACTIVE, logic_manager.QUEUE_ACT_NOW,
            logic_manager.QUEUE_WATCHLIST))
    deal_id = io_manager.prompt_deal_id(logic_manager.rank(candidates), "mark as used")
    if deal_id is None:
        return

    record = data_manager.get_record(deal_id)
    details = io_manager.prompt_usage_details(record)
    ok, updated, message = logic_manager.mark_used(
        record,
        amount_saved=details["amount_saved"],
        spend_amount=details["spend_amount"])
    if not ok:
        io_manager.display_warning(message)
        return

    decision = logic_manager.evaluate(updated, profile, None)
    updated = logic_manager.apply_decision(updated, decision)
    stored = data_manager.save(updated)
    io_manager.display_success(message)
    if decision["queue"] == logic_manager.QUEUE_USED:
        io_manager.display_info(
            "Single-use deal moved to your used list; it won't be recommended again.")
    io_manager.display_record(stored)
    io_manager.display_summary(logic_manager.compute_summary(data_manager.load()))


def check_basket():
    """Which saved deals can this basket amount actually redeem?"""
    amount = io_manager.prompt_spend_amount()
    active = data_manager.query(
        lambda record: record.get("queue") in (logic_manager.QUEUE_ACTIVE,
                                               logic_manager.QUEUE_ACT_NOW))
    if not active:
        io_manager.display_info("You have no active deals to check.")
        return

    usable = data_manager.query(logic_manager.filter_usable_for_spend(amount),
                                records=active)
    io_manager.display_list(logic_manager.rank(usable),
                            "Deals you can use for %s"
                            % io_manager.format_money(amount),
                            "None of your deals can be used at that amount.")
    for record in logic_manager.rank(active):
        io_manager.display_spend_check(
            record, logic_manager.check_min_spend(record, amount))


def search_deals():
    term = io_manager.prompt_search_term()
    matches = data_manager.query(logic_manager.filter_search(term))
    io_manager.display_list(logic_manager.rank(matches),
                            "Search results for '%s'" % term,
                            "Nothing matched that search.")


def edit_profile(profile):
    """Show the profile, optionally update it, then re-evaluate eligibility."""
    io_manager.display_profile(profile)
    if not io_manager.confirm("Update your profile?", default=False):
        return profile

    updated = io_manager.prompt_profile(profile)
    data_manager.save_profile(updated)

    # Eligibility depends on the profile, so every deal is re-judged.
    refreshed, changed = logic_manager.refresh_statuses(data_manager.load(), updated)
    if changed:
        data_manager.save_all(refreshed)
        io_manager.display_info("Re-checked eligibility on %d deals." % changed)
    return updated


# --------------------------------------------------------------------------
# Menu loop
# --------------------------------------------------------------------------
def run():
    profile, records = startup()
    io_manager.show_welcome(profile, logic_manager.compute_summary(records))

    while True:
        choice = io_manager.show_menu()

        if choice == "1":
            add_deal(profile)
        elif choice == "2":
            show_ready_deals()
        elif choice == "3":
            show_expiring_soon()
        elif choice == "4":
            show_needs_review(profile)
        elif choice == "5":
            show_duplicates(profile)
        elif choice == "6":
            show_queue(logic_manager.QUEUE_USED, "Used deals",
                       "You have not marked any deal as used yet.")
        elif choice == "7":
            show_queue(logic_manager.QUEUE_EXPIRED, "Expired deals",
                       "No expired deals.")
        elif choice == "8":
            mark_deal_used(profile)
        elif choice == "9":
            check_basket()
        elif choice == "10":
            search_deals()
        elif choice == "11":
            io_manager.display_summary(
                logic_manager.compute_summary(data_manager.load()))
        elif choice == "12":
            profile = edit_profile(profile)
        elif choice == "0":
            io_manager.display_goodbye(
                logic_manager.compute_summary(data_manager.load()))
            LOGGER.info("DealKeeper shutting down.")
            return


if __name__ == "__main__":
    run()

"""Data layer -- DealKeeper's memory.

Every record lives in a flat JSON file. This module knows how to write, read
and filter those records and nothing else: no user interaction, no API calls
and no business rules.

Failure policy: never crash. A missing file returns an empty list, a corrupt
file is moved aside and reported to the log, and the caller keeps going.
"""

import json
import logging
import os
import shutil
import time

import config

LOGGER = logging.getLogger(__name__)

# Default profile used the first time the app runs.
DEFAULT_PROFILE = {
    "name": "",
    "is_student": False,
    "school": "",
    "age": 0,
    "is_senior": False,
    "typical_spend": 0.0,
}


# --------------------------------------------------------------------------
# Low-level file helpers
# --------------------------------------------------------------------------
def _read_json(path, fallback):
    """Read JSON from path. Return fallback on any problem, never raise."""
    if not os.path.exists(path):
        LOGGER.info("File %s does not exist yet; starting empty.", path)
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read().strip()
    except OSError as error:
        LOGGER.error("Could not read %s: %s", path, error)
        return fallback

    if not content:
        LOGGER.warning("File %s is empty; starting empty.", path)
        return fallback

    try:
        return json.loads(content)
    except (ValueError, TypeError) as error:
        LOGGER.error("File %s is corrupt (%s); quarantining it.", path, error)
        quarantine_file(path)
        return fallback


def _write_json(path, payload):
    """Write JSON atomically (temp file + replace). Return True on success."""
    if not config.ensure_data_dir():
        return False
    temp_path = path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, path)
        return True
    except (OSError, TypeError, ValueError) as error:
        LOGGER.error("Could not write %s: %s", path, error)
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return False


def quarantine_file(path):
    """Move an unreadable file aside so the app can start clean next run."""
    backup = "%s.corrupt.%d" % (path, int(time.time()))
    try:
        shutil.move(path, backup)
        LOGGER.warning("Moved corrupt file %s to %s", path, backup)
        return backup
    except OSError as error:
        LOGGER.error("Could not quarantine %s: %s", path, error)
        return ""


# --------------------------------------------------------------------------
# Deal records
# --------------------------------------------------------------------------
def load(path=None):
    """Return every saved deal, sorted by id. Empty list if nothing is stored.

    Called on startup. Any file problem is logged and swallowed.
    """
    target = path or config.DEALS_FILE
    data = _read_json(target, [])

    if isinstance(data, dict):
        data = data.get("deals", [])
    if not isinstance(data, list):
        LOGGER.error("Unexpected structure in %s (%s); ignoring.", target, type(data))
        return []

    records = [item for item in data if isinstance(item, dict)]
    if len(records) != len(data):
        LOGGER.warning("Dropped %d malformed entries from %s",
                       len(data) - len(records), target)
    return sort_records(records)


def save_all(records, path=None):
    """Overwrite the store with records (sorted by id for stable output)."""
    target = path or config.DEALS_FILE
    return _write_json(target, sort_records(records))


def save(record, path=None):
    """Append one evaluated record to the store and return the stored copy.

    An id is assigned when the record does not have one yet. Runs are stable:
    ids are sequential and the file is always written sorted by id.
    """
    if not isinstance(record, dict):
        LOGGER.error("save() expects a dict, got %s", type(record))
        return {}

    target = path or config.DEALS_FILE
    records = load(target)
    stored = dict(record)

    if not stored.get("id"):
        stored["id"] = next_id(records)

    replaced = False
    for index, existing in enumerate(records):
        if existing.get("id") == stored["id"]:
            records[index] = stored
            replaced = True
            break
    if not replaced:
        records.append(stored)

    if not save_all(records, target):
        LOGGER.error("Deal %s could not be persisted.", stored.get("id"))
        return {}

    LOGGER.info("Saved deal id=%s merchant=%s queue=%s", stored.get("id"),
                get_field(stored, "merchant"), stored.get("queue"))
    return stored


def query(filter_fn, path=None, records=None):
    """Return records for which filter_fn(record) is truthy.

    filter_fn is any callable taking a record dict. A filter that raises is
    logged and treated as "no match" so a bad predicate cannot kill the app.
    """
    pool = records if records is not None else load(path)
    if not callable(filter_fn):
        LOGGER.error("query() needs a callable filter, got %s", type(filter_fn))
        return []

    matches = []
    for record in pool:
        try:
            if filter_fn(record):
                matches.append(record)
        except Exception as error:                      # noqa: BLE001
            LOGGER.error("Filter failed on deal %s: %s", record.get("id"), error)
    return sort_records(matches)


def get_record(deal_id, path=None, records=None):
    """Return a single record by id, or an empty dict when not found."""
    matches = query(lambda item: item.get("id") == deal_id, path, records)
    return matches[0] if matches else {}


def update_record(deal_id, changes, path=None):
    """Shallow-merge changes into the stored record and persist it."""
    record = get_record(deal_id, path)
    if not record:
        LOGGER.warning("update_record: deal %s not found", deal_id)
        return {}
    merged = dict(record)
    merged.update(changes or {})
    return save(merged, path)


def delete_record(deal_id, path=None):
    """Remove a record by id. Returns True when something was removed."""
    target = path or config.DEALS_FILE
    records = load(target)
    remaining = [item for item in records if item.get("id") != deal_id]
    if len(remaining) == len(records):
        return False
    return save_all(remaining, target)


def next_id(records):
    """Return the next sequential integer id for a list of records."""
    highest = 0
    for record in records or []:
        value = record.get("id")
        if isinstance(value, int) and value > highest:
            highest = value
    return highest + 1


def sort_records(records):
    """Deterministic ordering: by id, then merchant name."""
    def key(record):
        deal_id = record.get("id")
        deal_id = deal_id if isinstance(deal_id, int) else 0
        return (deal_id, str(get_field(record, "merchant")))
    return sorted(list(records or []), key=key)


def get_field(record, key, default=None):
    """Read an AI-extracted field from a record, wherever it is nested.

    Records store the AI payload under record["ai"]; this helper lets callers
    (and filter functions) ask for "merchant" without knowing the layout.
    """
    if not isinstance(record, dict):
        return default
    ai_block = record.get("ai")
    if isinstance(ai_block, dict) and key in ai_block:
        return ai_block.get(key, default)
    if key in record:
        return record.get(key, default)
    user_block = record.get("user")
    if isinstance(user_block, dict) and key in user_block:
        return user_block.get(key, default)
    return default


# --------------------------------------------------------------------------
# User profile
# --------------------------------------------------------------------------
def load_profile(path=None):
    """Return the saved user profile merged over the defaults."""
    target = path or config.PROFILE_FILE
    data = _read_json(target, {})
    profile = dict(DEFAULT_PROFILE)
    if isinstance(data, dict):
        for key, value in data.items():
            profile[key] = value
    else:
        LOGGER.error("Profile file %s had unexpected structure.", target)
    return profile


def save_profile(profile, path=None):
    """Persist the user profile. Returns True on success."""
    target = path or config.PROFILE_FILE
    merged = dict(DEFAULT_PROFILE)
    if isinstance(profile, dict):
        merged.update(profile)
    return _write_json(target, merged)


def profile_exists(path=None):
    """True when a profile has already been saved."""
    return os.path.exists(path or config.PROFILE_FILE)

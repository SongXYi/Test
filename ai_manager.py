"""AI layer -- the core engine of DealKeeper (Groq API).

Nothing enters the system without passing through this module. A forwarded
promo message, or a handful of details typed in by hand, is meaningless to the
app until the model has turned it into the structured record that logic_manager
reasons about and data_manager stores. Delete this file and the product has no
purpose left.

Responsibilities here are strictly: build the prompt, talk to the API, parse
the reply and validate its schema. There are no business rules in this file --
"is this deal eligible / expiring / a duplicate" all belong to logic_manager.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import date, datetime

import config

LOGGER = logging.getLogger(__name__)

# The exact schema the model is asked to return and that validate_response
# enforces before a single field is allowed into the rest of the app.
RESPONSE_KEYS = (
    "merchant",
    "category",
    "discount_type",
    "discount_value",
    "original_price",
    "discounted_price",
    "description",
    "expiry_date",
    "eligibility",
    "min_spend",
    "single_use",
    "terms",
    "confidence",
    "missing_fields",
)

ELIGIBILITY_FLAGS = (
    "students_only",
    "seniors_only",
    "new_customers_only",
    "members_only",
)

SYSTEM_PROMPT = """You are the extraction engine of a personal deal and voucher tracker.
You convert one messy promotion (pasted advertisement text, a forwarded chat or email
message, or a few details typed in by the user) into exactly ONE JSON object.

Return JSON only. No markdown fences, no commentary, no trailing text.

Use this exact schema and these exact key names:
{
  "merchant": string|null,            // store or brand name as printed, e.g. "ZUS Coffee"
  "category": string,                 // EXACTLY one of: %(categories)s
  "discount_type": string,            // EXACTLY one of: %(discount_types)s
  "discount_value": number|null,      // percent -> the percent number (50 for "50%% off");
                                      // fixed -> currency amount off (10 for "%(currency)s10 off");
                                      // bogo -> percent off the qualifying item
                                      //         ("50%% off 2nd item" -> 50, "buy 1 free 1" -> 100);
                                      // freebie/other -> null if there is no number
  "original_price": number|null,      // plain number, no currency symbol
  "discounted_price": number|null,    // plain number, no currency symbol
  "description": string,              // one line, max 120 chars, plain summary of the offer
  "expiry_date": string|null,         // "YYYY-MM-DD" only
  "eligibility": {
    "students_only": boolean,
    "seniors_only": boolean,
    "new_customers_only": boolean,
    "members_only": boolean,
    "min_age": number|null,
    "max_age": number|null,
    "school": string|null,            // named school/university if the offer is limited to one
    "raw": string                     // the eligibility wording you saw, "" if none
  },
  "min_spend": number|null,           // minimum spend required to use the deal
  "single_use": boolean,              // true for one-time codes/vouchers, false if reusable
  "terms": [string],                  // other short conditions, max 5 items
  "confidence": number,               // 0.0-1.0, your confidence in merchant + category
                                      // + discount + expiry_date together
  "missing_fields": [string]          // subset of ["merchant","category","discount",
                                      // "expiry_date","eligibility"] you could NOT determine
}

Hard rules:
1. Never invent data. If a value is not stated and cannot be read, use null (or false for
   booleans, "" for eligibility.raw) and name it in "missing_fields".
2. "category" must be lowercase and must be one of the listed values. If nothing fits, use
   "other" -- never a word outside the list.
3. Dates: today is %(today)s. Resolve relative wording against it ("valid till this Sunday",
   "ends in 3 days"). If only a month is given, use the last day of that month. If the text
   shows a date with an ambiguous format, prefer day-first (31/08/2026 -> 2026-08-31). If no
   expiry is stated at all, use null and add "expiry_date" to missing_fields.
4. If eligibility wording is vague ("selected customers", "T&C apply" with no detail), keep the
   flags false, copy the wording into eligibility.raw, add "eligibility" to missing_fields and
   lower your confidence.
5. Strip currency symbols and thousands separators from all numbers.
6. If the input is not a deal/promotion at all, return the schema with nulls, category "other",
   confidence 0.0 and every required field listed in missing_fields.
7. Be honest with "confidence": guessing between two dates, or inferring the merchant from
   a URL or a hashtag, means a value below 0.6.
""" % {
    "categories": ", ".join(config.VALID_CATEGORIES),
    "discount_types": ", ".join(config.VALID_DISCOUNT_TYPES),
    "currency": config.CURRENCY,
    "today": date.today().isoformat(),
}


# --------------------------------------------------------------------------
# 1. Prompt construction
# --------------------------------------------------------------------------
def build_prompt(record, correction=None):
    """Turn an input record into a ready-to-send prompt payload.

    record comes from io_manager and may contain:
        source_type : "text" (pasted promotion) | "manual" (typed details)
        raw_text    : pasted promo text, or the typed details as a text block
        hints       : dict of fields the user already typed by hand

    correction is an optional list of validation errors from a previous
    attempt; it is appended so the model can fix its own output.

    Returns a dict: {"model", "system", "user_text", "json_mode"}
    """
    record = record if isinstance(record, dict) else {}
    source_type = str(record.get("source_type") or "text").lower()
    raw_text = str(record.get("raw_text") or "").strip()
    hints = record.get("hints") if isinstance(record.get("hints"), dict) else {}

    sections = []
    if source_type == "manual":
        sections.append(
            "TASK: normalise the deal details the user typed in below into the schema. "
            "Read their wording carefully -- the discount type, eligibility and terms "
            "are often hidden in the description they wrote."
        )
    else:
        sections.append("TASK: extract the deal from the promotion text below.")

    if raw_text:
        sections.append("PROMOTION INPUT:\n\"\"\"\n%s\n\"\"\"" % raw_text[:6000])
    else:
        sections.append("PROMOTION INPUT: (empty)")

    known = _format_hints(hints)
    if known:
        sections.append(
            "FIELDS THE USER ALREADY CONFIRMED (trust these over your own reading, "
            "copy them into your answer):\n%s" % known
        )

    sections.append(
        "Respond now with the single JSON object described in the system message."
    )

    if correction:
        sections.append(
            "YOUR PREVIOUS ANSWER WAS REJECTED. Fix exactly these problems and resend "
            "the full JSON object:\n- %s" % "\n- ".join(str(item) for item in correction)
        )

    prompt = {
        "model": config.TEXT_MODEL,
        "system": SYSTEM_PROMPT,
        "user_text": "\n\n".join(sections),
        "json_mode": True,
    }
    LOGGER.info("Built prompt: source=%s model=%s chars=%d",
                source_type, prompt["model"], len(prompt["user_text"]))
    return prompt


def _format_hints(hints):
    """Render user-supplied hints as compact 'key: value' lines."""
    lines = []
    for key in sorted(hints or {}):
        value = hints.get(key)
        if value in (None, "", [], {}):
            continue
        lines.append("- %s: %s" % (key, value))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 2. API call
# --------------------------------------------------------------------------
def call_api(prompt):
    """Send a prompt to the Groq chat-completions API.

    Returns {"ok", "raw", "error", "attempts", "model"}. Connection errors,
    timeouts, rate limits and HTTP errors are logged and reported -- they never
    raise out of this function.
    """
    result = {"ok": False, "raw": "", "error": "", "attempts": 0,
              "model": (prompt or {}).get("model", "")}

    if not isinstance(prompt, dict) or not prompt.get("user_text"):
        result["error"] = "Empty prompt; nothing sent to the AI."
        LOGGER.error(result["error"])
        return result

    api_key = config.get_api_key()
    if not api_key:
        result["error"] = ("No GROQ_API_KEY found. Put it in a .env file next to "
                           "main.py or export it before running.")
        LOGGER.error("Missing GROQ_API_KEY.")
        return result

    json_mode = bool(prompt.get("json_mode", True))
    delay = 1.0

    for attempt in range(1, config.API_MAX_ATTEMPTS + 1):
        result["attempts"] = attempt
        body = json.dumps(_build_payload(prompt, json_mode)).encode("utf-8")
        request = urllib.request.Request(
            config.API_URL,
            data=body,
            headers={
                "Authorization": "Bearer %s" % api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=config.API_TIMEOUT_SECONDS
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = _first_message(payload)
            if not content:
                result["error"] = "AI replied with an empty message."
                LOGGER.error("%s Payload keys: %s", result["error"], list(payload))
                return result
            result["ok"] = True
            result["raw"] = content
            LOGGER.info("AI call succeeded on attempt %d (model=%s, %d chars).",
                        attempt, prompt.get("model"), len(content))
            return result

        except urllib.error.HTTPError as error:
            detail = _read_error_body(error)
            LOGGER.error("HTTP %s from Groq (attempt %d): %s",
                         error.code, attempt, detail[:400])
            if error.code == 400 and json_mode and "response_format" in detail:
                LOGGER.warning("Model rejected json_object mode; retrying plain text.")
                json_mode = False
                continue
            if error.code in (408, 409, 429, 500, 502, 503, 504) \
                    and attempt < config.API_MAX_ATTEMPTS:
                time.sleep(delay)
                delay *= 2
                continue
            result["error"] = "Groq API returned HTTP %s: %s" % (
                error.code, _short_reason(detail))
            return result

        except urllib.error.URLError as error:
            LOGGER.error("Network error talking to Groq (attempt %d): %s",
                         attempt, error.reason)
            if attempt < config.API_MAX_ATTEMPTS:
                time.sleep(delay)
                delay *= 2
                continue
            result["error"] = "Could not reach the Groq API (%s)." % error.reason
            return result

        except (TimeoutError, OSError) as error:
            LOGGER.error("Connection problem on attempt %d: %s", attempt, error)
            if attempt < config.API_MAX_ATTEMPTS:
                time.sleep(delay)
                delay *= 2
                continue
            result["error"] = "Connection to the Groq API failed (%s)." % error
            return result

        except ValueError as error:
            result["error"] = "Groq API sent a non-JSON envelope (%s)." % error
            LOGGER.error(result["error"])
            return result

    result["error"] = result["error"] or "AI call failed after all retries."
    return result


def _build_payload(prompt, json_mode):
    """Assemble the chat-completions request body."""
    payload = {
        "model": prompt.get("model") or config.TEXT_MODEL,
        "temperature": config.API_TEMPERATURE,
        "messages": [
            {"role": "system", "content": prompt.get("system", SYSTEM_PROMPT)},
            {"role": "user", "content": prompt["user_text"]},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _first_message(payload):
    """Pull the assistant text out of a chat-completions envelope."""
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, list):                    # some models chunk the reply
        parts = [str(part.get("text", "")) for part in content
                 if isinstance(part, dict)]
        content = "".join(parts)
    return str(content or "").strip()


def _read_error_body(error):
    try:
        return error.read().decode("utf-8", "replace")
    except Exception:                                # noqa: BLE001
        return str(error)


def _short_reason(detail):
    """Extract a human-sized reason from a Groq error body."""
    try:
        parsed = json.loads(detail)
        message = parsed.get("error", {}).get("message")
        if message:
            return str(message)[:200]
    except (ValueError, AttributeError):
        pass
    return detail[:200] if detail else "no detail"


# --------------------------------------------------------------------------
# 3. Response parsing
# --------------------------------------------------------------------------
def parse_response(raw):
    """Extract the JSON object from a model reply. Returns a dict or None.

    Handles the usual misbehaviour: markdown fences, a sentence before the
    JSON, a JSON array wrapper, or trailing text after the closing brace.
    """
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        LOGGER.error("parse_response received an empty reply.")
        return None

    text = _strip_code_fences(text)

    try:
        data = json.loads(text)
    except ValueError:
        block = _extract_json_block(text)
        if block is None:
            LOGGER.error("No JSON object found in reply: %s", text[:300])
            return None
        try:
            data = json.loads(block)
        except ValueError as error:
            LOGGER.error("JSON in reply is malformed (%s): %s", error, block[:300])
            return None

    if isinstance(data, list):
        dicts = [item for item in data if isinstance(item, dict)]
        if not dicts:
            LOGGER.error("Reply was a list without any object in it.")
            return None
        if len(dicts) > 1:
            LOGGER.warning("Reply held %d objects; using the first.", len(dicts))
        data = dicts[0]

    if not isinstance(data, dict):
        LOGGER.error("Reply parsed to %s, expected an object.", type(data))
        return None

    # A few models nest the answer, e.g. {"deal": {...}}.
    if not any(key in data for key in RESPONSE_KEYS):
        for value in data.values():
            if isinstance(value, dict) and any(k in value for k in RESPONSE_KEYS):
                LOGGER.warning("Unwrapped a nested deal object from the reply.")
                return value
    return data


def _strip_code_fences(text):
    if "```" not in text:
        return text
    chunks = text.split("```")
    for chunk in chunks[1:]:
        candidate = chunk
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate
    return text.replace("```", " ")


def _extract_json_block(text):
    """Return the first balanced {...} block in text, honouring strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


# --------------------------------------------------------------------------
# 4. Schema validation
# --------------------------------------------------------------------------
def validate_response(data):
    """Check and normalise an AI payload before the app is allowed to use it.

    Returns (ok, cleaned, errors).

    Rejects (ok=False) when the shape is wrong: unknown category, unknown
    discount type, non-numeric numbers, bad date format, confidence out of
    range, missing description/eligibility container.

    A schema-valid payload that simply could not find a value (expiry_date is
    null) is accepted here -- deciding what to do about that is
    logic_manager's job, not this module's.
    """
    errors = []
    if not isinstance(data, dict):
        return False, {}, ["AI response was not a JSON object."]

    cleaned = {}

    # -- merchant -----------------------------------------------------------
    merchant = _as_text(data.get("merchant"))
    if merchant and len(merchant) > 120:
        merchant = merchant[:120]
    cleaned["merchant"] = merchant or None

    # -- category (fixed vocabulary, strictly enforced) ---------------------
    category = _as_text(data.get("category")).lower()
    if not category:
        errors.append("category is missing.")
    elif category not in config.VALID_CATEGORIES:
        errors.append(
            "category '%s' is not one of the allowed values (%s)."
            % (category, ", ".join(config.VALID_CATEGORIES))
        )
    cleaned["category"] = category

    # -- discount -----------------------------------------------------------
    discount_type = _as_text(data.get("discount_type")).lower()
    if not discount_type:
        errors.append("discount_type is missing.")
    elif discount_type not in config.VALID_DISCOUNT_TYPES:
        errors.append(
            "discount_type '%s' is not one of the allowed values (%s)."
            % (discount_type, ", ".join(config.VALID_DISCOUNT_TYPES))
        )
    cleaned["discount_type"] = discount_type

    discount_value, bad = _as_number(data.get("discount_value"))
    if bad:
        errors.append("discount_value must be a number or null.")
    elif discount_value is not None and discount_value < 0:
        errors.append("discount_value cannot be negative.")
    elif discount_type == "percent" and discount_value is not None \
            and discount_value > 100:
        errors.append("a percentage discount cannot exceed 100.")
    cleaned["discount_value"] = discount_value

    for key in ("original_price", "discounted_price", "min_spend"):
        value, bad = _as_number(data.get(key))
        if bad:
            errors.append("%s must be a number or null." % key)
        elif value is not None and value < 0:
            errors.append("%s cannot be negative." % key)
        cleaned[key] = value

    # -- description --------------------------------------------------------
    description = _as_text(data.get("description"))
    cleaned["description"] = description[:200]

    # -- expiry date --------------------------------------------------------
    expiry_raw = _as_text(data.get("expiry_date"))
    if not expiry_raw or expiry_raw.lower() in ("null", "none", "n/a", "-"):
        cleaned["expiry_date"] = None
    else:
        try:
            parsed = datetime.strptime(expiry_raw[:10], "%Y-%m-%d").date()
            cleaned["expiry_date"] = parsed.isoformat()
        except ValueError:
            errors.append(
                "expiry_date '%s' is not in YYYY-MM-DD format." % expiry_raw)
            cleaned["expiry_date"] = None

    # -- eligibility --------------------------------------------------------
    eligibility_in = data.get("eligibility")
    if eligibility_in is None:
        errors.append("eligibility object is missing.")
        eligibility_in = {}
    elif not isinstance(eligibility_in, dict):
        errors.append("eligibility must be an object.")
        eligibility_in = {}

    eligibility = {}
    for flag in ELIGIBILITY_FLAGS:
        eligibility[flag] = _as_bool(eligibility_in.get(flag))
    for key in ("min_age", "max_age"):
        value, bad = _as_number(eligibility_in.get(key))
        if bad:
            errors.append("eligibility.%s must be a number or null." % key)
        eligibility[key] = int(value) if isinstance(value, float) and \
            value.is_integer() else value
    eligibility["school"] = _as_text(eligibility_in.get("school")) or None
    eligibility["raw"] = _as_text(eligibility_in.get("raw"))
    cleaned["eligibility"] = eligibility

    # -- flags and lists ----------------------------------------------------
    cleaned["single_use"] = _as_bool(data.get("single_use"))
    cleaned["terms"] = _as_text_list(data.get("terms"))[:5]
    cleaned["missing_fields"] = [
        item.lower() for item in _as_text_list(data.get("missing_fields"))
    ]

    # -- confidence ---------------------------------------------------------
    confidence, bad = _as_number(data.get("confidence"))
    if bad or confidence is None:
        errors.append("confidence must be a number between 0 and 1.")
        cleaned["confidence"] = 0.0
    elif confidence < 0 or confidence > 1:
        errors.append("confidence %s is outside 0.0-1.0." % confidence)
        cleaned["confidence"] = 0.0
    else:
        cleaned["confidence"] = round(float(confidence), 2)

    ok = not errors
    if not ok:
        LOGGER.warning("Rejected AI payload: %s", "; ".join(errors))
    return ok, cleaned, errors


def _as_text(value):
    if value is None or isinstance(value, (dict, list, bool)):
        return "" if not isinstance(value, bool) else str(value)
    return str(value).strip()


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in ("true", "yes", "y", "1")


def _as_number(value):
    """Return (number_or_None, bad_flag). Tolerates "RM10.50" and "20%"."""
    if value is None or value == "":
        return None, False
    if isinstance(value, bool):
        return None, True
    if isinstance(value, (int, float)):
        return float(value), False
    text = str(value).strip().lower()
    if text in ("null", "none", "n/a", "-"):
        return None, False
    kept = "".join(char for char in text
                   if char.isdigit() or char in ".-")
    kept = kept.replace("--", "-")
    try:
        return float(kept), False
    except ValueError:
        return None, True


def _as_text_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(";") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


# --------------------------------------------------------------------------
# 5. Orchestration -- the single entry point used by main.py
# --------------------------------------------------------------------------
def process(record):
    """Run one record through the full AI pipeline.

    build_prompt -> call_api -> parse_response -> validate_response, retrying
    with the validation errors fed back to the model when the schema check
    fails (for example when it invents a category outside the allowed set).

    Returns {"ok", "data", "errors", "attempts", "model", "raw"}.
    """
    outcome = {"ok": False, "data": {}, "errors": [], "attempts": 0,
               "model": "", "raw": ""}
    correction = None

    for attempt in range(1, config.SCHEMA_MAX_ATTEMPTS + 1):
        outcome["attempts"] = attempt
        prompt = build_prompt(record, correction=correction)
        outcome["model"] = prompt.get("model", "")

        call = call_api(prompt)
        if not call.get("ok"):
            outcome["errors"] = [call.get("error", "AI call failed.")]
            return outcome

        outcome["raw"] = call.get("raw", "")
        parsed = parse_response(outcome["raw"])
        if parsed is None:
            correction = ["Your reply was not valid JSON. Send only the JSON object."]
            outcome["errors"] = ["AI reply could not be parsed as JSON."]
            continue

        ok, cleaned, errors = validate_response(parsed)
        if ok:
            outcome["ok"] = True
            outcome["data"] = cleaned
            outcome["errors"] = []
            return outcome

        LOGGER.info("Schema attempt %d failed; re-asking the model.", attempt)
        correction = errors
        outcome["errors"] = errors
        outcome["data"] = cleaned

    LOGGER.error("AI could not produce a valid payload after %d attempts.",
                 outcome["attempts"])
    return outcome

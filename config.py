"""Shared configuration for DealKeeper.

This module holds constants, file paths and logging setup only.
It contains no user interaction (no print/input), no API calls and no
business rules -- those live in io_manager, ai_manager and logic_manager.
"""

import logging
import os

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DEALS_FILE = os.path.join(DATA_DIR, "deals.json")
PROFILE_FILE = os.path.join(DATA_DIR, "profile.json")
LOG_FILE = os.path.join(DATA_DIR, "app.log")
ENV_FILE = os.path.join(BASE_DIR, ".env")

# --------------------------------------------------------------------------
# Groq API settings
# --------------------------------------------------------------------------
API_URL = "https://api.groq.com/openai/v1/chat/completions"
TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
VISION_MODEL = os.environ.get(
    "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"
)
API_TIMEOUT_SECONDS = 45
API_MAX_ATTEMPTS = 3          # network/transient retries inside call_api
SCHEMA_MAX_ATTEMPTS = 2       # re-ask the model when the JSON fails validation
API_TEMPERATURE = 0.0         # deterministic-as-possible extraction
MAX_IMAGE_BYTES = 4 * 1024 * 1024

# --------------------------------------------------------------------------
# Domain vocabulary -- the fixed set the app is willing to accept from the AI
# --------------------------------------------------------------------------
VALID_CATEGORIES = (
    "food",
    "groceries",
    "retail",
    "apparel",
    "cosmetics",
    "electronics",
    "subscription",
    "transport",
    "entertainment",
    "other",
)

VALID_DISCOUNT_TYPES = (
    "percent",     # 20% off
    "fixed",       # RM10 off
    "bogo",        # buy one get one / 50% off 2nd item
    "freebie",     # free gift / free delivery
    "other",
)

VALID_SOURCE_TYPES = ("text", "image", "manual")

# --------------------------------------------------------------------------
# Business thresholds (used by logic_manager)
# --------------------------------------------------------------------------
EXPIRING_SOON_DAYS = 7            # deals expiring within 7 days are urgent
MIN_CONFIDENCE = 0.6              # below this the deal goes to "needs review"
DUPLICATE_NAME_RATIO = 0.85       # merchant-name similarity for a duplicate
DUPLICATE_PERCENT_TOLERANCE = 5.0 # +/- percentage points counted as "similar"
DUPLICATE_FIXED_TOLERANCE = 2.0   # +/- currency units counted as "similar"
STRONG_PERCENT_DISCOUNT = 30.0    # "big" percentage discount
STRONG_FIXED_DISCOUNT = 10.0      # "big" fixed discount
HIGH_CONFIDENCE = 0.7             # confident enough to act on immediately
CURRENCY = "RM"

# Fields the domain considers mandatory before a deal joins the active list.
REQUIRED_DEAL_FIELDS = ("merchant", "category", "expiry_date")


def load_env_file(path=None):
    """Load simple KEY=VALUE pairs from a .env file into os.environ.

    Existing environment variables always win. Missing file is not an error.
    Returns the number of keys loaded.
    """
    target = path or ENV_FILE
    loaded = 0
    try:
        with open(target, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
                    loaded += 1
    except FileNotFoundError:
        return 0
    except OSError as error:
        logging.getLogger(__name__).warning("Could not read %s: %s", target, error)
        return 0
    return loaded


def get_api_key():
    """Return the Groq API key from the environment, or an empty string."""
    load_env_file()
    return os.environ.get("GROQ_API_KEY", "").strip()


def ensure_data_dir():
    """Create the data directory if it does not exist. Never raises."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        return True
    except OSError as error:
        logging.getLogger(__name__).error("Cannot create %s: %s", DATA_DIR, error)
        return False


def setup_logging():
    """Send all diagnostics to a log file so the terminal stays clean.

    Logging to a file (instead of stdout) is deliberate: every character the
    user sees must come from io_manager.
    """
    ensure_data_dir()
    root = logging.getLogger()
    if root.handlers:
        return root
    root.setLevel(logging.INFO)
    try:
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    return root

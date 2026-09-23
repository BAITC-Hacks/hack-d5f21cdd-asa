import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DATA_FILES = [ROOT / "data" / "vendors.csv", ROOT / "data" / "synthetic_extra.csv"]
CACHE_DIR = ROOT / ".cache"

MAX_RESULTS = 3

# LLM выключен по умолчанию: без ключа всё работает на шаблонах.
USE_LLM = os.getenv("USE_LLM", "0") == "1"
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-5")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "8"))

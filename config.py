import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده است (فایل .env یا Environment Variable در Render).")

WEBHOOK_BASE = (os.getenv("WEBHOOK_BASE") or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "dev-secret")
MODE = os.getenv("BOT_MODE", "webhook" if WEBHOOK_BASE else "polling").lower()
DROP_PENDING = os.getenv("DROP_PENDING_UPDATES", "0") == "1"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATA_DIR = os.getenv("DATA_DIR", "./data")
DB_PATH = os.path.join(DATA_DIR, "dong.db")

CURRENCY = os.getenv("CURRENCY", "تومان")
STATE_TTL = int(os.getenv("STATE_TTL", "1800"))
PROXY = os.getenv("HTTPS_PROXY_URL", "")
MAX_MEMBERS = 40

# --- OCR (خواندن عکس فیش با Gemini) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
OCR_TIMEOUT = int(os.getenv("OCR_TIMEOUT", "45"))
OCR_MAX_ITEMS = 40
OCR_MAX_BYTES = 8 * 1024 * 1024
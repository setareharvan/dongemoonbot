"""خواندن عکس فیش با Gemini + تلاش خودکار روی مدل‌های جایگزین هنگام شلوغی."""
import base64
import json
import logging
import time

import requests

import config
from utils import parse_money, new_id

log = logging.getLogger(__name__)

PROMPT = """تو یک حسابدار دقیق هستی. این عکس یک فیش/رسید رستوران یا فروشگاه ایرانی است.
همهٔ ردیف‌های خرید را استخراج کن.

قواعد:
- name: نام کالا/غذا، کوتاه و خوانا (حداکثر ۴۰ حرف). اگر تعداد بیشتر از ۱ است، «×2» را به نام اضافه کن.
- qty: تعداد (اگر نامشخص بود 1).
- price: مبلغ کل همان ردیف (تعداد × قیمت واحد)، عدد صحیح بدون جداکننده.
- اگر مبالغ فیش به ریال است، همه را به تومان تبدیل کن (تقسیم بر ۱۰) و currency را "rial" بگذار؛ اگر تومان است "toman".
- tax_and_service: مجموع مالیات + حق سرویس + هزینهٔ ارسال/پیک (تومان). اگر نبود 0.
- discount: مجموع تخفیف‌ها (تومان، مثبت). اگر نبود 0.
- total: مبلغ نهایی پرداختی که روی فیش چاپ شده (تومان). اگر پیدا نشد 0.
- ردیف‌هایی مثل «جمع کل»، «قابل پرداخت»، «شماره سفارش» را به عنوان آیتم نیاور.
- فقط JSON برگردان، هیچ توضیح اضافه‌ای نده."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "qty": {"type": "INTEGER"},
                    "price": {"type": "INTEGER"},
                },
                "required": ["name", "price"],
            },
        },
        "tax_and_service": {"type": "INTEGER"},
        "discount": {"type": "INTEGER"},
        "total": {"type": "INTEGER"},
        "currency": {"type": "STRING"},
    },
    "required": ["items"],
}

# اگر مدل اصلی شلوغ/در دسترس نبود، به ترتیب این‌ها امتحان می‌شوند
FALLBACK_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

REQUEST_TIMEOUT = 15   # ثانیه برای هر درخواست
TOTAL_BUDGET = 45      # حداکثر کل زمان خواندن (تا تلگرام منتظر نماند)


def _models():
    seen, out = set(), []
    for m in [config.GEMINI_MODEL] + FALLBACK_MODELS:
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _clean_json(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b != -1:
        t = t[a:b + 1]
    return json.loads(t)


def _norm_amount(v):
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return max(0, int(round(v)))
    p = parse_money(str(v))
    return p if p and p > 0 else 0


def read_receipt(image_bytes, mime="image/jpeg"):
    if not config.GEMINI_API_KEY:
        return {"ok": False, "error": "کلید GEMINI_API_KEY تنظیم نشده است. فعلاً دستی وارد کن."}
    if len(image_bytes) > config.OCR_MAX_BYTES:
        return {"ok": False, "error": "عکس خیلی بزرگ است؛ به صورت «عکس» (نه فایل) بفرست."}

    payload = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime,
                             "data": base64.b64encode(image_bytes).decode()}},
            {"text": PROMPT},
        ]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
        },
    }
    headers = {"Content-Type": "application/json",
               "x-goog-api-key": config.GEMINI_API_KEY}

    deadline = time.monotonic() + TOTAL_BUDGET
    last_code = None
    data = None

    for model in _models():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(2):
            if time.monotonic() > deadline:
                break
            try:
                r = requests.post(url, headers=headers,
                                  json=payload, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                log.warning("OCR network error (%s): %s", model, e)
                last_code = None
                if attempt == 0 and time.monotonic() + 2 < deadline:
                    time.sleep(2)
                    continue
                break

            last_code = r.status_code
            if r.status_code == 200:
                data = r.json()
                break
            if r.status_code in (400, 401, 403):
                log.error("OCR auth error %s: %s", r.status_code, r.text[:300])
                return {"ok": False, "error": f"کلید Gemini نامعتبر است (کد {r.status_code})؛ GEMINI_API_KEY را در Render بررسی کن."}
            if r.status_code == 404:
                log.warning("OCR model not found: %s", model)
                break  # مدل بعدی
            if r.status_code == 429:
                log.warning("OCR quota on %s", model)
                break  # سهمیهٔ این مدل پر شده؛ مدل بعدی
            if r.status_code >= 500:
                log.warning("OCR busy %s on %s", r.status_code, model)
                if attempt == 0 and time.monotonic() + 3 < deadline:
                    time.sleep(3)
                    continue
                break  # مدل بعدی
            break
        if data is not None:
            break

    if data is None:
        if last_code == 429:
            return {"ok": False, "error": "سهمیهٔ رایگان امروز تمام شده 😅 فردا ریست می‌شود؛ فعلاً 💰 دستی وارد کن."}
        if last_code in (500, 503):
            return {"ok": False, "error": "سرویس گوگل الان شلوغ است (503). چند دقیقه بعد دوباره بفرست یا 💰 دستی وارد کن."}
        if last_code == 404:
            return {"ok": False, "error": "مدل پیدا نشد (404). در Render مقدار GEMINI_MODEL را روی gemini-flash-latest بگذار."}
        return {"ok": False, "error": "سرویس خواندن فیش جواب نداد؛ کمی بعد دوباره بفرست."}

    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        raw = _clean_json(text)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        log.warning("OCR parse error: %s | %s", e, str(data)[:300])
        return {"ok": False, "error": "نتوانستم فیش را بخوانم؛ عکس واضح‌تر و کامل بفرست."}

    items = []
    for it in (raw.get("items") or [])[:config.OCR_MAX_ITEMS]:
        name = str(it.get("name", "")).strip()[:40]
        price = _norm_amount(it.get("price"))
        if not name or price <= 0:
            continue
        items.append({"id": new_id(), "name": name,
                     "price": price, "owners": []})

    if not items:
        return {"ok": False, "error": "هیچ آیتمی روی فیش نخواندم؛ عکس واضح‌تر بفرست یا دستی وارد کن."}

    note = ""
    if str(raw.get("currency", "")).lower() in ("rial", "ریال"):
        note = "💱 مبالغ فیش ریالی بود و به تومان تبدیل شد."
    return {
        "ok": True,
        "items": items,
        "extra": _norm_amount(raw.get("tax_and_service")),
        "discount": _norm_amount(raw.get("discount")),
        "total": _norm_amount(raw.get("total")) or None,
        "note": note,
    }

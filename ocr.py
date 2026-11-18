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
- price: مبلغ کل همان ردیف (تعداد × قیمت واحد)، دقیقاً همان عددی که روی فیش چاپ شده، بدون هیچ تبدیل واحد.
- tax_and_service: مجموع مالیات + حق سرویس + هزینهٔ ارسال/پیک، دقیقاً كما چاپ شده. اگر نبود 0.
- discount: مجموع تخفیف‌ها (مثبت، كما چاپ شده). اگر نبود 0.
- total: مبلغ نهایی پرداختی چاپ‌شده. اگر نبود 0.
- currency: فقط اگر روی فیش صریحاً «ریال / Rials / IRR» نوشته شده "rial"؛ اگر صریحاً «تومان / Toman / IRT» نوشته شده "toman"؛ در غیر این صورت "unknown". از روی بزرگی عدد حدس نزن.
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

FALLBACK_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

REQUEST_TIMEOUT = 15
TOTAL_BUDGET = 45


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
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(image_bytes).decode()}},
            {"text": PROMPT},
        ]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
        },
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}

    deadline = time.monotonic() + TOTAL_BUDGET
    last_code = None
    data = None

    for model in _models():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(2):
            if time.monotonic() > deadline:
                break
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
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
                break
            if r.status_code == 429:
                log.warning("OCR quota on %s", model)
                break
            if r.status_code >= 500:
                log.warning("OCR busy %s on %s", r.status_code, model)
                if attempt == 0 and time.monotonic() + 3 < deadline:
                    time.sleep(3)
                    continue
                break
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

    # اعداد دقیقاً كما چاپ شده؛ تبدیل واحد فقط اینجا و فقط اگر فیش صریحاً ریالی باشد
    printed = []
    for it in (raw.get("items") or [])[:config.OCR_MAX_ITEMS]:
        name = str(it.get("name", "")).strip()[:40]
        price = _norm_amount(it.get("price"))
        if not name or price <= 0:
            continue
        printed.append({"id": new_id(), "name": name, "price": price})

    if not printed:
        return {"ok": False, "error": "هیچ آیتمی روی فیش نخواندم؛ عکس واضح‌تر بفرست یا دستی وارد کن."}

    cur = str(raw.get("currency", "")).lower()
    if cur in ("rial", "ریال"):
        factor = 0.1
        note = "💱 روی فیش «ریال» بود؛ مبالغ ÷۱۰ شد تا تومان شود. اگر اشتباه خواندم، با 💱 ×۱۰ / ÷۱۰ اصلاح کن."
    elif cur in ("toman", "تومان"):
        factor = 1
        note = ""
    else:
        factor = 1
        note = "💱 واحد فیش مشخص نبود؛ اعداد همان‌طور که چاپ شده آوردم (فرض: تومان). اگر یک صفر کم دارد 💱 ×۱۰ و اگر ریالی است 💱 ÷۱۰ را بزن."

    items = [{"id": it["id"], "name": it["name"],
              "price": max(1, int(round(it["price"] * factor))), "owners": []}
             for it in printed]

    return {
        "ok": True,
        "items": items,
        "extra": int(round(_norm_amount(raw.get("tax_and_service")) * factor)),
        "discount": int(round(_norm_amount(raw.get("discount")) * factor)),
        "total": int(round(_norm_amount(raw.get("total")) * factor)) or None,
        "note": note,
    }
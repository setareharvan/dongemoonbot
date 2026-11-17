"""خواندن عکس فیش با Gemini و برگرداندن آیتم‌ها به صورت ساخت‌یافته."""
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
    """خروجی: {'ok': True, items, extra, discount, total, note} یا {'ok': False, 'error': پیام}"""
    if not config.GEMINI_API_KEY:
        return {"ok": False, "error": "کلید GEMINI_API_KEY تنظیم نشده است. فعلاً دستی وارد کن."}
    if len(image_bytes) > config.OCR_MAX_BYTES:
        return {"ok": False, "error": "عکس خیلی بزرگ است؛ به صورت «عکس» (نه فایل) بفرست."}

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent")
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

    data = None
    for attempt in range(2):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=config.OCR_TIMEOUT)
        except requests.RequestException as e:
            log.warning("OCR network error: %s", e)
            if attempt == 0:
                time.sleep(2)
                continue
            return {"ok": False, "error": "ارتباط با سرویس خواندن فیش برقرار نشد؛ دوباره امتحان کن."}
        if r.status_code == 200:
            data = r.json()
            break
        if r.status_code == 429:
            return {"ok": False, "error": "سهمیهٔ رایگان امروز تمام شده 😅 بعداً امتحان کن یا دستی وارد کن."}
        if r.status_code in (400, 401, 403):
            log.error("OCR auth error %s: %s", r.status_code, r.text[:300])
            return {"ok": False, "error": "کلید Gemini نامعتبر است؛ GEMINI_API_KEY را بررسی کن."}
        if attempt == 0 and r.status_code >= 500:
            time.sleep(2)
            continue
        log.error("OCR error %s: %s", r.status_code, r.text[:300])
        return {"ok": False, "error": "سرویس خواندن فیش خطا داد؛ کمی بعد دوباره بفرست."}

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
        items.append({"id": new_id(), "name": name, "price": price, "owners": []})

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
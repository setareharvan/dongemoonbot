import os
import time
import logging
import threading
from collections import OrderedDict

from flask import Flask, request, abort, jsonify
import telebot

import config
from bot_instance import bot
import handlers  # noqa: F401  (ثبت هندلرها با import انجام می‌شود)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

app = Flask(__name__)

# جلوگیری از پردازش تکراری یک آپدیت (وقتی تلگرام به‌خاطر کندی OCR دوباره می‌فرستد)
_seen = OrderedDict()
_seen_lock = threading.Lock()


def _seen_update(uid):
    if uid is None:
        return False
    with _seen_lock:
        if uid in _seen:
            return True
        _seen[uid] = None
        while len(_seen) > 2000:
            _seen.popitem(last=False)
    return False


@app.get("/")
@app.get("/healthz")
def health():
    return "ok", 200


@app.post(config.WEBHOOK_PATH)
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.WEBHOOK_SECRET:
        abort(403)
    if request.headers.get("content-type") != "application/json":
        abort(400)
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    if _seen_update(update.update_id):
        return "", 200
    bot.process_new_updates([update])
    return "", 200


@app.get("/tginfo")
def tginfo():
    """عیب‌یابی بدون لو رفتن توکن: /tginfo?key=WEBHOOK_SECRET"""
    if request.args.get("key") != config.WEBHOOK_SECRET:
        abort(403)
    return jsonify(bot.get_webhook_info().to_json())


def setup_webhook():
    url = config.WEBHOOK_BASE + config.WEBHOOK_PATH
    for attempt in range(3):
        try:
            bot.remove_webhook()          # ✅ بدون آرگومان
            time.sleep(1)
            bot.set_webhook(              # ✅ drop_pending_updates اینجا مجاز است
                url=url,
                secret_token=config.WEBHOOK_SECRET,
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=config.DROP_PENDING,
            )
            log.info("Webhook set: %s", url)
            return True
        except Exception as e:
            log.warning("webhook setup failed (%d/3): %s", attempt + 1, e)
            time.sleep(3)
    return False


if config.MODE == "webhook":
    if config.WEBHOOK_BASE:
        setup_webhook()
    else:
        log.error("WEBHOOK_BASE/RENDER_EXTERNAL_URL تنظیم نشده است!")

if __name__ == "__main__":
    if config.MODE == "polling":
        # فقط برای اجرای لوکال؛ هرگز همزمان با نسخهٔ روی سرور اجرا نکن
        bot.remove_webhook()              # ✅ بدون آرگومان
        log.info("Polling started (local)")
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30,
                             allowed_updates=["message", "callback_query"])
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

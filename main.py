import os
import re
import time
import logging
import threading
from collections import OrderedDict, deque

from flask import Flask, request, abort, jsonify
import telebot

import config
from bot_instance import bot
import handlers  # noqa: F401  (ثبت هندلرها با import انجام می‌شود)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

app = Flask(__name__)

_seen = OrderedDict()
_seen_lock = threading.Lock()
_errors = deque(maxlen=5)


class _Capture(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            try:
                _errors.append(self.format(record))
            except Exception:
                pass


_cap = _Capture()
_cap.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.getLogger().addHandler(_cap)

WEBHOOK_RESULT = {"ok": False, "error": "not attempted"}


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


@app.get("/status")
def status():
    if request.args.get("key") != config.WEBHOOK_SECRET:
        abort(403)
    try:
        wi = bot.get_webhook_info()
        info = {"url": wi.url, "pending": wi.pending_update_count,
                "last_error": wi.last_error_message}
    except Exception as e:
        info = {"telegram_error": str(e)}
    t = config.TOKEN
    return jsonify({
        "mode": config.MODE,
        "token_preview": t[:6] + f"... (len={len(t)})",
        "webhook_setup": WEBHOOK_RESULT,
        "webhook_info": info,
        "recent_errors": list(_errors),
    })


@app.get("/fixwebhook")
def fixwebhook():
    if request.args.get("key") != config.WEBHOOK_SECRET:
        abort(403)
    ok = setup_webhook()
    return jsonify({"ok": ok, "detail": WEBHOOK_RESULT})


@app.post(config.WEBHOOK_PATH)
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.WEBHOOK_SECRET:
        abort(403)
    if request.headers.get("content-type") != "application/json":
        abort(400)
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    if _seen_update(update.update_id):
        return "", 200
    try:
        bot.process_new_updates([update])
    except Exception:
        log.exception("update processing crashed")
    return "", 200


@app.get("/tginfo")
def tginfo():
    if request.args.get("key") != config.WEBHOOK_SECRET:
        abort(403)
    try:
        return jsonify(bot.get_webhook_info().to_json())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200


def setup_webhook():
    global WEBHOOK_RESULT
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", config.WEBHOOK_SECRET):
        WEBHOOK_RESULT = {"ok": False,
                          "error": "WEBHOOK_SECRET کاراکتر غیرمجاز دارد"}
        log.error(WEBHOOK_RESULT["error"])
        return False
    url = config.WEBHOOK_BASE + config.WEBHOOK_PATH
    for attempt in range(3):
        try:
            bot.remove_webhook()
            time.sleep(1)
            bot.set_webhook(url=url, secret_token=config.WEBHOOK_SECRET,
                            allowed_updates=["message", "callback_query"],
                            drop_pending_updates=config.DROP_PENDING)
            WEBHOOK_RESULT = {"ok": True, "url": url}
            log.info("Webhook set: %s", url)
            return True
        except Exception as e:
            WEBHOOK_RESULT = {"ok": False, "error": str(e)}
            log.warning("webhook setup failed (%d/3): %s", attempt + 1, e)
            time.sleep(2)
    return False


if config.MODE == "webhook":
    if config.WEBHOOK_BASE:
        setup_webhook()
    else:
        WEBHOOK_RESULT = {"ok": False, "error": "WEBHOOK_BASE missing"}
        log.error("WEBHOOK_BASE/RENDER_EXTERNAL_URL تنظیم نشده است!")

if __name__ == "__main__":
    if config.MODE == "polling":
        bot.remove_webhook()
        log.info("Polling started (local)")
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30,
                             allowed_updates=["message", "callback_query"])
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

import re
import time
import logging
import functools

from telebot.apihelper import ApiTelegramException

import config
import keyboards as kb
import ocr
from bot_instance import bot
from storage import store
from utils import parse_money, parse_percent, money, esc, new_id, split_text, split_item
from calc import subtotal_of, total_of, compute, settle

log = logging.getLogger(__name__)

DONE = {"تمام", "پایان", "تمام شد", "تموم", "done", "/done", "/finish"}
CANCEL = {"/cancel", "لغو", kb.BTN_CANCEL}

WELCOME = (
    "سلام 👋 به <b>دنگ‌شمار</b> خوش اومدی!\n\n"
    "۱) 👥 اعضا → افزودن (چند نفر یکجا: <b>علی، رضا، سارا</b>)\n"
    "۲) 💰 ثبت هزینه → دستی\n"
    "   📸 فیش با عکس → عکس فیش را بفرست، خودم آیتم‌ها را می‌خوانم!\n"
    "۳) 📊 محاسبه دنگ → گزارش کامل + کمترین تراکنش تسویه\n\n"
    "✨ عدد فارسی، «۳۰۰ هزار» و «۹٪» هم قبول می‌شود. هرجا گیر کردی ❌ لغو."
)
ITEM_ASK = (
    "🍽 نام و قیمت آیتم را <b>در یک پیام</b> بفرست:\n"
    "<b>جوجه 300000</b>\n"
    "(یا فقط نام را بفرست تا قیمت را جدا بپرسم)\n\n"
    "وقتی تمام شد کلمهٔ <b>تمام</b> را بفرست."
)
EXTRA_ASK = (
    "🧾 مالیات / حق سرویس این فیش چقدر است؟\n"
    "• مبلغ: <b>85000</b>\n• درصد: <b>9%</b>\n• ندارد: <b>0</b>\n\n"
    "به نسبت سهم هر نفر از غذا تقسیم می‌شود."
)
PHOTO_ASK = (
    "📸 عکس فیش را بفرست.\n\n"
    "✅ نور کافی، کل فیش در کادر، بدون سایه و تاخوردگی\n"
    "✅ به صورت «عکس» بفرست، نه فایل\n\n"
    "من آیتم‌ها و مبلغ‌ها را می‌خوانم و برای تأیید نشانت می‌دهم."
)
OCR_BUSY = "🔎 دارم فیش را می‌خوانم… (چند ثانیه صبر کن)"


# ---------- ابزار ارسال امن ----------
def send(cid, text, markup=None):
    parts = split_text(text)
    for i, p in enumerate(parts):
        try:
            bot.send_message(cid, p, parse_mode="HTML", disable_web_page_preview=True,
                             reply_markup=markup if i == len(parts) - 1 else None)
        except ApiTelegramException as e:
            log.warning("send failed: %s", e)


def edit(cid, mid, text, markup=None):
    try:
        bot.edit_message_text(text, cid, mid, parse_mode="HTML",
                              reply_markup=markup, disable_web_page_preview=True)
    except ApiTelegramException as e:
        if "message is not modified" in str(e):
            return
        log.warning("edit failed: %s", e)
        send(cid, text, markup)


def edit_kb(cid, mid, markup):
    try:
        bot.edit_message_reply_markup(cid, mid, reply_markup=markup)
    except ApiTelegramException as e:
        log.warning("edit_kb failed: %s", e)


def answer(call_id, text=None, alert=False):
    try:
        bot.answer_callback_query(call_id, text, show_alert=alert)
    except ApiTelegramException:
        pass


def safe_msg(fn):
    @functools.wraps(fn)
    def wrapper(m):
        try:
            return fn(m)
        except Exception:
            log.exception("message handler error")
            send(m.chat.id, "⚠️ خطای موقت رخ داد؛ دوباره امتحان کن یا ❌ لغو را بزن.")
    return wrapper


def safe_cb(fn):
    @functools.wraps(fn)
    def wrapper(c):
        try:
            return fn(c)
        except Exception:
            log.exception("callback handler error")
            answer(c.id, "⚠️ خطای موقت", True)
    return wrapper


# ---------- وضعیت (state) ----------
def get_state(s):
    st = s.get("state")
    if st and time.time() - st.get("ts", 0) > config.STATE_TTL:
        s["state"] = None
        return None
    return st


def set_state(s, step, **kw):
    s["state"] = {"step": step, "ts": time.time(), **kw}


def names_of(s):
    return {m["id"]: m["name"] for m in s["members"]}


def toggle(lst, x):
    lst.remove(x) if x in lst else lst.append(x)


def next_unowned(ex, start=0):
    for i in range(start, len(ex["items"])):
        if not ex["items"][i].get("owners"):
            return i
    return None


def extra_ask(suggest=None):
    t = EXTRA_ASK
    if suggest:
        t += f"\n\n🔎 از فیش خواندم: <b>{money(suggest)}</b> — اگر درست است همین را بفرست."
    return t


# ---------- متن‌ها ----------
def members_text(s):
    if not s["members"]:
        return "👥 هنوز عضوی نداری. روی ➕ افزودن عضو بزن."
    return "👥 <b>اعضا (%d نفر):</b>\n%s" % (
        len(s["members"]), "، ".join(esc(m["name"]) for m in s["members"]))


def owners_text(name, price):
    return (f"🍽 <b>{esc(name)}</b> — {money(price)} {config.CURRENCY}\n"
            "این آیتم سهم چه کسانی است؟ (تیک بزن، بعد ✅ ثبت)")


def review_text(s, ex, ocr_total=None, note=""):
    lines = ["🧾 <b>این‌ها را از فیش خواندم:</b>", ""]
    for i, it in enumerate(ex["items"], 1):
        lines.append(f"{i}) {esc(it['name'])} — <b>{money(it['price'])}</b>")
    sub = subtotal_of(ex)
    lines += ["", f"🧮 جمع آیتم‌ها: <b>{money(sub)}</b> {config.CURRENCY}"]
    if ex.get("extra"):
        lines.append(f"➕ مالیات/سرویس خوانده‌شده: {money(ex['extra'])}")
    if ex.get("discount"):
        lines.append(f"➖ تخفیف خوانده‌شده: {money(ex['discount'])}")
    if ocr_total:
        lines.append(f"🖨 مبلغ کل روی فیش: {money(ocr_total)}")
        expected = sub + ex.get("extra", 0) - ex.get("discount", 0)
        if abs(ocr_total - expected) > max(1000, ocr_total * 0.02):
            lines.append("⚠️ جمع آیتم‌ها با مبلغ کل فیش نمی‌خواند؛ احتمالاً چیزی جا افتاده. با 🗑 حذف و ➕ افزودن اصلاح کن.")
    if note:
        lines += ["", note]
    lines += ["", "اگر ردیفی اشتباه است با 🗑 حذفش کن و با ➕ درستش را اضافه کن."]
    return "\n".join(lines)


def summary_text(s, ex):
    names = names_of(s)
    lines = ["🧾 <b>پیش‌نمایش فیش</b>", f"👤 پرداخت‌کننده: <b>{esc(names.get(ex['payer'], '؟'))}</b>", ""]
    if ex["mode"] == "equal":
        lines.append(f"⚖️ تقسیم مساوی بین {len(ex.get('members', []))} نفر")
    else:
        for it in ex["items"]:
            who = "، ".join(esc(names.get(o, "؟")) for o in it.get("owners", []))
            lines.append(f"• {esc(it['name'])}: {money(it['price'])} ← {who}")
    lines += ["", f"🧮 جمع آیتم‌ها: <b>{money(subtotal_of(ex))}</b>"]
    if ex.get("extra"):
        lines.append(f"➕ مالیات/سرویس: {money(ex['extra'])}")
    if ex.get("discount"):
        lines.append(f"➖ تخفیف: {money(ex['discount'])}")
    lines.append(f"💵 <b>مبلغ نهایی: {money(total_of(ex))} {config.CURRENCY}</b>")
    return "\n".join(lines)


def saved_text(s, ex):
    return (f"✅ <b>هزینه ثبت شد</b>\n"
            f"👤 {esc(names_of(s).get(ex['payer'], '؟'))} | "
            f"💵 {money(total_of(ex))} {config.CURRENCY}\n"
            f"📌 تعداد هزینه‌ها: {len(s['expenses'])}\n\n"
            f"➕ هزینهٔ بعدی: 💰 ثبت هزینه یا 📸 فیش با عکس\n📊 تسویه: محاسبه دنگ")


def status_text(s):
    msg = members_text(s) + "\n\n🧾 <b>هزینه‌ها:</b>\n"
    if not s["expenses"]:
        return msg + "هنوز هزینه‌ای ثبت نشده."
    names = names_of(s)
    for i, ex in enumerate(s["expenses"], 1):
        kind = "مساوی" if ex["mode"] == "equal" else f"{len(ex['items'])} آیتم"
        msg += f"{i}. {esc(names.get(ex['payer'], '؟'))} | {kind} | <b>{money(total_of(ex))}</b>\n"
    msg += "\nبرای حذف هر هزینه روی دکمه‌اش بزن 👇"
    return msg


def report_text(s):
    if not s["members"]:
        return "⚠️ هنوز عضوی ثبت نشده."
    if not s["expenses"]:
        return "⚠️ هنوز هزینه‌ای ثبت نشده."
    names = names_of(s)
    paid, share, net, total = compute(s["members"], s["expenses"])
    lines = ["📊 <b>گزارش تسویه حساب</b>",
             f"💵 کل هزینه‌ها: <b>{money(total)}</b> {config.CURRENCY}", "",
             "👤 <b>وضعیت هر نفر:</b>"]
    for m in s["members"]:
        i, n = m["id"], net[m["id"]]
        tag = "🟢 طلبکار" if n > 0 else ("🔴 بدهکار" if n < 0 else "⚪️ تسویه")
        lines.append(f"• {esc(m['name'])}: پرداخت {money(paid[i])} | سهم {money(share[i])} | {tag} {money(abs(n))}")
    lines.append("")
    tr = settle(net)
    if not tr:
        lines.append("✨ همه حساب‌ها تسویه است!")
    else:
        lines.append("🔁 <b>کمترین تراکنش‌های لازم:</b>")
        for k, (d, c, a) in enumerate(tr, 1):
            lines.append(f"{k}) {esc(names[d])} ⬅️ <b>{money(a)}</b> ➡️ {esc(names[c])}")
    return "\n".join(lines)


def finalize(s, ex):
    ex["id"] = new_id()
    ex["ts"] = time.time()
    s["expenses"].append(ex)
    s["state"] = None


def ask_owner(s, cid, ex, idx, mid=None):
    it = ex["items"][idx]
    sel = list(it.get("owners") or [])
    set_state(s, "ocr_owners", ex=ex, idx=idx, sel=sel)
    txt = (f"🍽 آیتم {idx + 1} از {len(ex['items'])}: <b>{esc(it['name'])}</b> — {money(it['price'])}\n"
           "سهم چه کسانی است؟")
    markup = kb.ocr_owner_kb(s["members"], sel)
    if mid is not None:
        edit(cid, mid, txt, markup)
    else:
        send(cid, txt, markup)


def after_extra(s, cid, ex):
    idx = next_unowned(ex)
    if idx is None:
        set_state(s, "summary", ex=ex)
        send(cid, summary_text(s, ex), kb.summary_kb())
    else:
        ask_owner(s, cid, ex, idx)


def start_owners(s, cid, st, name, price):
    set_state(s, "owners", ex=st["ex"], cur={"name": name[:40], "price": price}, sel=[])
    send(cid, owners_text(name, price), kb.select_kb(s["members"], [], "ow", "owall", "owok"))


# ---------- هندلرها ----------
@bot.message_handler(commands=["start", "help"])
@safe_msg
def cmd_start(m):
    with store.session(m.chat.id) as s:
        s["state"] = None
    send(m.chat.id, WELCOME, kb.main_menu())


@bot.message_handler(content_types=["text"])
@safe_msg
def on_text(m):
    cid, text = m.chat.id, (m.text or "").strip()
    with store.session(cid) as s:
        st = get_state(s)

        # --- منوی اصلی ---
        if text.lower() in CANCEL:
            s["state"] = None
            send(cid, "❌ لغو شد.", kb.main_menu())
            return
        if text == kb.BTN_NEW:
            send(cid, "⚠️ اعضا و هزینه‌های فعلی پاک می‌شوند. مطمئنی؟", kb.reset_kb())
            return
        if text == kb.BTN_MEMBERS:
            s["state"] = None
            send(cid, members_text(s), kb.members_kb(s["members"]))
            return
        if text == kb.BTN_STATUS:
            send(cid, status_text(s), kb.expenses_kb(s))
            return
        if text == kb.BTN_CALC:
            send(cid, report_text(s), kb.main_menu())
            return
        if text == kb.BTN_EXPENSE:
            if not s["members"]:
                send(cid, "⚠️ اول از 👥 اعضا حداقل یک نفر اضافه کن.", kb.main_menu())
                return
            s["state"] = None
            send(cid, "💸 چه کسی این هزینه را پرداخت کرده؟", kb.payer_kb(s["members"]))
            return
        if text == kb.BTN_PHOTO:
            if not s["members"]:
                send(cid, "⚠️ اول از 👥 اعضا حداقل یک نفر اضافه کن.", kb.main_menu())
                return
            set_state(s, "photo_wait")
            send(cid, PHOTO_ASK, kb.cancel_ikb())
            return
        if text.startswith("/") and text.lower() not in DONE:
            send(cid, "دستور ناشناخته. /help", kb.main_menu())
            return
        if not st:
            send(cid, "از دکمه‌های منو استفاده کن 👇", kb.main_menu())
            return

        step = st["step"]

        if step == "photo_wait":
            send(cid, "📸 منتظر عکس فیش هستم. عکس را بفرست یا ❌ لغو را بزن.")
            return

        if step == "m_add":
            added, dup = [], []
            for n in re.split(r"[,،\n]+", text):
                n = n.strip()[:30]
                if not n:
                    continue
                if any(x["name"] == n for x in s["members"]):
                    dup.append(n)
                    continue
                if len(s["members"]) >= config.MAX_MEMBERS:
                    break
                s["members"].append({"id": new_id(), "name": n})
                added.append(n)
            s["state"] = None
            msg = ("✅ اضافه شد: " + "، ".join(esc(a) for a in added)) if added else "چیزی اضافه نشد."
            if dup:
                msg += "\n⚠️ تکراری: " + "، ".join(esc(d) for d in dup)
            send(cid, msg + "\n\n" + members_text(s), kb.members_kb(s["members"]))
            return

        if step == "eq_amount":
            amt = parse_money(text)
            if not amt:
                send(cid, "❌ مبلغ معتبر نیست. مثال: 450000 یا ۴۵۰ هزار")
                return
            ex = st["ex"]
            ex["amount"] = amt
            ids = [m["id"] for m in s["members"]]
            set_state(s, "eq_members", ex=ex, sel=list(ids))
            send(cid, f"💰 مبلغ: <b>{money(amt)}</b>\nبین چه کسانی تقسیم شود؟",
                 kb.select_kb(s["members"], ids, "eq", "eqall", "eqok"))
            return

        if step == "item":
            if text.lower() in DONE:
                if not st["ex"]["items"]:
                    send(cid, "⚠️ هنوز آیتمی ثبت نشده. نام و قیمت اولین آیتم را بفرست.")
                    return
                set_state(s, "extra", ex=st["ex"])
                send(cid, extra_ask(), kb.cancel_ikb())
                return
            name, price = split_item(text)
            if not name:
                send(cid, "❌ نام آیتم خالی است.")
                return
            if price is None:
                set_state(s, "item_price", ex=st["ex"], cur={"name": name[:40]})
                send(cid, f"💵 قیمت <b>{esc(name)}</b> چقدر است؟", kb.cancel_ikb())
                return
            start_owners(s, cid, st, name, price)
            return

        if step == "item_price":
            price = parse_money(text)
            if price is None:
                send(cid, "❌ فقط عدد بفرست (مثلاً 300000 یا ۳۰۰ هزار).")
                return
            start_owners(s, cid, st, st["cur"]["name"], price)
            return

        if step == "extra":
            pct = parse_percent(text)
            sub = subtotal_of(st["ex"])
            if pct is not None:
                extra = int(round(sub * pct / 100))
            elif text.strip() in ("ندارد", "نه", "no", "-"):
                extra = 0
            else:
                extra = parse_money(text)
            if extra is None:
                send(cid, "❌ مبلغ یا درصد بفرست؛ اگر ندارد 0.")
                return
            ex = st["ex"]
            ex["extra"] = extra
            after_extra(s, cid, ex)
            return

        if step == "discount":
            d = parse_money(text)
            if d is None:
                send(cid, "❌ مبلغ تخفیف را عددی بفرست (یا 0).")
                return
            ex = st["ex"]
            ex["discount"] = min(d, subtotal_of(ex) + ex.get("extra", 0))
            set_state(s, "summary", ex=ex)
            send(cid, summary_text(s, ex), kb.summary_kb())
            return

        if step == "summary":
            if text.lower() in ("ثبت", "تایید", "ok", "بله"):
                finalize(s, st["ex"])
                send(cid, saved_text(s, s["expenses"][-1]), kb.main_menu())
            else:
                send(cid, "از دکمه‌های زیر پیام استفاده کن 👇", kb.summary_kb())
            return

        send(cid, "برای این مرحله از دکمه‌های روی پیام استفاده کن 🙂 (یا ❌ لغو)")


@bot.message_handler(content_types=["photo", "document"])
@safe_msg
def on_photo(m):
    cid = m.chat.id
    with store.session(cid) as s:
        if not s["members"]:
            send(cid, "⚠️ اول از 👥 اعضا حداقل یک نفر اضافه کن.", kb.main_menu())
            return
        st = get_state(s)

        if m.content_type == "document":
            mime = m.document.mime_type or ""
            if not mime.startswith("image/"):
                send(cid, "این فایل عکس نیست 🙂 فیش را به صورت «عکس» بفرست.", kb.main_menu())
                return
            file_id = m.document.file_id
        else:
            mime = "image/jpeg"
            file_id = m.photo[-1].file_id

        try:
            fi = bot.get_file(file_id)
            img = bot.download_file(fi.file_path)
        except ApiTelegramException as e:
            log.warning("download failed: %s", e)
            send(cid, "❌ دانلود عکس ناموفق بود؛ دوباره بفرست (به صورت عکس، نه فایل).")
            return

        busy = bot.send_message(cid, OCR_BUSY)
        try:
            res = ocr.read_receipt(img, mime)
        except Exception:
            log.exception("ocr crash")
            res = {"ok": False, "error": "خطای غیرمنتظره در خواندن فیش."}
        try:
            bot.delete_message(cid, busy.message_id)
        except ApiTelegramException:
            pass

        if not res["ok"]:
            set_state(s, "photo_wait")
            send(cid, "❌ " + res["error"], kb.cancel_ikb())
            return

        ex = None
        if st and st.get("ex") and st["step"] in ("item", "item_price", "owners", "extra", "summary", "mode"):
            ex = st["ex"]
            ex["mode"] = "items"
            ex["items"] = (ex.get("items") or []) + res["items"]
        if ex is None:
            ex = {"mode": "items", "payer": None, "items": res["items"], "extra": 0, "discount": 0}
        if res["extra"]:
            ex["extra"] = res["extra"]
        if res["discount"]:
            ex["discount"] = res["discount"]

        set_state(s, "ocr_review", ex=ex, ocr_total=res["total"], note=res["note"])
        send(cid, review_text(s, ex, res["total"], res["note"]), kb.ocr_review_kb(ex["items"]))


@bot.callback_query_handler(func=lambda c: True)
@safe_cb
def on_cb(c):
    cid, mid, d = c.message.chat.id, c.message.message_id, (c.data or "")
    with store.session(cid) as s:
        st = get_state(s)

        if d == "noop":
            answer(c.id)
            return
        if d == "x":
            s["state"] = None
            answer(c.id, "لغو شد")
            edit(cid, mid, "❌ لغو شد.")
            return
        if d == "add":
            set_state(s, "m_add")
            answer(c.id)
            edit(cid, mid, "✍️ نام عضو را بفرست.\nچند نفر یکجا: <b>علی، رضا، سارا</b>", kb.cancel_ikb())
            return
        if d.startswith("del:"):
            uid = d[4:]
            used = any(ex["payer"] == uid
                       or uid in ex.get("members", [])
                       or any(uid in it.get("owners", []) for it in ex.get("items", []))
                       for ex in s["expenses"])
            if used:
                answer(c.id, "این عضو در هزینه‌ها ثبت شده؛ اول هزینه‌هایش را از 📋 وضعیت حذف کن.", True)
                return
            s["members"] = [m for m in s["members"] if m["id"] != uid]
            answer(c.id, "حذف شد 🗑")
            edit(cid, mid, members_text(s), kb.members_kb(s["members"]))
            return
        if d == "r:yes":
            s.update({"members": [], "expenses": [], "state": None})
            answer(c.id)
            edit(cid, mid, "✅ دورهمی جدید ساخته شد. از 👥 اعضا شروع کن.")
            return
        if d.startswith("rm:"):
            eid = d[3:]
            n = len(s["expenses"])
            s["expenses"] = [e for e in s["expenses"] if e["id"] != eid]
            answer(c.id, "حذف شد 🗑" if len(s["expenses"]) < n else "پیدا نشد")
            edit(cid, mid, status_text(s), kb.expenses_kb(s))
            return
        if d.startswith("pay:"):
            pid = d[4:]
            if pid not in names_of(s):
                answer(c.id, "عضو یافت نشد", True)
                return
            if st and st["step"] == "ocr_payer":
                ex = st["ex"]
                ex["payer"] = pid
                set_state(s, "extra", ex=ex)
                answer(c.id)
                edit(cid, mid, f"👤 پرداخت‌کننده: <b>{esc(names_of(s)[pid])}</b>\n\n" + extra_ask(ex.get("extra")),
                     kb.cancel_ikb())
                return
            ex = {"mode": None, "payer": pid, "items": [], "extra": 0, "discount": 0}
            set_state(s, "mode", ex=ex)
            answer(c.id)
            edit(cid, mid, f"👤 پرداخت‌کننده: <b>{esc(names_of(s)[pid])}</b>\n\nنوع هزینه را انتخاب کن:", kb.mode_kb())
            return
        if d in ("md:i", "md:e"):
            if not st or st["step"] != "mode":
                answer(c.id, "این دکمه منقضی شده؛ از منو شروع کن.", True)
                return
            ex = st["ex"]
            if d == "md:i":
                ex["mode"] = "items"
                set_state(s, "item", ex=ex)
                edit(cid, mid, ITEM_ASK, kb.cancel_ikb())
            else:
                ex["mode"] = "equal"
                set_state(s, "eq_amount", ex=ex)
                edit(cid, mid, "💰 مبلغ کل را بفرست (مثلاً 450000 یا ۴۵۰ هزار).", kb.cancel_ikb())
            answer(c.id)
            return
        if d == "fin":
            if not st or st["step"] != "item" or not st["ex"]["items"]:
                answer(c.id, "اول حداقل یک آیتم را ثبت کن.", True)
                return
            set_state(s, "extra", ex=st["ex"])
            answer(c.id)
            edit(cid, mid, extra_ask(), kb.cancel_ikb())
            return

        # --- بازبینی فیش خوانده‌شده از عکس ---
        if d in ("ocr:ok", "ocr:add") or d.startswith("ocr:rm:"):
            if not st or st["step"] != "ocr_review":
                answer(c.id, "این دکمه منقضی شده؛ از منو شروع کن.", True)
                return
            ex = st["ex"]
            if d.startswith("ocr:rm:"):
                iid = d[7:]
                ex["items"] = [it for it in ex["items"] if it.get("id") != iid]
                if not ex["items"]:
                    set_state(s, "photo_wait")
                    answer(c.id, "همه حذف شدند")
                    edit(cid, mid, "🗑 همهٔ آیتم‌ها حذف شدند.\n📸 عکس واضح‌تر بفرست یا 💰 ثبت هزینه را بزن.", kb.cancel_ikb())
                    return
                st["ts"] = time.time()
                answer(c.id, "حذف شد 🗑")
                edit(cid, mid, review_text(s, ex, st.get("ocr_total"), st.get("note", "")), kb.ocr_review_kb(ex["items"]))
                return
            if d == "ocr:add":
                set_state(s, "item", ex=ex)
                answer(c.id)
                edit(cid, mid, "✍️ نام و قیمت آیتم را بفرست (مثلاً «نوشابه 45000»).\nآیتم‌های قبلی حفظ شدند.\nوقتی تمام شد <b>تمام</b> را بفرست.", kb.next_kb())
                return
            if not ex["items"]:
                answer(c.id, "آیتمی نمانده!", True)
                return
            if ex.get("payer"):
                set_state(s, "extra", ex=ex)
                answer(c.id)
                edit(cid, mid, extra_ask(ex.get("extra")), kb.cancel_ikb())
            else:
                set_state(s, "ocr_payer", ex=ex)
                answer(c.id)
                edit(cid, mid, "💸 چه کسی این فیش را پرداخت کرده؟", kb.payer_kb(s["members"]))
            return

        if d == "ooskip":
            if not st or st["step"] != "ocr_owners":
                answer(c.id, "این دکمه منقضی شده.", True)
                return
            ex = st["ex"]
            ids = [m["id"] for m in s["members"]]
            for it in ex["items"]:
                if not it.get("owners"):
                    it["owners"] = list(ids)
            set_state(s, "summary", ex=ex)
            answer(c.id)
            edit(cid, mid, summary_text(s, ex), kb.summary_kb())
            return

        # --- انتخاب افراد: ow (آیتم دستی) / eq (مساوی) / oo (آیتم‌های عکس) ---
        for prefix, step, ok in (("ow", "owners", "owok"), ("eq", "eq_members", "eqok"), ("oo", "ocr_owners", "oook")):
            if d.startswith(prefix + ":") or d in (prefix + "all", ok):
                if not st or st["step"] != step:
                    answer(c.id, "این دکمه منقضی شده؛ از منو شروع کن.", True)
                    return
                ids = [m["id"] for m in s["members"]]
                if d.startswith(prefix + ":"):
                    toggle(st["sel"], d[len(prefix) + 1:])
                    st["ts"] = time.time()
                    if prefix == "oo":
                        markup = kb.ocr_owner_kb(s["members"], st["sel"])
                    else:
                        markup = kb.select_kb(s["members"], st["sel"], prefix, prefix + "all", ok)
                    edit_kb(cid, mid, markup)
                    answer(c.id)
                    return
                if d == prefix + "all":
                    st["sel"] = [] if len(st["sel"]) == len(ids) else list(ids)
                    st["ts"] = time.time()
                    if prefix == "oo":
                        markup = kb.ocr_owner_kb(s["members"], st["sel"])
                    else:
                        markup = kb.select_kb(s["members"], st["sel"], prefix, prefix + "all", ok)
                    edit_kb(cid, mid, markup)
                    answer(c.id)
                    return
                if not st["sel"]:
                    answer(c.id, "حداقل یک نفر را انتخاب کن!", True)
                    return
                ex = st["ex"]
                if prefix == "ow":
                    ex["items"].append({"id": new_id(), "name": st["cur"]["name"],
                                        "price": st["cur"]["price"], "owners": list(st["sel"])})
                    set_state(s, "item", ex=ex)
                    answer(c.id, "ثبت شد ✅")
                    edit(cid, mid, f"✅ <b>{esc(st['cur']['name'])}</b> ثبت شد.\n\n➕ آیتم بعدی را بفرست (نام + قیمت)\nیا 🧾 پایان را بزن.", kb.next_kb())
                elif prefix == "oo":
                    idx = st["idx"]
                    ex["items"][idx]["owners"] = list(st["sel"])
                    nxt = next_unowned(ex, idx + 1)
                    answer(c.id, "ثبت شد ✅")
                    if nxt is None:
                        set_state(s, "summary", ex=ex)
                        edit(cid, mid, summary_text(s, ex), kb.summary_kb())
                    else:
                        ask_owner(s, cid, ex, nxt, mid=mid)
                else:
                    ex["members"] = list(st["sel"])
                    finalize(s, ex)
                    answer(c.id, "ثبت شد ✅")
                    edit(cid, mid, saved_text(s, ex))
                return

        if d in ("s:ok", "s:disc"):
            if not st or st["step"] != "summary":
                answer(c.id, "این دکمه منقضی شده؛ از منو شروع کن.", True)
                return
            if d == "s:ok":
                ex = st["ex"]
                finalize(s, ex)
                answer(c.id, "ثبت شد ✅")
                edit(cid, mid, saved_text(s, ex))
            else:
                set_state(s, "discount", ex=st["ex"])
                answer(c.id)
                edit(cid, mid, "🏷 مبلغ تخفیف را بفرست (مثلاً 50000).", kb.cancel_ikb())
            return

        answer(c.id, "دکمه نامعتبر", True)


@bot.message_handler(content_types=["sticker", "voice", "video", "audio", "animation", "location", "contact"])
@safe_msg
def on_other(m):
    send(m.chat.id, "فعلاً فقط متن و عکس فیش می‌فهمم 🙂 از دکمه‌های منو استفاده کن.", kb.main_menu())
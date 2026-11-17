from telebot import types

from utils import money
from calc import total_of

BTN_NEW = "🆕 دورهمی جدید"
BTN_MEMBERS = "👥 اعضا"
BTN_EXPENSE = "💰 ثبت هزینه"
BTN_PHOTO = "📸 فیش با عکس"
BTN_CALC = "📊 محاسبه دنگ"
BTN_STATUS = "📋 وضعیت"
BTN_CANCEL = "❌ لغو"


def main_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.row(BTN_EXPENSE, BTN_PHOTO)
    m.row(BTN_CALC, BTN_MEMBERS)
    m.row(BTN_STATUS, BTN_NEW)
    m.row(BTN_CANCEL)
    return m


def ikb(rows):
    m = types.InlineKeyboardMarkup()
    for row in rows:
        m.row(*[types.InlineKeyboardButton(t, callback_data=d)
              for t, d in row])
    return m


def cancel_ikb():
    return ikb([[("❌ لغو", "x")]])


def next_kb():
    return ikb([
        [("🧾 پایان ثبت آیتم‌ها", "fin")],
        [("✏️ بازبینی / حذف آیتم‌ها", "rev")],
        [("❌ لغو", "x")],
    ])


def mode_kb():
    return ikb([
        [("⚖️ تقسیم مساوی (سریع)", "md:e")],
        [("🍽 فیش تفکیکی (هرکس چی خورد)", "md:i")],
        [("❌ لغو", "x")],
    ])


def payer_kb(members):
    rows = [[(f"👤 {m['name']}", f"pay:{m['id']}") for m in members[i:i + 2]]
            for i in range(0, len(members), 2)]
    rows.append([("❌ لغو", "x")])
    return ikb(rows)


def members_kb(members):
    rows = [[(f"👤 {m['name']}", "noop"), ("🗑 حذف", f"del:{m['id']}")]
            for m in members]
    rows.append([("➕ افزودن عضو", "add")])
    return ikb(rows)


def select_kb(members, selected, prefix, all_cb, ok_cb):
    rows = []
    for i in range(0, len(members), 2):
        row = []
        for m in members[i:i + 2]:
            icon = "✅" if m["id"] in selected else "⬜"
            row.append((f"{icon} {m['name']}", f"{prefix}:{m['id']}"))
        rows.append(row)
    rows.append([("👥 همه / هیچ", all_cb)])
    rows.append([("✅ ثبت", ok_cb), ("❌ لغو", "x")])
    return ikb(rows)


def ocr_review_kb(items):
    rows = []
    for i, it in enumerate(items, 1):
        rows.append(
            [(f"🗑 {i}. {it['name']} — {money(it['price'])}", f"ocr:rm:{it['id']}")])
    rows.append([("➕ افزودن / اصلاح دستی", "ocr:add")])
    rows.append([("✅ درسته، ادامه", "ocr:ok"), ("❌ لغو", "x")])
    return ikb(rows)


def ocr_owner_kb(members, selected):
    m = select_kb(members, selected, "oo", "ooall", "oook")
    m.row(types.InlineKeyboardButton(
        "👥 همهٔ آیتم‌ها بین همه مساوی", callback_data="ooskip"))
    return m


def summary_kb():
    return ikb([
        [("✅ ثبت نهایی", "s:ok")],
        [("✏️ ویرایش آیتم‌ها", "s:edit"), ("👤 تغییر پرداخت‌کننده", "s:payer")],
        [("🧾 مالیات/سرویس", "s:extra"), ("🏷 تخفیف", "s:disc")],
        [("❌ لغو", "x")],
    ])


def reset_kb():
    return ikb([[("✅ بله، پاک کن", "r:yes"), ("❌ نه", "x")]])


def expenses_kb(s):
    if not s["expenses"]:
        return None
    names = {m["id"]: m["name"] for m in s["members"]}
    rows = []
    for i, ex in enumerate(s["expenses"][-15:], 1):
        label = f"✏️ {i}. {names.get(ex['payer'], '؟')} — {money(total_of(ex))}"
        rows.append([(label, f"ed:{ex['id']}"), ("🗑", f"rm:{ex['id']}")])
    return ikb(rows)

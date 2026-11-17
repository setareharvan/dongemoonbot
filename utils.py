import re
import html
import uuid

# تبدیل ارقام فارسی/عربی به انگلیسی + جداکننده‌ها
_TRANS = {}
for i, ch in enumerate("۰۱۲۳۴۵۶۷۸۹"):
    _TRANS[ord(ch)] = str(i)
for i, ch in enumerate("٠١٢٣٤٥٦٧٨٩"):
    _TRANS[ord(ch)] = str(i)
_TRANS[ord("٫")] = "."
_TRANS[ord("٬")] = ""
_TRANS[ord("،")] = ","


def to_en(s) -> str:
    return str(s).translate(_TRANS)


_UNITS = [("میلیون", 1_000_000), ("ميليون", 1_000_000), ("هزار", 1000), ("k", 1000), ("m", 1_000_000)]


def parse_money(raw):
    """۳۰۰,۰۰۰ / 300 هزار / 1.5میلیون / 300000 تومان -> int (None اگر نامعتبر)"""
    if raw is None:
        return None
    s = to_en(raw).strip().lower().replace(" ", "").replace("\u200c", "").replace(",", "")
    for w in ("تومان", "تومن", "تومانی", "ریال", "ريال"):
        s = s.replace(w, "")
    mult = 1
    for suffix, val in _UNITS:
        if s.endswith(suffix):
            s, mult = s[: -len(suffix)], val
            break
    if not re.fullmatch(r"\d+(\.\d+)?", s.strip()):
        return None
    return int(round(float(s) * mult))


def parse_percent(raw):
    s = to_en(raw).strip().replace("٪", "%").replace(" ", "")
    if s.endswith("%"):
        try:
            v = float(s[:-1])
            return v if 0 <= v <= 100 else None
        except ValueError:
            return None
    return None


def money(n) -> str:
    return f"{int(n):,}"


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def split_item(text):
    """'جوجه 300000' یا 'جوجه، ۳۰۰ هزار' -> (نام، قیمت) ؛ قیمت None اگر نبود"""
    t = to_en(text).strip()
    for sep in (",", ":", "|"):
        if sep in t:
            a, b = t.rsplit(sep, 1)
            p = parse_money(b)
            if p is not None:
                return a.strip(), p
    parts = t.rsplit(" ", 1)
    if len(parts) == 2:
        p = parse_money(parts[1])
        if p is not None:
            return parts[0].strip(), p
    return t, None


def split_text(text, limit=3500):
    """تلگرام پیام > 4096 را رد می‌کند؛ بر اساس خط می‌شکنیم تا HTML خراب نشود."""
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            out.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        out.append(buf)
    return out or [""]
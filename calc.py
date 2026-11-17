from fractions import Fraction


def subtotal_of(ex):
    if ex.get("mode") == "equal":
        return int(ex.get("amount", 0))
    return int(sum(i["price"] for i in ex.get("items", [])))


def total_of(ex):
    return subtotal_of(ex) + int(ex.get("extra", 0)) - int(ex.get("discount", 0))


def _weights(ex):
    """وزن هر نفر = مجموع سهمش از آیتم‌ها (دقیق، کسری)."""
    w = {}
    if ex.get("mode") == "equal":
        for m in ex.get("members", []):
            w[m] = w.get(m, Fraction(0)) + 1
    else:
        for it in ex.get("items", []):
            owners = it.get("owners") or []
            if not owners or it["price"] <= 0:
                continue
            part = Fraction(int(it["price"]), len(owners))
            for o in owners:
                w[o] = w.get(o, Fraction(0)) + part
    return w


def apportion(total, weights):
    """تسهیم عدد صحیح با روش بزرگ‌ترین باقیمانده: جمع سهم‌ها دقیقاً = total."""
    keys = [k for k, w in weights.items() if w > 0]
    out = {k: 0 for k in weights}
    if total <= 0 or not keys:
        return out
    s = sum(Fraction(weights[k]) for k in keys)
    raw = {k: Fraction(total) * Fraction(weights[k]) / s for k in keys}
    rest = total
    for k in keys:
        f = raw[k].numerator // raw[k].denominator
        out[k] = f
        rest -= f
    for k in sorted(keys, key=lambda k: (-(raw[k] - out[k]), k))[:rest]:
        out[k] += 1
    return out


def shares_of(ex):
    """سهم نهایی هر نفر: غذا + (مالیات/سرویس به نسبت غذا) - (تخفیف به نسبت غذا)."""
    w = _weights(ex)
    base = apportion(subtotal_of(ex), w)
    extra = apportion(int(ex.get("extra", 0)), w)
    disc = apportion(int(ex.get("discount", 0)), w)
    return {k: base.get(k, 0) + extra.get(k, 0) - disc.get(k, 0) for k in w}


def compute(members, expenses):
    ids = [m["id"] for m in members]
    paid = {i: 0 for i in ids}
    share = {i: 0 for i in ids}
    total = 0
    for ex in expenses:
        t = total_of(ex)
        total += t
        if ex["payer"] in paid:
            paid[ex["payer"]] += t
        for k, v in shares_of(ex).items():
            share[k] = share.get(k, 0) + v
    net = {i: paid[i] - share.get(i, 0) for i in ids}
    return paid, share, net, total


def settle(net):
    """حریصانه: بزرگ‌ترین بدهکار به بزرگ‌ترین طلبکار → حداکثر n-1 تراکنش."""
    debtors = sorted([(k, -v) for k, v in net.items() if v < 0], key=lambda x: -x[1])
    creditors = sorted([(k, v) for k, v in net.items() if v > 0], key=lambda x: -x[1])
    res, i, j = [], 0, 0
    while i < len(debtors) and j < len(creditors):
        d, da = debtors[i]
        c, ca = creditors[j]
        amt = min(da, ca)
        if amt > 0:
            res.append((d, c, amt))
        debtors[i], creditors[j] = (d, da - amt), (c, ca - amt)
        if da - amt == 0:
            i += 1
        if ca - amt == 0:
            j += 1
    return res
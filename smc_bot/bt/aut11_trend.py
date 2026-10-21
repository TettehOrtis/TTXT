"""AUT11 - daily trend following on 24 FX pairs (main) + gold (secondary). Autopsy agent AUT11.

Governing documents: bt/AUT11_14_BRIEF.md, bt/AUTOPSY_BRIEF.md, bt/AUTOPSY_SCHEMA.md.
Writes ONLY results/AUT11_trend/. Edits nothing shared. EXAM is never read (autopsy_lib.gate refuses it).

WHY THERE IS A DAILY ENGINE AND NOT common.simulate
  common.simulate() flattens every position before Friday 20:45 UTC and before any gap over 6 hours. A trend trade
  that is meant to last weeks would be chopped into one-week pieces, each paying the round trip again. So this
  module walks D1 bars itself. The cost arithmetic is a copy of simulate(): a buy fills at open + spread, a sell
  at the bid, a stop slips 0.2 pip, commission COMM[sym] pips is taken ONCE at close, gross = (exit-fill)*dir/pip.
  The engine is cross-checked against common.simulate on short trades that never touch a weekend (cmd `xcheck`).
  Cost is paid once, inside walk(). Nothing is subtracted anywhere else.

DAY BOUNDARY: 22:00 UTC rollover (see aut11_d1build.py for the why). Signal = bar CLOSE; fill = OPEN of the next bar.
"""
import os
import sys
import json
import time
import warnings

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import common as C                                                        # noqa: E402
import autopsy_lib as A                                                   # noqa: E402

warnings.simplefilter("ignore")
OUT = os.path.join(C.RESULTS, "AUT11_trend")
D1DIR = os.path.join(OUT, "_d1")
os.makedirs(OUT, exist_ok=True)

FX = [s for s in C.PAIRS if s != "XAUUSD"]
SYMS = FX + ["XAUUSD"]
ATR_N = 14
TRAIL_MULT = 3.0                      # fixed by the brief: 3 x daily ATR
CLOSE_H = 22                          # D1 bar `day` closes at day 22:00 UTC

# rules fixed up front (brief): 3 return-sign, 2 Donchian, 1 MA cross
RULES = {"TS20": ("ts", 20), "TS60": ("ts", 60), "TS120": ("ts", 120),
         "DC20": ("dc", 20), "DC55": ("dc", 55), "MA20_100": ("ma", (20, 100))}
EXITS = ("OPP", "TRAIL")             # OPP = stop-and-reverse on the opposite signal; TRAIL = 3 x ATR trail OR opposite signal


# ================================================================ data
_A = {}


class Arr:
    pass


def load(sym):
    if sym in _A:
        return _A[sym]
    d = pd.read_parquet(os.path.join(D1DIR, sym + ".parquet"))
    a = Arr()
    a.sym = sym
    a.df = d
    a.day = d.day.values.astype("datetime64[ns]")
    a.tclose = a.day + np.timedelta64(CLOSE_H, "h")
    a.o, a.h, a.l, a.c = (d[k].values.astype(float) for k in ("open", "high", "low", "close"))
    a.spr = d.spr.values.astype(float)
    a.tfirst = d.t_first.values.astype("datetime64[ns]")
    a.P = C.pip(sym)
    a.comm = C.COMM[sym]
    a.usd = C.USD_PIP_01[sym]
    n = len(d)
    a.n = n
    pc = np.r_[np.nan, a.c[:-1]]
    tr = np.nanmax(np.vstack([a.h - a.l, np.abs(a.h - pc), np.abs(a.l - pc)]), axis=0)
    a.atr = pd.Series(tr).ewm(alpha=1.0 / ATR_N, adjust=False, min_periods=ATR_N).mean().values
    cs = pd.Series(a.c)
    a.sma = {k: cs.rolling(k, min_periods=k).mean().values for k in (20, 50, 100)}
    a.ret = {k: a.c - cs.shift(k).values for k in (20, 60, 120, 250, 100)}
    hs, ls = pd.Series(a.h), pd.Series(a.l)
    a.hh = {k: hs.shift(1).rolling(k, min_periods=k).max().values for k in (20, 55, 250)}
    a.ll = {k: ls.shift(1).rolling(k, min_periods=k).min().values for k in (20, 55, 250)}
    # ---- features (all known at the CLOSE of bar i)
    at = a.atr
    w = 252
    rk = np.full(n, np.nan)
    if n > w:
        sw = np.lib.stride_tricks.sliding_window_view(at, w)           # row r = bars r..r+w-1
        rk[w - 1:] = (sw[:, :-1] < sw[:, -1:]).mean(axis=1)
    a.atr_rank = rk
    a.atr_exp = at / pd.Series(at).shift(20).values
    dif = pd.Series(np.abs(np.diff(a.c, prepend=np.nan)))
    a.er20 = np.abs(a.ret[20]) / dif.rolling(20, min_periods=20).sum().values
    a.ext50 = (a.c - a.sma[50]) / at                                   # signed; multiply by direction
    a.big = np.abs(a.c - pc) / at
    a.room_l = (a.hh[250] - a.c) / at                                  # long: untouched room up to the 250-day high
    a.room_s = (a.c - a.ll[250]) / at
    rng = a.h - a.l
    a.body = np.abs(a.c - a.o) / at
    a.closepos = np.where(rng > 0, (a.c - a.l) / np.where(rng > 0, rng, 1), np.nan)
    a.cost_atr = (a.comm * a.P + a.spr) / at                           # round trip as a share of ONE daily ATR
    a.s250 = np.sign(np.nan_to_num(a.ret[250]))
    a.s60 = np.sign(np.nan_to_num(a.ret[60]))
    s100 = np.sign(np.nan_to_num(a.ret[100]))
    age = np.zeros(n)
    for i in range(1, n):
        age[i] = age[i - 1] + 1 if s100[i] == s100[i - 1] else 0
    a.age = age
    a.dow = pd.to_datetime(a.day).dayofweek.values
    a.monthend = ((pd.to_datetime(a.day).day.values >= 27) | (pd.to_datetime(a.day).day.values <= 2)).astype(float)
    a.is_jpy = float("JPY" in sym)
    a.is_gold = float(sym == "XAUUSD")
    # holes in the D1 history (AUDJPY 2005/2012/2013, EURAUD 2008, GBPCHF 2008): a gap of more than 6 calendar days ends any open
    # trade at the last bar before it (same idea as simulate's >6 h flatten) and mutes signals for 260 bars after it, because
    # every moving window that straddles the hole is stale.
    gapd = np.r_[0, np.diff(a.day).astype("timedelta64[D]").astype(int)]
    big = np.flatnonzero(gapd > 6)
    seg_end = np.arange(n)
    starts = np.r_[0, big]
    ends = np.r_[big - 1, n - 1]
    for s_, e_ in zip(starts, ends):
        seg_end[s_:e_ + 1] = e_
    a.seg_end = seg_end
    a.gapd = gapd
    unsafe = np.zeros(n, bool)
    for b_ in big:
        unsafe[b_:b_ + 260] = True
    a.unsafe = unsafe
    a._ev = {}
    _A[sym] = a
    return a


def events(a, rule):
    """+1 / -1 / 0 per bar, decided on that bar's CLOSE."""
    if rule in a._ev:
        return a._ev[rule]
    kind, N = RULES[rule]
    n = a.n
    if kind in ("ts", "ma"):
        if kind == "ts":
            r = a.ret[N]
        else:
            r = a.sma[N[0]] - a.sma[N[1]]
        s = np.sign(np.nan_to_num(r))
        prev = np.r_[0.0, s[:-1]]
        ev = np.where((s != prev) & (s != 0), s, 0.0)
    else:
        up = a.c > a.hh[N]
        dn = a.c < a.ll[N]
        ev = np.where(up, 1.0, np.where(dn, -1.0, 0.0))
        ev[np.isnan(a.hh[N])] = 0.0
    ev = ev.astype(int)
    ev[a.unsafe] = 0
    a._ev[rule] = ev
    return ev


FEATS = ["agree250", "agree60", "age", "er20", "atr_rank", "atr_exp", "ext50", "big", "room", "body", "closepos_dir",
         "cost_atr", "monday", "monthend", "is_jpy", "is_gold", "long"]


def feat(a, name, d):
    """Feature array over all bars for direction d. Direction-aware where it must be."""
    if name == "agree250":
        return (a.s250 == d).astype(float)
    if name == "agree60":
        return (a.s60 == d).astype(float)
    if name == "age":
        return a.age
    if name == "er20":
        return a.er20
    if name == "atr_rank":
        return a.atr_rank
    if name == "atr_exp":
        return a.atr_exp
    if name == "ext50":
        return a.ext50 * d
    if name == "big":
        return a.big
    if name == "room":
        return a.room_l if d == 1 else a.room_s
    if name == "body":
        return a.body
    if name == "closepos_dir":
        return a.closepos if d == 1 else 1 - a.closepos
    if name == "cost_atr":
        return a.cost_atr
    if name == "monday":
        return (a.dow == 0).astype(float)
    if name == "monthend":
        return a.monthend
    if name == "is_jpy":
        return np.full(a.n, a.is_jpy)
    if name == "is_gold":
        return np.full(a.n, a.is_gold)
    if name == "long":
        return np.full(a.n, 1.0 if d == 1 else 0.0)
    raise KeyError(name)


def stage_idx(a, stage):
    A.gate(stage)
    s, e = A.STAGES[stage]
    i0 = int(np.searchsorted(a.tclose, np.datetime64(s), "left"))
    i1 = int(np.searchsorted(a.tclose, np.datetime64(e), "left"))     # exclusive
    return i0, i1


# ================================================================ the walker (cost paid once, here)
def walk(a, f, d, stop0, x_open, last, trailing, smult=1.0, slipx=0.0, label_x="OPP"):
    """One trade. f = fill bar; stop0 = initial stop price (anchored on the SIGNAL close; NaN = none);
    x_open = bar whose OPEN ends the trade (None = hold to `last` and close at its close);
    trailing = ratchet 3 x ATR chandelier stop; smult scales the spread; slipx = extra adverse pips on each fill.
    Returns dict with j (exit bar), px, reason, fill, net_pips, mfe/mae in price."""
    P = a.P
    slip = C.STOP_SLIP * P
    ex = slipx * P
    o, h, l, c, sp = a.o, a.h, a.l, a.c, a.spr
    fill = o[f] + (sp[f] * smult + ex if d == 1 else -ex)
    e = last if x_open is None else min(x_open - 1, last)
    reason, j, px = None, None, None
    if np.isfinite(stop0):
        if (fill - stop0) * d <= 0:                                    # gapped through the stop on the fill
            j, reason = f, "GAP_SL"
            px = fill - d * slip
        else:
            seg_h, seg_l = h[f:e + 1], l[f:e + 1]
            if trailing:
                at = a.atr[f:e + 1]
                if d == 1:
                    cand = np.maximum.accumulate(seg_h) - TRAIL_MULT * at
                    stl = np.maximum.accumulate(cand)
                    st = np.empty_like(stl)
                    st[0] = stop0
                    st[1:] = np.maximum(stop0, stl[:-1])
                    hit = seg_l <= st
                else:
                    cand = np.minimum.accumulate(seg_l) + TRAIL_MULT * at
                    stl = np.minimum.accumulate(cand)
                    st = np.empty_like(stl)
                    st[0] = stop0
                    st[1:] = np.minimum(stop0, stl[:-1])
                    hit = seg_h + sp[f:e + 1] * smult >= st
            else:
                st = np.full(e - f + 1, stop0)
                hit = (seg_l <= st) if d == 1 else (seg_h + sp[f:e + 1] * smult >= st)
            if hit.any():
                k = int(np.argmax(hit))
                j, reason = f + k, "SL"
                if d == 1:
                    px = (min(st[k], o[j]) if j > f else st[k]) - slip - ex
                else:
                    px = (max(st[k], o[j] + sp[j] * smult) if j > f else st[k]) + slip + ex
    if reason is None:
        if x_open is not None and x_open <= last:
            j, reason = x_open, label_x
            px = o[j] - ex if d == 1 else o[j] + sp[j] * smult + ex
        else:
            j, reason = last, "END"
            px = c[j] - ex if d == 1 else c[j] + sp[j] * smult + ex
    sh, sl_ = h[f:j + 1], l[f:j + 1]
    gross = (px - fill) * d / P
    net = gross - a.comm
    spr_paid = (sp[f] if d == 1 else sp[j]) * smult / P            # the one spread of the round trip, in pips
    return dict(j=j, px=px, reason=reason, fill=fill, gross=gross, net=net, spr_paid=spr_paid,
                mfe_p=((sh.max() - fill) if d == 1 else (fill - (sl_ + sp[f:j + 1] * smult).min())),
                mae_p=((fill - sl_.min()) if d == 1 else ((sh + sp[f:j + 1] * smult).max() - fill)))


def make_row(a, rule, mode, i, f, d, w, stop0, riskp, stage, extra=None):
    j = w["j"]
    row = dict(sym=a.sym, rule=rule, exit_mode=mode, t_signal=pd.Timestamp(a.tclose[i]), side=int(d),
               entry_type="market", entry=np.nan, expire=pd.NaT, sl=stop0, tp=np.nan, be_at_r=np.nan,
               max_hold_min=np.nan, status="filled",
               t_fill=pd.Timestamp(a.tfirst[f]), fill=w["fill"],
               t_exit=pd.Timestamp(a.tfirst[j]) if w["reason"] in ("OPP", "TIME") else pd.Timestamp(a.tclose[j]),
               exit=w["px"], exit_reason=w["reason"],
               hold_h=0.0, risk_pips=riskp / a.P, gross_pips=w["gross"], net_pips=w["net"],
               R=w["net"] / (riskp / a.P), usd_01=w["net"] * a.usd, gross_usd=w["gross"] * a.usd, spread_pips=w["spr_paid"], comm_pips=a.comm,
               cost_usd=(a.comm + w["spr_paid"]) * a.usd,
               mfe_R=w["mfe_p"] / riskp, mae_R=w["mae_p"] / riskp,
               i_sig=i, i_fill=f, i_exit=j,
               nights=int((a.day[j] - a.day[f]) / np.timedelta64(1, "D")),
               period=stage)
    row["hold_h"] = (row["t_exit"] - row["t_fill"]).total_seconds() / 3600.0
    row["edge_before_cost_usd"] = row["usd_01"] + row["cost_usd"]
    row["max_gap_days"] = int(a.gapd[f:j + 1].max()) if j > f else 1
    # features known at the signal close (direction aware)
    for nme in FEATS:
        row["f_" + nme] = feat_at(a, nme, d, i)
    row["f_gap_atr"] = (a.o[f] - a.c[i]) * d / a.atr[i]                # known only at the fill: a diagnostic, not a filter
    if extra:
        row.update(extra)
    return row


_FC = {}


def feat_at(a, name, d, i):
    k = (a.sym, name, d)
    if k not in _FC:
        _FC[k] = feat(a, name, d)
    return _FC[k][i]


def run_rule(rule, mode, stage, syms=None, entry_ok=None, smult=1.0, slipx=0.0, twin=False):
    """Trades of one rule/exit pair in one stage. entry_ok: callable(a, d) -> bool array over bars (entry gate only;
    exits still follow the unfiltered opposite signal). twin=True is the LOOK-AHEAD TWIN: fills at the signal bar's own
    open and exits at the exit-signal bar's own open."""
    rows = []
    for sym in (syms or SYMS):
        a = load(sym)
        i0, i1 = stage_idx(a, stage)
        if i1 - i0 < 5:
            continue
        ev = events(a, rule)
        evi = np.flatnonzero(ev[i0:i1] != 0) + i0
        if len(evi) == 0:
            continue
        ok = {1: entry_ok(a, 1) if entry_ok else None, -1: entry_ok(a, -1) if entry_ok else None}
        last = i1 - 1
        nxt = i0
        p = 0
        evs = ev[evi]
        while p < len(evi):
            i = int(evi[p])
            if i < nxt:
                p += 1
                continue
            d = int(evs[p])
            if ok[d] is not None and not ok[d][i]:
                p += 1
                continue
            f = i if twin else i + 1
            if f > last - 1 or not np.isfinite(a.atr[i]):
                break
            last_eff = min(last, int(a.seg_end[f]))
            if last_eff - f < 1:                                       # the hole starts right after this bar
                p += 1
                continue
            # next opposite event strictly after i
            q = p + 1
            while q < len(evi) and evs[q] != -d:
                q += 1
            if q < len(evi):
                k = int(evi[q])
                x = k if twin else k + 1
                x_open = x if x <= last_eff else None
            else:
                k, x_open = None, None
            risk = TRAIL_MULT * a.atr[i]
            stop0 = a.c[i] - d * risk
            trailing = (mode == "TRAIL")
            w = walk(a, f, d, stop0 if trailing else np.nan, x_open, last_eff, trailing, smult, slipx)
            rows.append(make_row(a, rule, mode, i, f, d, w, stop0 if trailing else np.nan, risk, stage,
                                 dict(i_xopen=(-1 if x_open is None else x_open), i_lasteff=last_eff)))
            if w["reason"] == "OPP":
                nxt = k
            elif w["reason"] in ("SL", "GAP_SL"):
                nxt = w["j"]
            elif last_eff < last:
                nxt = last_eff + 1                                     # ended by a hole in the data, carry on after it
            else:
                break
            p += 1
    df = pd.DataFrame(rows)
    return df


# ================================================================ numbers
def pf(x):
    g, ls = x[x > 0].sum(), -x[x < 0].sum()
    return round(g / ls, 3) if ls > 0 else None


def book(t):
    """The headline numbers. Dollars are at 0.10 lot (usd_01), pooled across pairs."""
    if t is None or len(t) == 0:
        return dict(trades=0)
    u = t.usd_01.values
    nights = t.nights.values.astype(float)
    w = u > 0
    d = dict(trades=int(len(t)), right_in_100=round(100 * w.mean(), 2), profit_factor=pf(t.usd_01),
             usd_per_trade=round(float(u.mean()), 3), usd_total=round(float(u.sum()), 1),
             gross_usd_per_trade=round(float(t.gross_usd.mean()), 3),
             cost_usd_per_trade=round(float(t.cost_usd.mean()), 3), edge_before_cost_usd_per_trade=round(float(t.edge_before_cost_usd.mean()), 3),
             avg_win_usd=round(float(u[w].mean()), 2) if w.any() else None,
             avg_loss_usd=round(float(u[~w].mean()), 2) if (~w).any() else None,
             avg_nights=round(float(nights.mean()), 1), median_nights=float(np.median(nights)),
             breakeven_swap_per_night=(round(float(u.sum() / nights.sum()), 3) if nights.sum() > 0 else None),
             interval_usd=(round(float(1.96 * u.std(ddof=1) / np.sqrt(len(u))), 2) if len(u) > 1 else None))
    if w.any() and (~w).any():
        aw, al = u[w].mean(), -u[~w].mean()
        d["breakeven_right_in_100"] = round(100 * al / (aw + al), 2)          # win rate needed for THIS payoff shape
    for nm, sd in (("buy", 1), ("sell", -1)):
        g = t[t.side == sd]
        d[nm + "_trades"] = int(len(g))
        d[nm + "_right_in_100"] = round(100 * (g.usd_01 > 0).mean(), 2) if len(g) else None
        d[nm + "_usd_per_trade"] = round(float(g.usd_01.mean()), 3) if len(g) else None
        d[nm + "_profit_factor"] = pf(g.usd_01) if len(g) else None
    return d


def by(t, key):
    rows = []
    for k, g in t.groupby(key):
        r = book(g)
        rows.append({key if isinstance(key, str) else "key": k, **{x: r.get(x) for x in
                     ("trades", "right_in_100", "profit_factor", "usd_per_trade", "usd_total", "avg_nights",
                      "breakeven_swap_per_night")}})
    return pd.DataFrame(rows)


# ================================================================ controls
def replay(t, rule, mode, stage, dirs, smult=1.0, slipx=0.0):
    """Same entry bars and same hold as the rule's own trades, but with the direction replaced by `dirs`."""
    rows = []
    for r, d in zip(t.itertuples(index=False), dirs):
        a = load(r.sym)
        i, f = int(r.i_sig), int(r.i_fill)
        i0, i1 = stage_idx(a, stage)
        last = min(i1 - 1, int(a.seg_end[f]))
        xo = int(r.i_xopen)
        # a rule trade that ended on a stop in bar j is matched to "leave at the open of j+1"
        if r.exit_reason in ("SL", "GAP_SL"):
            xo = int(r.i_exit) + 1
        x_open = None if xo < 0 or xo > last else xo
        d = int(d)
        risk = TRAIL_MULT * a.atr[i]
        stop0 = a.c[i] - d * risk
        trailing = (mode == "TRAIL")
        w = walk(a, f, d, stop0 if trailing else np.nan, x_open, last, trailing, smult, slipx)
        rows.append(dict(sym=r.sym, side=d, usd_01=w["net"] * a.usd, net_pips=w["net"], nights=int((a.day[w["j"]] - a.day[f]) / np.timedelta64(1, "D"))))
    return pd.DataFrame(rows)


def controls(t, rule, mode, stage, seeds=(7, 8, 9)):
    """Mandatory controls 1 and 3: always long / always short / coin flip at the rule's own entry bars and holds."""
    n = len(t)
    L = replay(t, rule, mode, stage, np.ones(n))
    S = replay(t, rule, mode, stage, -np.ones(n))
    cu, ca = [], []
    for sd in seeds:
        c = replay(t, rule, mode, stage, np.random.default_rng(sd).choice([1, -1], size=n))
        cu.append(c.usd_01.mean())
        ca.append(100 * (c.usd_01 > 0).mean())
    # does the rule pick the better side?
    better = np.where(L.usd_01.values > S.usd_01.values, 1, np.where(S.usd_01.values > L.usd_01.values, -1, 0))
    picked = (t.side.values == better) & (better != 0)
    return dict(trades=n, rule_right_in_100=round(100 * (t.usd_01 > 0).mean(), 2), rule_usd=round(float(t.usd_01.mean()), 3),
                all_long_right_in_100=round(100 * (L.usd_01 > 0).mean(), 2), all_long_usd=round(float(L.usd_01.mean()), 3),
                all_short_right_in_100=round(100 * (S.usd_01 > 0).mean(), 2), all_short_usd=round(float(S.usd_01.mean()), 3),
                coin_right_in_100=round(float(np.mean(ca)), 2), coin_usd=round(float(np.mean(cu)), 3), coin_seeds=list(seeds),
                picks_better_side_in_100=round(100 * picked.sum() / max((better != 0).sum(), 1), 2),
                rule_minus_coin_usd=round(float(t.usd_01.mean()) - float(np.mean(cu)), 3),
                rule_minus_coin_points=round(100 * (t.usd_01 > 0).mean() - float(np.mean(ca)), 2))


def luck(t, rule, mode, stage, n=20000, seeds=(0, 1, 2, 3, 4)):
    """Mandatory control 2: random entry bars, random direction, same instruments mix, same hold (drawn from the rule's
    own holds), same exit mechanism (3 x ATR trail for TRAIL; none for OPP), same costs. n >= 20,000 per seed."""
    pairs = t.sym.values
    holds = (np.where(t.exit_reason.isin(["SL", "GAP_SL"]), t.i_exit + 1, np.where(t.i_xopen >= 0, t.i_xopen, t.i_exit)) - t.i_fill).values
    holds = np.maximum(holds, 1)
    res_u, res_w = [], []
    syms = sorted(set(pairs))
    share = np.array([(pairs == s).mean() for s in syms])
    for sd in seeds:
        rng = np.random.default_rng(sd)
        sym_pick = rng.choice(len(syms), size=n, p=share)
        us = np.empty(n)
        for si, s in enumerate(syms):
            m = np.flatnonzero(sym_pick == si)
            if len(m) == 0:
                continue
            a = load(s)
            i0, i1 = stage_idx(a, stage)
            last = i1 - 1
            lo = max(i0, 130)
            for q in m:
                for _try in range(60):
                    hld = int(holds[rng.integers(len(holds))])
                    hi_ = last - hld - 1
                    if hi_ <= lo:
                        hld, hi_ = 1, last - 2
                    i = int(rng.integers(lo, hi_ + 1))
                    f = i + 1
                    le = min(last, int(a.seg_end[f]))
                    if le - f >= 1 and not a.unsafe[i] and np.isfinite(a.atr[i]):
                        break
                d = 1 if rng.random() < 0.5 else -1
                risk = TRAIL_MULT * a.atr[i]
                stop0 = a.c[i] - d * risk
                trailing = (mode == "TRAIL")
                w = walk(a, f, d, stop0 if trailing else np.nan, f + hld if f + hld <= le else None, le, trailing)
                us[q] = w["net"] * a.usd
        res_u.append(float(us.mean()))
        res_w.append(100 * float((us > 0).mean()))
    return dict(n=n, seeds=list(seeds), right_in_100=round(float(np.mean(res_w)), 2),
                seed_band_points=round(max(res_w) - min(res_w), 2), usd_per_trade=round(float(np.mean(res_u)), 3),
                usd_band=round(max(res_u) - min(res_u), 3), shape_matched=True, hold_matched=True, instrument_matched=True,
                hour_matched="n/a: every entry is at the 22:00 UTC daily rollover, as the rule's own are")


def lookahead(rule, mode, stage, entry_ok=None, syms=None):
    """Mandatory control 4: the look-ahead twin, in points AND dollars, with sign."""
    cl = run_rule(rule, mode, stage, syms=syms, entry_ok=entry_ok)
    tw = run_rule(rule, mode, stage, syms=syms, entry_ok=entry_ok, twin=True)
    if len(cl) == 0 or len(tw) == 0:
        return dict(note="no trades")
    return dict(clean_right_in_100=round(100 * (cl.usd_01 > 0).mean(), 2), clean_usd_per_trade=round(float(cl.usd_01.mean()), 3),
                twin_right_in_100=round(100 * (tw.usd_01 > 0).mean(), 2), twin_usd_per_trade=round(float(tw.usd_01.mean()), 3),
                lookahead_points=round(100 * (tw.usd_01 > 0).mean() - 100 * (cl.usd_01 > 0).mean(), 2),
                lookahead_usd=round(float(tw.usd_01.mean() - cl.usd_01.mean()), 3),
                twin_trades=int(len(tw)), clean_trades=int(len(cl)),
                twin_definition="fills at the signal bar's OWN open (1 day early) and exits at the exit-signal bar's own open; stop anchored the same")


def jdump(name, obj):
    def enc(x):
        if isinstance(x, (np.floating, np.integer)):
            return float(x)
        if isinstance(x, (np.bool_,)):
            return bool(x)
        return str(x)
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, default=enc)


def mdtab(df, flt="%.3f"):
    d = df.copy()
    for c in d.columns:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda x: "" if pd.isna(x) else (flt % x))
    d = d.astype(str)
    cols = list(d.columns)
    s = "| " + " | ".join(cols) + " |\n|" + "|".join(["---"] * len(cols)) + "|\n"
    s += "".join("| " + " | ".join(r) + " |\n" for r in d.itertuples(index=False))
    return s


# ================================================================ conditions (the autopsy catalogue slice)
def cond_defs(ref):
    """17 conditions. Thresholds frozen from the skeleton's own EXPLORE FX trades (median) or fixed constants.
    Every condition is stated in its plausible-good direction BEFORE it is scored; only that side can become a filter."""
    def med(c):
        return float(np.nanmedian(ref[c].values))
    return [
        ("A1", "250-day trend agrees with the trade", "agree250", ">", 0.5),
        ("A1", "60-day trend agrees with the trade", "agree60", ">", 0.5),
        ("A2", "fresh trend (age of the 100-day trend at or below the median)", "age", "<=", med("f_age")),
        ("A3", "clean trend (20-day efficiency ratio above the median)", "er20", ">", med("f_er20")),
        ("A4", "daily ATR rank in its own year above the median", "atr_rank", ">", med("f_atr_rank")),
        ("A5", "ATR expanding (today above 20 days ago)", "atr_exp", ">", 1.0),
        ("A7", "stretched from the 50-day average, in trade direction (above median)", "ext50", ">", med("f_ext50")),
        ("A8", "big move on the signal day (above median)", "big", ">", med("f_big")),
        ("A8", "signal on a Monday-labelled bar", "monday", ">", 0.5),
        ("B6", "month-end window (27th to 2nd)", "monthend", ">", 0.5),
        ("C1", "room to the 250-day extreme above the median", "room", ">", med("f_room")),
        ("C1", "closed AT a fresh 250-day extreme (no room left)", "room", "<=", 0.0),
        ("D1", "signal-bar body above the median", "body", ">", med("f_body")),
        ("D2", "signal bar closed near its extreme (above median)", "closepos_dir", ">", med("f_closepos_dir")),
        ("E5", "round trip small against ATR (at or below median)", "cost_atr", "<=", med("f_cost_atr")),
        ("X1", "JPY pair", "is_jpy", ">", 0.5),
        ("X2", "long trades only", "long", ">", 0.5),
    ]


def cond_mask(df, name, op, thr):
    v = df["f_" + name].values.astype(float)
    return (v > thr) if op == ">" else (v <= thr)


def entry_ok_from(name, op, thr):
    def fn(a, d):
        v = feat(a, name, d)
        with np.errstate(invalid="ignore"):
            return np.where(np.isnan(v), False, (v > thr) if op == ">" else (v <= thr))
    return fn


def scan(t, label):
    """Score every condition three ways: right_in_100, dollars per trade, trades kept. Also in both EXPLORE halves."""
    defs = cond_defs(t)
    base = book(t)
    half = t.t_signal < pd.Timestamp("2008-01-01")
    rows = []
    for cid, nm, f, op, thr in defs:
        m = cond_mask(t, f, op, thr)
        on, off = t[m], t[~m]
        if len(on) < 25 or len(off) < 25:
            rows.append(dict(skeleton=label, cat=cid, cond=nm, trades=int(m.sum()), note="too few"))
            continue
        bo, bf = book(on), book(off)
        r = dict(skeleton=label, cat=cid, cond=nm, feature=f, op=op, thr=thr, trades=int(m.sum()), kept_pct=round(100 * m.mean(), 1),
                 right_on=bo["right_in_100"], right_off=bf["right_in_100"], right_all=base["right_in_100"],
                 usd_on=bo["usd_per_trade"], usd_off=bf["usd_per_trade"], usd_all=base["usd_per_trade"],
                 pf_on=bo["profit_factor"], pf_off=bf["profit_factor"], pf_all=base["profit_factor"],
                 d_usd=round(bo["usd_per_trade"] - base["usd_per_trade"], 3))
        for hn, hm in (("h1", half), ("h2", ~half)):
            a_ = t[hm]
            m_ = cond_mask(a_, f, op, thr)
            if m_.sum() >= 10 and len(a_) > 0:
                r["d_usd_" + hn] = round(float(a_[m_].usd_01.mean() - a_.usd_01.mean()), 3)
                r["pf_on_" + hn] = pf(a_[m_].usd_01)
                r["pf_all_" + hn] = pf(a_.usd_01)
            else:
                r["d_usd_" + hn] = np.nan
        rows.append(r)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    pass

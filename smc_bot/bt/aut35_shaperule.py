"""PREREG addendum 21: end the trade differently (trend-flip exit, split targets) without losing any entry.
Usage: python aut35_shaperule.py STAGE VARIANT_INDEX     (rules run + saved)
       python aut35_shaperule.py check STAGE VARIANT_INDEX   (base replay vs common.simulate, trade for trade)
       python aut35_shaperule.py judge                       (merge + pre-registered verdict)
Open windows only (EXPLORE / VALIDATE / STRESS)."""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import aut16_layers as X
import aut17_round3 as R
import aut18_news as N
import common as C

P = C.pip(X.SYM)
COMM = C.COMM[X.SYM]                       # round-trip pips, paid once per basket
USD = C.USD_PIP_01[X.SYM]
SLIP = C.STOP_SLIP * P
FRACS = (0.2, 0.4, 0.6, 0.8, 1.0)
# rule -> (leg target fractions, leg weights, flip kind, move stop to entry after the first leg)
RULES = {"BASE": ((1.0,), (1.0,), None, False),
         "FLIP_E20": ((1.0,), (1.0,), "E20", False),
         "FLIP_TREND": ((1.0,), (1.0,), "TREND", False),
         "FLIP_2OPP": ((1.0,), (1.0,), "2OPP", False),
         "SPLIT5": (FRACS, (0.2,) * 5, None, False),
         "SPLIT5_BE": (FRACS, (0.2,) * 5, None, True)}


class Sim:
    def __init__(self, F):
        m1 = F["m1"]
        self.F = F
        self.t1 = m1.time.values
        self.o, self.h, self.l, self.c, self.s = F["o"], F["h"], F["l"], F["c"], m1.spr.values
        dow = m1.time.dt.dayofweek.values
        hm = (m1.time.dt.hour * 60 + m1.time.dt.minute).values
        gap = np.zeros(len(self.t1), bool)
        gap[:-1] = np.diff(self.t1) > np.timedelta64(6, "h")
        self.wk = np.where(((dow == 4) & (hm >= 20 * 60 + 45)) | gap)[0]
        self.n = len(self.t1)

    def flip_arrays(self, kind, trend):
        c, o, e20 = self.c, self.o, self.F["e20"]
        if kind == "E20":
            return c < e20, c > e20                                 # against a long, against a short
        if kind == "TREND":
            d = self.F["tr"][tuple(trend)]
            return d == -1, d == 1
        if kind == "2OPP":
            dn, up = c < o, c > o
            return dn & np.r_[False, dn[:-1]], up & np.r_[False, up[:-1]]
        raise KeyError(kind)

    def trade(self, f, d, fill, stop, tps, wts, flip, be_first, hold):
        """One basket entered on bar f. Returns (usd_01, last exit bar, text of how it ended) or None if invalid."""
        t1, o, h, l, c, s = self.t1, self.o, self.h, self.l, self.c, self.s
        if not (fill - stop) * d > 0 or any((tp - fill) * d <= 0 for tp in tps):
            return None
        end = min(f + 10 * 1440, self.n)
        end = min(end, np.searchsorted(t1, t1[f] + np.timedelta64(int(hold), "m"), "right"))
        wi = self.wk[np.searchsorted(self.wk, f):]
        hard = min(end, wi[0] + 1) if len(wi) else end
        m = hard - f
        hh, ll, ss = h[f:hard], l[f:hard], s[f:hard]

        def first(mask):
            return int(np.argmax(mask)) if mask.size and mask.any() else m

        def stop_at(stop_price, a):
            if a >= m:
                return m
            mk = (ll[a:] <= stop_price) if d == 1 else (hh[a:] + ss[a:] >= stop_price)
            return a + first(mk)

        def stop_px(k, stop_price):
            k_abs = f + k
            if d == 1:
                return (min(stop_price, o[k_abs]) - SLIP) if k_abs > f else stop_price - SLIP
            return (max(stop_price, o[k_abs] + s[k_abs]) + SLIP) if k_abs > f else stop_price + SLIP

        si = stop_at(stop, 0)
        fi = first(flip[f:hard]) if flip is not None else m
        legs = []
        be_stop_idx = None
        for q, tp in enumerate(tps):
            th = (hh >= tp) if d == 1 else (ll + ss <= tp)
            th[0] = False                                           # never a target on the fill bar
            ti = first(th)
            s_i, s_p, s_name = si, stop, "SL"
            if be_first and q > 0 and be_stop_idx is not None:
                s_i, s_p, s_name = be_stop_idx[0], be_stop_idx[1], "BE"
            ev = min((s_i, 0), (ti, 1), (fi, 2))
            if ev[0] >= m:
                k = m - 1
                legs.append((k, c[f + k] if d == 1 else c[f + k] + ss[k], "END"))
            elif ev[1] == 0:
                legs.append((ev[0], stop_px(ev[0], s_p), s_name))
            elif ev[1] == 1:
                legs.append((ev[0], tp, "TP"))
            else:
                k = ev[0]
                legs.append((k, c[f + k] if d == 1 else c[f + k] + ss[k], "FLIP"))
            if q == 0 and be_first and legs[0][2] == "TP":
                new_stop = fill + d * COMM * P
                be_stop_idx = (stop_at(new_stop, legs[0][0] + 1), new_stop)
        gross = sum(w * (px - fill) * d / P for w, (_, px, _) in zip(wts, legs))
        kmax = max(k for k, _, _ in legs)
        how = [r for _, _, r in legs]
        return (gross - COMM) * USD, f + kmax, how


def run_rule(S, F, i, side, spec, rule):
    fr, wts, flip_kind, be_first = RULES[rule]
    tpd, sld, hold = R.EXITS[spec["exit"]]
    flips = S.flip_arrays(flip_kind, spec["trend"]) if flip_kind else (None, None)
    t1, o, s = S.t1, S.o, S.s
    tc = F["tc"][i]
    f_all = np.searchsorted(t1, tc, "left")
    rows = []
    busy = np.datetime64("1970-01-01")
    for q in range(len(i)):
        d = int(side[q])
        tsig = tc[q]
        if tsig < busy:
            continue
        f = int(f_all[q])
        if f >= S.n - 2:
            continue
        fill = o[f] + s[f] if d == 1 else o[f]
        stop = fill - d * (sld - C.COMM[X.SYM]) / 10.0
        tps = [fill + d * (x * tpd + C.COMM[X.SYM]) / 10.0 for x in fr]
        r = S.trade(f, d, fill, stop, tps, wts, flips[0] if d == 1 else flips[1], be_first, hold)
        if r is None:
            continue
        usd, k, how = r
        busy = t1[k]
        rows.append((tsig, t1[k], d, usd, how[-1], sum(h == "TP" for h in how), (t1[k] - t1[f]) / np.timedelta64(1, "m")))
    return pd.DataFrame(rows, columns=["t_signal", "t_exit", "side", "usd_01", "last", "legs_tp", "hold_min"])


def stat(r, days):
    u = r.usd_01.values
    gp, gl = u[u > 0].sum(), -u[u <= 0].sum()
    r = r.sort_values("t_exit", kind="stable")
    cum = np.cumsum(r.usd_01.values)
    day = r.groupby(pd.DatetimeIndex(r.t_exit).normalize()).usd_01.sum()
    return dict(trades=len(r), per_day=round(len(r) / days, 2), wr=round(100.0 * (u > 0).mean(), 2), pf=round(gp / gl, 3) if gl > 0 else np.nan,
                net=round(u.sum()), usd_trade=round(u.mean(), 3), worst_day=round(day.min()), max_dd=round((np.maximum.accumulate(cum) - cum).max()),
                largest_loss=round(u.min(), 1), stop_pct=round(100.0 * r["last"].isin(["SL"]).mean(), 1),
                flip_pct=round(100.0 * (r["last"] == "FLIP").mean(), 1), hold_min=round(r.hold_min.mean(), 1))


def signals_for(F, sp):
    ix = pd.DatetimeIndex(F["tc"])
    hr, dow = ix.hour.values, ix.dayofweek.values
    ok = ~((hr >= 20) & (hr < 24)) & ~((dow == 0) & (hr < 7))
    i0, s0 = R.signals(F, tuple(sp["trend"]), sp.get("trig", "G0_BODY"), sp.get("hours", "ALL"), sp.get("side", 0))
    k = ok[i0] & (i0 < F["n"] - 3)
    return i0[k], s0[k]


def judge():
    d = pd.concat([pd.read_csv(f) for f in glob.glob(os.path.join(X.OUTD, "shaperule_*_*.csv"))])
    d.to_csv(os.path.join(X.OUTD, "SHAPERULE_ALL.csv"), index=False)
    order = ["EXPLORE", "VALIDATE", "STRESS"]
    res = []
    for (v, rule), g in d[d.rule != "BASE"].groupby(["variant", "rule"]):
        ok_all, bits = True, {}
        for s_ in order:
            x = g[g.stage == s_].iloc[0]
            b = d[(d.variant == v) & (d.rule == "BASE") & (d.stage == s_)].iloc[0]
            kept = 100.0 * x.trades / b.trades
            t = (kept >= 98, x.wr >= b.wr + 1.0, x.pf > b.pf, x.usd_trade >= b.usd_trade, x.max_dd <= b.max_dd)
            ok_all &= all(t)
            bits[s_] = f"kept {kept:.0f}% wr {x.wr - b.wr:+.1f} pf {x.pf - b.pf:+.3f} $/tr {x.usd_trade - b.usd_trade:+.2f} dd {x.max_dd - b.max_dd:+.0f}"
        res.append(dict(variant=v, rule=rule, works=bool(ok_all), **bits))
    r = pd.DataFrame(res)
    r.to_csv(os.path.join(X.OUTD, "SHAPERULE_JUDGED.csv"), index=False)
    print("variants helped (of 5) per rule:\n", r.groupby("rule").works.sum().to_string())
    rows = []
    for rule in RULES:
        out = {"rule": rule}
        for s_ in order:
            a = d[(d.rule == rule) & (d.stage == s_)].mean(numeric_only=True)
            out[s_] = f"trades/day {a.per_day:.1f} win {a.wr:.1f}% $/trade {a.usd_trade:+.2f} drop {a.max_dd:.0f}"
        rows.append(out)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    if sys.argv[1] == "judge":
        judge()
        sys.exit()
    check = sys.argv[1] == "check"
    args = sys.argv[2:] if check else sys.argv[1:]
    stage, vi = args[0], int(args[1])
    name = N.NAMES[vi]
    sp = N.spec_for(name)
    F = R.add_trends(X.build_stage(stage))
    i, side = signals_for(F, sp)
    S = Sim(F)
    if check:
        mine = run_rule(S, F, i, side, sp, "BASE")
        r = C.simulate(X.SYM, R.orders(F, i, side, sp["exit"]), one_at_a_time=True)
        r = r[r.status == "filled"].sort_values("t_signal", kind="stable").reset_index(drop=True)
        print(name, stage, "mine:", len(mine), round(mine.usd_01.sum(), 1), " common.simulate:", len(r), round(r.usd_01.sum(), 1))
        same = len(mine) == len(r) and np.allclose(mine.usd_01.values, r.usd_01.values, atol=1e-6)
        print("TRADE FOR TRADE MATCH:", same)
        sys.exit(0 if same else 1)
    rows = []
    for rule in RULES:
        r = run_rule(S, F, i, side, sp, rule)
        st = stat(r, F["days"])
        st.update(variant=name, stage=stage, rule=rule)
        rows.append(st)
        print(name, stage, rule, st["trades"], st["wr"], st["pf"], st["usd_trade"], st["max_dd"], flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(X.OUTD, f"shaperule_{stage}_{vi}.csv"), index=False)
    print("done", flush=True)

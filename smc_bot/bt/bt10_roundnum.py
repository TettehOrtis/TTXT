"""BT10 - ROUND-NUMBER LIQUIDITY (Osler stop-cascade / take-profit-bounce event study). DEV 2021-2023 only.
Run from the TTXT root:
    .venv/Scripts/python.exe smc_bot/bt/bt10_roundnum.py study      # Step 1 event study -> results/BT10/*.csv + tables.md
    .venv/Scripts/python.exe smc_bot/bt/bt10_roundnum.py check      # independent slow re-implementation vs vectorised events
Never touches VAL/TEST (events are cut at 2024-01-01; TEST is never loaded into any statistic).

Levels (in pips, so it is the same rule for every symbol): grid G = 50 pips  (FX: xx00/xx50 pips; JPY: .00/.50; gold $5).
  ROUND50  = all multiples of 50 pips, ROUND100 = multiples of 100 pips (xx00 / gold $10).
  PLACEBO  = the same grid shifted by 13, 23, 27, 37 pips (xx23, xx73 ... = offset 23; xx77 = offset 27 mod 50).
Sessions (UTC): Asia 00-07, London 07-13, NY 13-21. Bars opening 21:00-24:00 (rollover, wide spread) are not used.
Events are decided at the CLOSE of an M5 bar (t_signal = tclose). "Fresh" = none of the previous 12 M5 bars (1 hour) had been
in the trigger zone, and the level had not fired the same event earlier in the same session.
  BREACH   : close beyond level by >= k pips (k=3 FX, $1=10 pips gold). Direction = breach direction (continuation).
  APPROACH : bar high (low) comes within a=2 pips of the level from below (above) but the bar CLOSES on the near side.
             strict = high never reached the level; pierce = wick went through, close back on near side.
             Direction = away from the level (bounce).
Forward return = close(t+h) - close(t) in event direction, h = 15m, 1h, 4h (window must be gapless).
Cost = spread at the decision bar (bid+spr = ask) + COMM round trip. ATR = H1 ATR14 (last closed H1 bar).
t-stats are clustered by calendar day (pooled) or by pair-day (per pair): overlapping horizons and simultaneous events on the
same day share one cluster, so t does not benefit from overlap.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *          # noqa: F401,F403
import numpy as np, pandas as pd, zlib

OUT = os.path.join(RESULTS, "BT10"); os.makedirs(OUT, exist_ok=True)
DEV_A = np.datetime64("2021-01-01"); DEV_B = np.datetime64("2024-01-01")
G = 50.0                                    # grid spacing, pips
PLACEBO_OFF = [13.0, 23.0, 27.0, 37.0]
HORIZ = {"15m": 3, "1h": 12, "4h": 48}      # in M5 bars
FRESH = 12                                  # bars
A_PIPS = 2.0
SESS_NAMES = {0: "Asia", 1: "London", 2: "NY"}


def kpips(sym, mult=1.0):
    return (10.0 if sym == "XAUUSD" else 3.0) * mult


def session_of(hour):
    """0 Asia, 1 London, 2 NY, -1 off(21-24)."""
    return np.where(hour < 7, 0, np.where(hour < 13, 1, np.where(hour < 21, 2, -1)))


class Pair:
    def __init__(self, sym):
        self.sym = sym; self.P = pip(sym)
        b = bars(sym, "M5"); b = b[(b.time.values >= DEV_A) & (b.tclose.values <= DEV_B)].reset_index(drop=True)   # DEV only
        self.b = b
        self.o = b.open.values / self.P; self.h = b.high.values / self.P; self.l = b.low.values / self.P
        self.c = b.close.values / self.P; self.spr = b.spr.values / self.P
        self.tc = b.tclose.values; self.t0 = b.time.values
        hr = b.time.dt.hour.values
        self.sess = session_of(hr)
        self.day = (b.time.dt.floor("D")).values
        self.sess_id = (self.day.astype("datetime64[D]").astype(np.int64) * 4 + np.where(self.sess < 0, 3, self.sess))
        h1 = bars(sym, "H1"); a = pd.Series(atr(h1, 14))
        ix = asof_index(h1, self.tc)
        self.atr_pips = np.where(ix >= 0, a.values[np.clip(ix, 0, None)], np.nan) / self.P
        # forward index for each horizon, gapless check
        n = len(b); self.fwd = {}
        for name, k in HORIZ.items():
            j = np.arange(n) + k
            ok = j < n
            jj = np.where(ok, j, 0)
            ok &= (self.tc[jj] - self.tc) == np.timedelta64(5 * k, "m")
            self.fwd[name] = np.where(ok, jj, -1)

    # ------------------------------------------------------------------ event generators (arrays of bar index, level, dir)
    def _prior_max(self, x, n):     # max of x over previous n bars (excl. current)
        return pd.Series(x).shift(1).rolling(n, min_periods=n).max().values

    def _prior_min(self, x, n):
        return pd.Series(x).shift(1).rolling(n, min_periods=n).min().values

    def breach(self, off, kmult=1.0):
        k = kpips(self.sym, kmult); c = self.c
        up_lvl = np.floor((c - off - k) / G)                     # highest level index with L + k <= close
        dn_lvl = np.ceil((c - off + k) / G)                      # lowest level index with L - k >= close
        pm = self._prior_max(up_lvl, FRESH); pn = self._prior_min(dn_lvl, FRESH)
        ev = []
        up = np.where((up_lvl > pm) & (self.sess >= 0))[0]
        dn = np.where((dn_lvl < pn) & (self.sess >= 0))[0]
        for idx, lv, d in ((up, up_lvl, 1), (dn, dn_lvl, -1)):
            if len(idx):
                ev.append(pd.DataFrame({"i": idx, "lvl": lv[idx].astype(int), "dir": d}))
        ev = pd.concat(ev, ignore_index=True) if ev else pd.DataFrame(columns=["i", "lvl", "dir"])
        return self._first_in_session(ev, off)

    def approach(self, off, a=A_PIPS):
        c, h, l = self.c, self.h, self.l
        # from below: nearest level strictly above the close
        up_L = (np.floor((c - off) / G) + 1) * G + off
        dn_L = (np.ceil((c - off) / G) - 1) * G + off
        z_up = (h >= up_L - a) & (self.sess >= 0)
        z_dn = (l <= dn_L + a) & (self.sess >= 0)
        pm = self._prior_max(h, FRESH); pn = self._prior_min(l, FRESH)
        f_up = z_up & (pm < up_L - a); f_dn = z_dn & (pn > dn_L + a)
        ev = []
        i = np.where(f_up)[0]
        if len(i):
            ev.append(pd.DataFrame({"i": i, "lvl": np.round((up_L[i] - off) / G).astype(int), "dir": -1,
                                    "pierce": h[i] >= up_L[i]}))
        i = np.where(f_dn)[0]
        if len(i):
            ev.append(pd.DataFrame({"i": i, "lvl": np.round((dn_L[i] - off) / G).astype(int), "dir": 1,
                                    "pierce": l[i] <= dn_L[i]}))
        ev = pd.concat(ev, ignore_index=True) if ev else pd.DataFrame(columns=["i", "lvl", "dir", "pierce"])
        return self._first_in_session(ev, off)

    def _first_in_session(self, ev, off):
        if not len(ev):
            return ev
        ev = ev.sort_values(["i"]).copy()
        ev["sid"] = self.sess_id[ev.i.values]
        ev = ev.drop_duplicates(["sid", "lvl", "dir"], keep="first").drop(columns="sid")
        ev["off"] = off
        return ev.reset_index(drop=True)

    def random_events(self, n, seed):
        rng = np.random.default_rng(seed)
        ok = np.where((self.sess >= 0))[0]
        i = np.sort(rng.choice(ok, size=min(n, len(ok)), replace=False))
        return pd.DataFrame({"i": i, "lvl": 0, "dir": rng.choice([-1, 1], size=len(i)), "off": -1.0})

    # ------------------------------------------------------------------ forward returns
    def measure(self, ev, arm, kind):
        if not len(ev):
            return pd.DataFrame()
        i = ev.i.values; d = ev.dir.values
        out = pd.DataFrame({"sym": self.sym, "arm": arm, "kind": kind, "t": pd.to_datetime(self.tc[i]),
                            "day": self.day[i], "sess": self.sess[i], "dir": d, "lvl": ev.lvl.values, "off": ev.off.values,
                            "px": self.c[i] * self.P})
        if "pierce" in ev:
            out["pierce"] = ev.pierce.values
        cost = self.spr[i] + COMM[self.sym]
        ap = self.atr_pips[i]
        out["cost_pips"] = cost; out["atr_pips"] = ap
        for name in HORIZ:
            j = self.fwd[name][i]
            r = np.where(j >= 0, d * (self.c[np.clip(j, 0, None)] - self.c[i]), np.nan)
            out[f"g_{name}"] = r                                 # gross pips
            out[f"a_{name}"] = r / ap                            # gross in ATR
            out[f"n_{name}"] = r - cost                          # net pips
            out[f"na_{name}"] = (r - cost) / ap                  # net in ATR
        return out


# ---------------------------------------------------------------------- statistics
def cluster_mean(x, g):
    """mean, clustered SE, t, n, clusters."""
    x = np.asarray(x, float); m = ~np.isnan(x)
    x = x[m]; g = np.asarray(g)[m]
    n = len(x)
    if n < 5:
        return np.nan, np.nan, np.nan, n, 0
    mu = x.mean()
    s = pd.Series(x - mu).groupby(g).sum().values
    se = np.sqrt((s ** 2).sum()) / n
    return mu, se, (mu / se if se > 0 else np.nan), n, len(s)


def summarize(df, by_pair, horizons=HORIZ):
    """df: events. Returns one row per (group, horizon)."""
    rows = []
    for h in horizons:
        col_pips, col_atr, col_net, col_neta = f"g_{h}", f"a_{h}", f"n_{h}", f"na_{h}"
        if by_pair:
            gid = df.sym.astype(str) + df.day.astype(str)
        else:
            gid = df.day.astype(str)
        d = df[[col_pips, col_atr, col_net, col_neta]].copy()
        d["gid"] = gid.values
        d = d.dropna()
        g_ = cluster_mean(d[col_pips], d.gid); a_ = cluster_mean(d[col_atr], d.gid)
        n_ = cluster_mean(d[col_net], d.gid); na_ = cluster_mean(d[col_neta], d.gid)
        rows.append(dict(h=h, n=g_[3], clusters=g_[4], gross_pips=g_[0], gross_pips_t=g_[2], gross_ATR=a_[0], gross_ATR_t=a_[2],
                         net_pips=n_[0], net_pips_t=n_[2], net_ATR=na_[0], net_ATR_t=na_[2],
                         se_pips=g_[1], se_ATR=a_[1]))
    return pd.DataFrame(rows)


def table_for(ev_all, name_col="arm"):
    """ev_all has columns sym, arm, kind ... Build pooled + per pair summary with round-minus-placebo differences."""
    res = []
    for by_pair in (False, True):
        keys = [None] if not by_pair else PAIRS
        for sym in keys:
            sub = ev_all if sym is None else ev_all[ev_all.sym == sym]
            for arm, g in sub.groupby("arm"):
                s = summarize(g, by_pair=by_pair)
                s.insert(0, "arm", arm); s.insert(0, "sym", sym or "POOLED")
                res.append(s)
    t = pd.concat(res, ignore_index=True)
    return t


def add_diff(t, round_arm, plc_arm):
    """difference round - placebo (gross ATR/pips) with combined SE."""
    rows = []
    for (sym, h), g in t.groupby(["sym", "h"]):
        a = g[g.arm == round_arm]; p = g[g.arm == plc_arm]
        if len(a) and len(p):
            a = a.iloc[0]; p = p.iloc[0]
            dATR = a.gross_ATR - p.gross_ATR; sATR = np.hypot(a.se_ATR, p.se_ATR)
            dP = a.gross_pips - p.gross_pips; sP = np.hypot(a.se_pips, p.se_pips)
            rows.append(dict(sym=sym, h=h, n_round=a.n, n_placebo=p.n, round_ATR=a.gross_ATR, placebo_ATR=p.gross_ATR,
                             diff_ATR=dATR, diff_ATR_t=dATR / sATR, round_pips=a.gross_pips, placebo_pips=p.gross_pips,
                             diff_pips=dP, diff_pips_t=dP / sP))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------- Step 1 driver
def build_events(kmult=1.0, a=A_PIPS, tag=""):
    allev = []
    for sym in PAIRS:
        p = Pair(sym)
        parts = []
        # round levels
        b0 = p.breach(0.0, kmult)
        if len(b0):
            r100 = (b0.lvl % 2 == 0)
            parts.append(p.measure(b0, "breach_round50", "breach"))
            parts.append(p.measure(b0[r100], "breach_round100", "breach"))
        ap0 = p.approach(0.0, a)
        for nm, sel in (("strict", ~ap0.pierce.astype(bool)), ("pierce", ap0.pierce.astype(bool)), ("all", np.ones(len(ap0), bool))):
            e = ap0[sel]
            parts.append(p.measure(e, f"approach_{nm}_round50", "approach"))
            parts.append(p.measure(e[e.lvl % 2 == 0], f"approach_{nm}_round100", "approach"))
        # placebo levels
        for off in PLACEBO_OFF:
            pb = p.breach(off, kmult); parts.append(p.measure(pb, "breach_placebo", "breach"))
            pa = p.approach(off, a)
            for nm, sel in (("strict", ~pa.pierce.astype(bool)), ("pierce", pa.pierce.astype(bool)), ("all", np.ones(len(pa), bool))):
                parts.append(p.measure(pa[sel], f"approach_{nm}_placebo", "approach"))
        # random times (5x the number of round50 breach events, random direction)
        nrand = max(2000, 5 * len(b0))
        parts.append(p.measure(p.random_events(nrand, seed=zlib.crc32(sym.encode()) % 1000 + 7), "random", "random"))
        allev.append(pd.concat([x for x in parts if len(x)], ignore_index=True))
        print(sym, {k: int(v) for k, v in pd.concat(parts).groupby("arm").size().items() if "round50" in k or k == "breach_placebo"}, flush=True)
    return pd.concat(allev, ignore_index=True)


def md(df, cols, fmt=None, floatfmt=3):
    fmt = fmt or {}
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, (float, np.floating)):
                cells.append("" if np.isnan(v) else f"{v:.{fmt.get(c, floatfmt)}f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def study():
    ev = build_events()
    ev.to_parquet(os.path.join(OUT, "events_main.parquet"), index=False)
    t = table_for(ev)
    t.to_csv(os.path.join(OUT, "study_by_arm.csv"), index=False)
    lines = []
    diffs = []
    for kind, pairs in (("breach", [("breach_round50", "breach_placebo"), ("breach_round100", "breach_placebo")]),
                        ("approach_strict", [("approach_strict_round50", "approach_strict_placebo"), ("approach_strict_round100", "approach_strict_placebo")]),
                        ("approach_pierce", [("approach_pierce_round50", "approach_pierce_placebo"), ("approach_pierce_round100", "approach_pierce_placebo")]),
                        ("approach_all", [("approach_all_round50", "approach_all_placebo"), ("approach_all_round100", "approach_all_placebo")])):
        for ra, pa in pairs:
            d = add_diff(t, ra, pa); d.insert(0, "arm", ra); diffs.append(d)
    D = pd.concat(diffs, ignore_index=True)
    D.to_csv(os.path.join(OUT, "study_round_vs_placebo.csv"), index=False)

    # -------- markdown tables
    pooled = t[t.sym == "POOLED"].copy()
    lines.append("## Pooled (10 pairs), clustered by day; ATR = H1 ATR14\n")
    order = ["breach_round50", "breach_round100", "breach_placebo", "approach_strict_round50", "approach_strict_round100",
             "approach_strict_placebo", "approach_pierce_round50", "approach_pierce_round100", "approach_pierce_placebo", "random"]
    pooled["ord"] = pooled.arm.map({a: i for i, a in enumerate(order)}); pooled = pooled.dropna(subset=["ord"]).sort_values(["ord", "h"])
    lines.append(md(pooled, ["arm", "h", "n", "clusters", "gross_ATR", "gross_ATR_t", "net_ATR", "net_ATR_t"], dict(n=0, clusters=0), 4))
    lines.append("\n## Round minus placebo, pooled (gross, ATR units and t)\n")
    lines.append(md(D[D.sym == "POOLED"], ["arm", "h", "n_round", "n_placebo", "round_ATR", "placebo_ATR", "diff_ATR", "diff_ATR_t"], dict(n_round=0, n_placebo=0), 4))
    for arm in ["breach_round50", "breach_round100", "approach_strict_round50", "approach_pierce_round50"]:
        pl = arm.replace("round50", "placebo").replace("round100", "placebo")
        lines.append(f"\n## Per pair: {arm} vs {pl} (gross pips; net pips of the round arm; t clustered pair-day)\n")
        rows = []
        for sym in PAIRS:
            for h in HORIZ:
                a = t[(t.sym == sym) & (t.arm == arm) & (t.h == h)]; p = t[(t.sym == sym) & (t.arm == pl) & (t.h == h)]
                if len(a) and len(p):
                    a = a.iloc[0]; p = p.iloc[0]
                    dd = D[(D.arm == arm) & (D.sym == sym) & (D.h == h)].iloc[0]
                    rows.append(dict(sym=sym, h=h, n=a.n, n_plc=p.n, round_gross_pips=a.gross_pips, round_t=a.gross_pips_t,
                                     plc_gross_pips=p.gross_pips, diff_pips=dd.diff_pips, diff_t=dd.diff_pips_t,
                                     round_net_pips=a.net_pips, cost_pips=a.gross_pips - a.net_pips))
        lines.append(md(pd.DataFrame(rows), ["sym", "h", "n", "n_plc", "round_gross_pips", "round_t", "plc_gross_pips", "diff_pips", "diff_t", "round_net_pips", "cost_pips"],
                        dict(n=0, n_plc=0), 2))
    open(os.path.join(OUT, "tables.md"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))


def sensitivity():
    """k x2 for breaches, approach zone a=1 and a=4 (documented trials)."""
    out = []
    for kmult in (2.0,):
        ev = build_events(kmult=kmult)
        t = table_for(ev)
        D = pd.concat([add_diff(t, "breach_round50", "breach_placebo").assign(arm="breach_round50")])
        D["variant"] = f"k x{kmult}"; out.append(D[D.sym == "POOLED"])
        D2 = add_diff(t, "breach_round50", "breach_placebo"); D2["variant"] = f"k x{kmult}"
        D2.to_csv(os.path.join(OUT, f"sens_k{kmult}.csv"), index=False)
    R = pd.concat(out)
    print(md(R, ["variant", "h", "n_round", "n_placebo", "round_ATR", "placebo_ATR", "diff_ATR", "diff_ATR_t", "diff_pips", "diff_pips_t"], dict(n_round=0, n_placebo=0), 4))
    R.to_csv(os.path.join(OUT, "sensitivity.csv"), index=False)


def extras():
    """direction, session, period-half and 'gross needed' diagnostics from the saved events."""
    ev = pd.read_parquet(os.path.join(OUT, "events_main.parquet"))
    lines = []
    ev["year"] = pd.to_datetime(ev.t).dt.year
    for arm, pl in (("breach_round50", "breach_placebo"), ("approach_strict_round50", "approach_strict_placebo")):
        lines.append(f"\n### {arm} vs {pl} by year / session / direction (pooled, 1h and 4h, gross ATR, diff t)\n")
        rows = []
        for key, lab in (("year", "year"), ("sess", "session"), ("dir", "dir")):
            for v in sorted(ev[key].dropna().unique()):
                for h in ("1h", "4h"):
                    a = ev[(ev.arm == arm) & (ev[key] == v)]; p = ev[(ev.arm == pl) & (ev[key] == v)]
                    sa = summarize(a, False, [h]).iloc[0]; sp = summarize(p, False, [h]).iloc[0]
                    d = sa.gross_ATR - sp.gross_ATR; se = np.hypot(sa.se_ATR, sp.se_ATR)
                    rows.append(dict(split=lab, val=str(v), h=h, n=sa.n, round_ATR=sa.gross_ATR, placebo_ATR=sp.gross_ATR, diff_ATR=d, t=d / se,
                                     round_net_ATR=sa.net_ATR))
        lines.append(md(pd.DataFrame(rows), ["split", "val", "h", "n", "round_ATR", "placebo_ATR", "diff_ATR", "t", "round_net_ATR"], dict(n=0), 4))
    # cost in ATR
    c = ev[ev.arm == "breach_round50"].groupby("sym").agg(cost_pips=("cost_pips", "mean"), atr_pips=("atr_pips", "mean"))
    c["cost_ATR"] = c.cost_pips / c.atr_pips
    lines.append("\n### Mean round-trip cost at breach events\n"); lines.append(md(c.reset_index(), ["sym", "cost_pips", "atr_pips", "cost_ATR"], floatfmt=3))
    open(os.path.join(OUT, "extras.md"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))


# ---------------------------------------------------------------------- independent slow check
def check(sym="EURUSD", month=("2022-03-01", "2022-04-01")):
    """Re-implement breach + approach detection from M1 bars with plain python loops and compare with the vectorised events."""
    m1 = bars(sym, "M1"); P = pip(sym)
    m1 = m1[(m1.time >= month[0]) & (m1.time < month[1])].reset_index(drop=True)
    # own M5 build
    g = m1.set_index("time").resample("5min", label="left", closed="left")
    m5 = pd.DataFrame({"high": g.high.max(), "low": g.low.min(), "close": g.close.last()}).dropna().reset_index()
    m5["tclose"] = m5.time + pd.Timedelta(minutes=5)
    k = kpips(sym); a = A_PIPS
    def sess(hour):
        return -1 if hour >= 21 else (0 if hour < 7 else (1 if hour < 13 else 2))
    ev_slow = set(); seen = set(); seen_a = set()
    closes = (m5.close / P).values; highs = (m5.high / P).values; lows = (m5.low / P).values
    for i in range(FRESH, len(m5)):
        s = sess(m5.time[i].hour); sid = (m5.time[i].date(), s)
        if s < 0:
            continue
        # every 50-pip level near the close
        base = int(np.floor(closes[i] / G))
        for lv in range(base - 2, base + 4):
            L = lv * G
            # up breach
            if closes[i] >= L + k and all(closes[j] < L + k for j in range(i - FRESH, i)) and \
               not any(True for _ in []) :
                # highest crossed level only
                if not (closes[i] >= L + G + k):
                    key = ("b", sid, lv, 1)
                    if key not in seen:
                        seen.add(key); ev_slow.add(("breach", m5.tclose[i], lv, 1))
            if closes[i] <= L - k and all(closes[j] > L - k for j in range(i - FRESH, i)):
                if not (closes[i] <= L - G - k):
                    key = ("b", sid, lv, -1)
                    if key not in seen:
                        seen.add(key); ev_slow.add(("breach", m5.tclose[i], lv, -1))
            # approach from below: level strictly above close
            if L > closes[i] and L - G <= closes[i] and highs[i] >= L - a and all(highs[j] < L - a for j in range(i - FRESH, i)):
                key = ("a", sid, lv, -1)
                if key not in seen_a:
                    seen_a.add(key); ev_slow.add(("approach", m5.tclose[i], lv, -1))
            if L < closes[i] and L + G >= closes[i] and lows[i] <= L + a and all(lows[j] > L + a for j in range(i - FRESH, i)):
                key = ("a", sid, lv, 1)
                if key not in seen_a:
                    seen_a.add(key); ev_slow.add(("approach", m5.tclose[i], lv, 1))
    # vectorised on the same M1-derived month (same detector code as study, but on Pair object restricted to month)
    p = Pair(sym)
    ix = np.where((p.tc >= np.datetime64(month[0])) & (p.tc <= np.datetime64(month[1])))[0]
    b = p.breach(0.0); a_ = p.approach(0.0)
    ev_fast = set()
    for r in b.itertuples():
        if r.i in ix and r.i > ix[0] + FRESH:
            ev_fast.add(("breach", pd.Timestamp(p.tc[r.i]), int(r.lvl), int(r.dir)))
    for r in a_.itertuples():
        if r.i in ix and r.i > ix[0] + FRESH:
            ev_fast.add(("approach", pd.Timestamp(p.tc[r.i]), int(r.lvl), int(r.dir)))
    # the slow version keys sessions independently for breach & approach levels but events are identical by construction
    only_slow = ev_slow - ev_fast; only_fast = ev_fast - ev_slow
    print(sym, month, "slow", len(ev_slow), "fast", len(ev_fast), "only_slow", len(only_slow), "only_fast", len(only_fast))
    for x in sorted(only_slow)[:5]:
        print(" only slow", x)
    for x in sorted(only_fast)[:5]:
        print(" only fast", x)
    # forward-return check against M1 closes
    ev = pd.read_parquet(os.path.join(OUT, "events_main.parquet"))
    e = ev[(ev.sym == sym) & (ev.arm == "breach_round50")].head(3)
    m1full = bars(sym, "M1"); m1full = m1full.set_index("tclose")
    for r in e.itertuples():
        c0 = m1full.close.get(pd.Timestamp(r.t)); c1 = m1full.close.get(pd.Timestamp(r.t) + pd.Timedelta(hours=1))
        print("fwd check", r.t, "M1 based 1h pips", round(r.dir * (c1 - c0) / P, 2), "event table", round(r.g_1h, 2))


def examples():
    ev = pd.read_parquet(os.path.join(OUT, "events_main.parquet"))
    e = ev[ev.arm == "breach_round50"].copy()
    rng = np.random.default_rng(1)
    sel = e.iloc[np.sort(rng.choice(len(e), 10, replace=False))]
    cols = ["sym", "t", "dir", "px", "lvl", "g_15m", "g_1h", "g_4h", "cost_pips", "atr_pips"]
    sel = sel[cols].copy()
    sel["level_px"] = [(r.lvl * G * pip(r.sym)) for r in sel.itertuples()]
    sel.to_csv(os.path.join(OUT, "examples_breach.csv"), index=False)
    ea = ev[ev.arm == "approach_strict_round50"].copy()
    sel2 = ea.iloc[np.sort(rng.choice(len(ea), 5, replace=False))][cols]
    sel2.to_csv(os.path.join(OUT, "examples_approach.csv"), index=False)
    print(sel.to_string()); print(sel2.to_string())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "study"
    {"study": study, "check": check, "sens": sensitivity, "extras": extras, "examples": examples}[cmd]()

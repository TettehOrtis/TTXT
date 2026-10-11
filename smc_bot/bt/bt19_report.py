"""BT19 report - every question in minutes. Reads the first-passage tables from bt19_timemap.py.

CONDITIONING (the thing that makes these numbers right): a basket stops existing the moment it hits its
target or its stop. So "it dipped to -$30" only counts when -$30 came BEFORE the target. Measuring the
dip without that condition counts moves that happened hours after the basket already closed, which
inflates every dip number. Every table below conditions on the basket still being open.

ERAS are kept apart on purpose. The 2021-2025 Exness Zero feed is much faster than the 2009-2021
Dukascopy history (median win 8 min vs 20 min), so pooling them hides the thing being asked about.
The earlier BT15 tables were the Zero feed alone; the "2021-2025" rows here are what reconcile with them.

    .venv/Scripts/python.exe smc_bot/bt/bt19_report.py
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt import bt19_timemap as TM                                      # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402

C_USD = TM.C_USD
RES = TM.RES
NEV = TM.NEVER
ERAS = {"2009-2021": ("duka", "2009-03-15", "2021-01-01"),
        "2021-2025": ("zero", "2021-01-01", L.LAST_OPP)}
TB = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 120), (120, 240), (240, 1441)]
IU = {v: i for i, v in enumerate(TM.UP)}
IDN = {v: i for i, v in enumerate(TM.DN)}


def mdtab(df):
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join("" if pd.isna(r[c]) else str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def load():
    E = {}
    for era, (ds, a, b_) in ERAS.items():
        f = np.load(os.path.join(TM.PARTS, f"tm_{ds}.npz"))
        t = f["t_opp"]
        lo = np.datetime64(pd.Timestamp(a)).astype("datetime64[ns]").astype(np.int64)
        hi = np.datetime64(pd.Timestamp(b_)).astype("datetime64[ns]").astype(np.int64)
        m = (t >= lo) & (t < hi)
        n = int(m.sum())
        E[era] = dict(up=f["up"][:, m].reshape(2 * n, -1), dn=f["dn"][:, m].reshape(2 * n, -1),
                      peak=f["peak"][:, m].ravel(), tpeak=f["tpeak"][:, m].ravel(), t=np.tile(t[m], 2))
    return E


def med(x):
    x = np.asarray(x, float)
    return float(np.median(x)) if len(x) else np.nan


def pct(x, p):
    x = np.asarray(x, float)
    return float(np.percentile(x, p)) if len(x) else np.nan


def R(v, d=0):
    return None if v != v else (round(v) if d == 0 else round(v, d))


def main():
    os.makedirs(RES, exist_ok=True)
    E = load()
    out = []
    W = out.append
    tot = sum(e["up"].shape[0] for e in E.values())
    W("# BT19 - the time map\n")
    W(f"**0.10 lots throughout.** {tot:,} baskets, a chance every 30 minutes, 2009-03 to 2025-06, both "
      f"sides of every chance. Forward walk capped at 24 hours.\n")
    W("Floating dollars are the price move. A basket closing at **+$7 gross pays +$5.90**; a **-$70 gross "
      "costs -$71.10**, after the $1.10 round-trip commission.\n")
    W("A basket only counts as 'down $X' when it got there **before** it hit its target - after the target "
      "the basket is already closed.\n")
    W("The two feeds are kept apart because they are not the same speed. **2021-2025 is the one that "
      "matches the tables you have already seen.**\n")

    # ------------------------------------------------- 1: the clock, by target size
    W("## 1. How long does a basket take - to the win, and to the loss\n")
    W("Target T with the shipped $70 stop. 'min to win' is measured only on baskets that won, "
      "'min to loss' only on the ones that lost.\n")
    rows = []
    for era, d in E.items():
        up, dn = d["up"], d["dn"]
        fl70 = dn[:, IDN[70]]
        for T in [7, 10, 15, 20, 30, 50]:
            fw = up[:, IU[T]]
            win, los = fw < fl70, fl70 < fw
            dec = win | los
            tw, tl = fw[win].astype(float), fl70[los].astype(float)
            pw, pl = T - C_USD, -70 - C_USD
            wr = win[dec].mean()
            rows.append(dict(era=era, target=f"+${T:g}", win_pct=R(100 * wr, 2),
                             min_to_win_med=R(med(tw)), min_to_win_p75=R(pct(tw, 75)),
                             min_to_loss_med=R(med(tl)), min_to_loss_p75=R(pct(tl, 75)),
                             need_win_pct=R(100 * (-pl) / (pw - pl), 2),
                             per_basket=R(wr * pw + (1 - wr) * pl, 2)))
    t1 = pd.DataFrame(rows)
    W(mdtab(t1) + "\n")
    t1.to_csv(os.path.join(RES, "clock_by_target.csv"), index=False)

    # ------------------------------------------------- 2: how far up, and when
    W("## 2. How far UP can a basket go if nothing closes it\n")
    W("No target, no stop, 24 hours. This is the ceiling the $7 cap gives away.\n")
    rows = []
    for era, d in E.items():
        pk, tp = d["peak"], d["tpeak"].astype(float)
        rows.append(dict(era=era, p25=R(pct(pk, 25), 2), median=R(pct(pk, 50), 2), p75=R(pct(pk, 75), 2),
                         p90=R(pct(pk, 90), 2), p99=R(pct(pk, 99), 2),
                         min_to_peak_med=R(med(tp)), hours_to_peak_med=R(med(tp) / 60, 1)))
    t2 = pd.DataFrame(rows)
    W(mdtab(t2) + "\n")
    t2.to_csv(os.path.join(RES, "peak.csv"), index=False)

    W("And the clock on the way up - the share of all baskets that ever reach a level, and how long it took:\n")
    rows = []
    for era, d in E.items():
        up = d["up"]
        for v in [7, 15, 30, 50, 100, 200]:
            f = up[:, IU[v]]
            got = f < NEV
            tt = f[got].astype(float)
            rows.append(dict(era=era, level=f"+${v:g}", reach_pct=R(100 * got.mean(), 1),
                             min_med=R(med(tt)), hours_med=R(med(tt) / 60, 1), min_p25=R(pct(tt, 25))))
    t3 = pd.DataFrame(rows)
    W(mdtab(t3) + "\n")
    t3.to_csv(os.path.join(RES, "run_up_clock.csv"), index=False)

    # ------------------------------------------------- 3: winner dips, properly conditioned
    W("## 3. How deep does a WINNER dip before it comes back - and when\n")
    W("Winners at the shipped $7 target / $70 stop. The dip must happen before the +$7.\n")
    rows = []
    for era, d in E.items():
        up, dn = d["up"], d["dn"]
        f7, f70 = up[:, IU[7]], dn[:, IDN[70]]
        win = f7 < f70
        for v in [5, 10, 15, 20, 30, 50]:
            fd = dn[:, IDN[v]]
            dipped = win & (fd < f7)
            rows.append(dict(era=era, dip=f"-${v:g}",
                             pct_of_winners=R(100 * dipped.sum() / max(1, win.sum()), 1),
                             min_to_dip_med=R(med(fd[dipped].astype(float)))))
    t4 = pd.DataFrame(rows)
    W(mdtab(t4) + "\n")
    t4.to_csv(os.path.join(RES, "winner_dips.csv"), index=False)

    # ------------------------------------------------- 4: the kill rule
    W("## 4. THE KILL RULE - it is down $X at minute M and still open. Cut it or hold it?\n")
    W("'hold' is what riding on to +$7 or -$70 actually paid on average; 'cut' is closing on the spot "
      "for -$X minus the $1.10 commission.\n")
    rows = []
    for era, d in E.items():
        up, dn = d["up"], d["dn"]
        f7, f70 = up[:, IU[7]], dn[:, IDN[70]]
        win = f7 < f70
        for v in [10, 20, 30, 50]:
            fd = dn[:, IDN[v]]
            openat = (fd < f7) & (fd < NEV)
            for (a, b_) in TB:
                sel = openat & (fd >= a) & (fd < b_)
                if sel.sum() < 300:
                    continue
                w = float(win[sel].mean())
                hold = w * (7 - C_USD) + (1 - w) * (-70 - C_USD)
                cut = -v - C_USD
                rows.append(dict(era=era, down=f"-${v:g}", at_min=f"{a}-{b_ if b_ < 1441 else 1440}",
                                 baskets=int(sel.sum()), still_win_pct=R(100 * w, 1),
                                 hold=R(hold, 2), cut=R(cut, 2), better="CUT" if cut > hold else "hold"))
    t5 = pd.DataFrame(rows)
    W(mdtab(t5) + "\n")
    t5.to_csv(os.path.join(RES, "kill_rule.csv"), index=False)

    # ------------------------------------------------- 5: sessions
    W("## 5. Sessions - how many chances, and what they pay at $7 / $70\n")
    rows = []
    for era, d in E.items():
        up, dn = d["up"], d["dn"]
        f7, f70 = up[:, IU[7]], dn[:, IDN[70]]
        win = f7 < f70
        money = np.where(win, 7 - C_USD, -70 - C_USD)
        sess = L.session_of(pd.to_datetime(d["t"]))
        for nm, a, b_ in L.SESSIONS:
            m = sess == nm
            if m.sum() == 0:
                continue
            rows.append(dict(era=era, session=f"{nm} {a:02d}-{b_:02d}", baskets=int(m.sum()),
                             share_pct=R(100 * m.mean(), 1), win_pct=R(100 * win[m].mean(), 2),
                             per_basket=R(float(money[m].mean()), 2),
                             min_to_win_med=R(med(f7[m & win].astype(float))),
                             min_to_loss_med=R(med(f70[m & ~win].astype(float)))))
    t6 = pd.DataFrame(rows)
    W(mdtab(t6) + "\n")
    t6.to_csv(os.path.join(RES, "sessions.csv"), index=False)

    txt = "\n".join(out)
    with open(os.path.join(RES, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)


if __name__ == "__main__":
    main()

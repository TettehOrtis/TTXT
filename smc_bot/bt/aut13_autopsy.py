"""AUT13 autopsy: features, the condition scan (22 conditions, fixed in advance), refinement selection, VALIDATE, STRESS."""
import os, sys, json, time
os.environ.setdefault("SMC_DATASET", "duka")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import aut13_leadlag as L
C, A = L.C, L.A

TARGETS = ("XAUUSD", "EURUSD")


# ---------------------------------------------------------------- features on the event table
def feat(target, mode="gap"):
    key = ("feat", target, mode)
    if key in L._CACHE:
        return L._CACHE[key]
    ev = L.events(target, mode).copy()
    u = ev.beta * ev.D_k                                             # the move the dollar implies for the target
    ev["u_implied"] = u
    ev["f_absz"] = ev.z.abs()
    ev["f_abszD"] = ev.z_D.abs()
    ev["f_abszT"] = ev.z_T.abs()
    ev["f_dollar_led"] = (ev.dollar_share > 0.5).astype(float)
    ev["f_dollar_led_strong"] = (ev.dollar_share > 0.75).astype(float)
    ev["f_target_led"] = (ev.dollar_share < 0.25).astype(float)
    ev["f_wrongway"] = ((ev.T_k * u) < 0).astype(float)                  # target moved the opposite way to what the dollar implies
    same = (ev.T_k * u) > 0
    ev["f_underreact"] = (same & (ev.T_k.abs() < u.abs())).astype(float)   # right way, not far enough: the textbook "lag"
    ev["f_overreact"] = (same & (ev.T_k.abs() >= u.abs())).astype(float)
    ev["f_abscorr"] = ev["corr"].abs()
    pip = C.pip(target)
    ev["f_cost_share"] = (C.COMM[target] + ev.spr_fill / pip) / (ev.atr15 / pip)
    # A4: M15 ATR against its own trailing 60-day median, last CLOSED M15 bar
    b15 = L.bars(target, "M15")
    a15 = A.atr(b15, 14)
    med = a15.rolling(96 * 60, min_periods=96 * 5).median().values
    j = np.clip(C.asof_index(b15, ev.t_signal.values), 0, len(a15) - 1)
    ev["f_atr_vs_med"] = a15.values[j] / med[j]
    # clustering: minutes since the previous 2-sigma bar of the same k (before any cooldown)
    prev = ev.groupby("k").tmin.diff()
    ev["f_since_prev"] = prev.fillna(1e9)
    h = ev.hour.values
    ev["f_asia"] = ((h >= 22) | (h < 7)).astype(float)
    ev["f_london"] = ((h >= 7) & (h < 12)).astype(float)
    ev["f_overlap"] = ((h >= 12) & (h < 16)).astype(float)
    ev["f_ny_late"] = ((h >= 16) & (h < 22)).astype(float)
    ev["f_not_bad_hour"] = (~pd.Series(h).isin(A.BAD_HOURS_UTC).values).astype(float)
    L._CACHE[key] = ev
    return ev


def thresholds(target, tr):
    """Median splits are FROZEN here, from the EXPLORE base trades of this instrument, and reused unchanged later."""
    return dict(abscorr_med=float(np.nanmedian(tr.f_abscorr)), spr_med=float(np.nanmedian(tr.spr_fill)))


def CONDS(th):
    """22 conditions, fixed before any result was seen. (id, catalogue group, name, mask function)."""
    return [
        ("S1", "signal", "gap at least 3 sigma", lambda d: d.f_absz.values >= 3),
        ("S2", "B5 proxy", "gap at least 4 sigma (spike)", lambda d: d.f_absz.values >= 4),
        ("L1", "lead-lag", "gap mostly from the dollar move (dollar share > 0.5)", lambda d: d.f_dollar_led.values > 0.5),
        ("L2", "lead-lag", "gap almost all from the dollar (share > 0.75)", lambda d: d.f_dollar_led_strong.values > 0.5),
        ("L3", "lead-lag", "gap almost all from the target's own move (share < 0.25)", lambda d: d.f_target_led.values > 0.5),
        ("L4", "lead-lag", "target moved the WRONG way for the dollar move", lambda d: d.f_wrongway.values > 0.5),
        ("L5", "lead-lag", "target followed the dollar but too little (under-reaction)", lambda d: d.f_underreact.values > 0.5),
        ("L6", "lead-lag", "target followed the dollar and too much (over-reaction)", lambda d: d.f_overreact.values > 0.5),
        ("L7", "lead-lag", "dollar move large (|z| >= 2)", lambda d: d.f_abszD.values >= 2),
        ("L8", "lead-lag", "dollar move small (|z| < 1)", lambda d: d.f_abszD.values < 1),
        ("L9", "lead-lag", "target move large (|z| >= 3)", lambda d: d.f_abszT.values >= 3),
        ("L10", "lead-lag", "5 of 6+ dollar pairs agree (breadth >= 0.8)", lambda d: d.breadth.values >= 0.8),
        ("L11", "A3", "target and dollar tightly linked over past 5 days (top half)", lambda d: d.f_abscorr.values > th["abscorr_med"]),
        ("T1", "B1", "Asia 22-07 UTC", lambda d: d.f_asia.values > 0.5),
        ("T2", "B1", "London 07-12 UTC", lambda d: d.f_london.values > 0.5),
        ("T3", "B2", "overlap 12-16 UTC", lambda d: d.f_overlap.values > 0.5),
        ("T4", "B1", "late New York 16-22 UTC", lambda d: d.f_ny_late.values > 0.5),
        ("T5", "B4", "outside AUT09's blocked hours", lambda d: d.f_not_bad_hour.values > 0.5),
        ("E1", "E5", "spread at the fill below its EXPLORE median", lambda d: d.spr_fill.values <= th["spr_med"]),
        ("E2", "A4", "M15 ATR above its own 60-day median", lambda d: d.f_atr_vs_med.values > 1.0),
        ("E3", "G", "first signal in an hour (no 2-sigma bar of this k in the last 60 minutes)", lambda d: d.f_since_prev.values > 60),
        ("E4", "A8", "not Friday", lambda d: d.dow.values != 4),
    ]


def filt(ev, th, cid):
    for c in CONDS(th):
        if c[0] == cid:
            return ev[c[3](ev)]
    raise KeyError(cid)


def scan(target, tr, th):
    rows = []
    for cid, grp, name, fn in CONDS(th):
        m = np.asarray(fn(tr), dtype=bool)
        on, off = tr[m], tr[~m]
        if len(on) < 25 or len(off) < 25:
            rows.append(dict(id=cid, group=grp, cond=name, trades=int(m.sum()), note="too few"))
            continue
        b_on, s_on = on[on.side == 1], on[on.side == -1]
        rows.append(dict(id=cid, group=grp, cond=name, trades=int(m.sum()), kept_pct=round(100 * m.mean(), 1),
                         right_on=round(100 * (on.net_pips > 0).mean(), 2), right_off=round(100 * (off.net_pips > 0).mean(), 2),
                         usd_on=round(on.usd_01.mean(), 3), usd_off=round(off.usd_01.mean(), 3),
                         d_usd=round(on.usd_01.mean() - off.usd_01.mean(), 3),
                         d_usd_vs_all=round(on.usd_01.mean() - tr.usd_01.mean(), 3),
                         buy_usd_on=round(b_on.usd_01.mean(), 3) if len(b_on) else None,
                         sell_usd_on=round(s_on.usd_01.mean(), 3) if len(s_on) else None))
    return pd.DataFrame(rows)


def base_trades(target, stage, k=L.BASE_K, h=L.BASE_H, ev=None, th=None, cid=None):
    ev = feat(target) if ev is None else ev
    if cid:
        ev = filt(ev, th, cid)
    e = L.cell(ev, stage, k, h)
    return L.filled(L.run(target, e, h)), e


if __name__ == "__main__":
    pass

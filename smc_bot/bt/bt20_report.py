"""BT20 report - does any real entry clear its own bar at any exit shape, in every era?

The test that matters is CONSISTENCY. BT18's headline 52.55 turned out to be 73% pre-2021, reading a coin
flip on 2021-2023 and above 52 only on a few hundred recent trades. So a shape is only interesting here if
it pays in 2009-2021 AND 2021-2023 AND 2024-2025. Anything that works in one window is noise until proven
otherwise.

    .venv/Scripts/python.exe smc_bot/bt/bt20_report.py
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bt import bt20_lib as B                                           # noqa: E402
from bt import bt15_lib as L                                           # noqa: E402

ERAS = ["2009-2021", "2021-2023", "2024-2025"]
SESS = [s for s, _, _ in L.SESSIONS]


def mdtab(df):
    c = list(df.columns)
    out = ["| " + " | ".join(map(str, c)) + " |", "|" + "|".join(["---"] * len(c)) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join("" if pd.isna(r[x]) else str(r[x]) for x in c) + " |")
    return "\n".join(out)


def main():
    df = pd.concat([pd.read_csv(os.path.join(B.OUT, f"grid_{d}.csv")) for d in ("duka", "zero")])
    out = []
    W = out.append
    W("# BT20 - real entries at every exit shape\n")
    W("**0.10 lots. Our costs ($1.10 round trip).** Gross = the price move in dollars. Net = after "
      "commission. One basket at a time with the robot's 60-second cooldown, so the shape changes how "
      "many trades fit in a day.\n")
    W("`acc` = times out of 100 it picks the right side. `own bar` = what it must beat ON THE MINUTES IT "
      "CHOOSES (spike minutes are wilder than average, so their bar is higher). `beats` = acc minus bar; "
      "it must be positive to make money.\n")

    # ---------------------------------------------------------------- throughput
    W("## 1. Does a tighter stop give us more trades?\n")
    W("Your question. Same entry rule, same signals, only the exit changes. 2021-2025.\n")
    v = "A_spikefade_p99.5_w1440"
    d = df[(df.era == "2021-2025") & (df.variant == v)]
    rows = []
    for S in (5, 10, 15, 20, 30, 70):
        for T in (7, 30):
            r = d[(d.target == T) & (d.stop == S)]
            if len(r) == 0:
                continue
            r = r.iloc[0]
            rows.append(dict(shape=f"${T} / -${S}", trades=int(r.trades), per_day=r.per_day,
                             per_month=r.per_month, med_min=int(r.med_min),
                             gross=int(r.gross), net=int(r.net), per_basket=r.per_basket))
    t1 = pd.DataFrame(rows).sort_values("shape")
    W(mdtab(t1) + "\n")
    t1.to_csv(os.path.join(B.OUT, "throughput.csv"), index=False)

    # ---------------------------------------------------------------- cross-era consistency
    W("## 2. The real test - does it pay in EVERY era?\n")
    p = df[df.era.isin(ERAS)].pivot_table(index=["variant", "target", "stop"], columns="era",
                                          values="per_basket")
    n = df[df.era.isin(ERAS)].pivot_table(index=["variant", "target", "stop"], columns="era",
                                          values="trades")
    p = p.dropna()
    good = p[(p > 0).all(axis=1)]
    W(f"Shapes tested: **{len(p):,}**. Shapes that make money in all three eras: **{len(good)}**.\n")
    if len(good):
        g = good.join(n, rsuffix="_n").reset_index()
        g["worst_era"] = g[ERAS].min(axis=1).round(3)
        g = g.sort_values("worst_era", ascending=False).head(20)
        g = g[["variant", "target", "stop"] + ERAS + [f"{e}_n" for e in ERAS] + ["worst_era"]]
        for e in ERAS:
            g[e] = g[e].round(2)
            g[f"{e}_n"] = g[f"{e}_n"].astype(int)
        W(mdtab(g) + "\n")
        g.to_csv(os.path.join(B.OUT, "consistent.csv"), index=False)
    else:
        W("**Not one.** No entry rule at any exit shape made money in all three eras.\n")

    # beats-its-own-bar version
    pb = df[df.era.isin(ERAS)].pivot_table(index=["variant", "target", "stop"], columns="era",
                                           values="beats_bar").dropna()
    gb = pb[(pb > 0).all(axis=1)]
    W(f"Shapes where the entry beats its own bar in all three eras: **{len(gb)}** of {len(pb):,}.\n")
    if len(gb):
        h = gb.join(n, rsuffix="_n").reset_index()
        h["worst"] = h[ERAS].min(axis=1).round(2)
        h = h.sort_values("worst", ascending=False).head(15)
        for e in ERAS:
            h[e] = h[e].round(2)
            h[f"{e}_n"] = h[f"{e}_n"].astype(int)
        W(mdtab(h[["variant", "target", "stop"] + ERAS + [f"{e}_n" for e in ERAS] + ["worst"]]) + "\n")
        h.to_csv(os.path.join(B.OUT, "consistent_bar.csv"), index=False)

    # ---------------------------------------------------------------- sessions
    W("## 3. Sessions - where the trades are and which pays best\n")
    W("Pooled over every entry rule and every shape, 2021-2025. 'share' is the share of all trades taken.\n")
    z = df[df.era == "2021-2025"]
    rows = []
    for s in SESS:
        nn, uu = z[f"n_{s}"].sum(), z[f"usd_{s}"].sum()
        rows.append(dict(session=s, trades=int(nn), share_pct=round(100 * nn / z.trades.sum(), 1),
                         net_usd=int(uu), per_basket=round(uu / max(1, nn), 3)))
    t3 = pd.DataFrame(rows).sort_values("per_basket", ascending=False)
    W(mdtab(t3) + "\n")
    t3.to_csv(os.path.join(B.OUT, "sessions.csv"), index=False)

    # ---------------------------------------------------------------- best overall, recent
    W("## 4. The best shapes on the recent feed (2021-2025, the whole window)\n")
    W("Sorted by money per basket. Watch the trade count - the good-looking rows are thin.\n")
    z2 = z.sort_values("per_basket", ascending=False).head(15)
    cols = ["variant", "target", "stop", "trades", "per_day", "per_month", "acc", "own_be",
            "beats_bar", "gross", "net", "per_basket", "per_month_usd", "med_min"]
    W(mdtab(z2[cols].round(2)) + "\n")

    txt = "\n".join(out)
    with open(os.path.join(B.OUT, "report.md"), "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)


if __name__ == "__main__":
    main()

"""AUT15 Part B variant picking: applies the rule in results/AUT15_costcut/preregistration.txt (plus its dated addendum).
Writes partA_marks.csv (every variant x view with its marks) and partB_picks.json."""
import os
import sys
import json

os.environ.setdefault("SMC_DATASET", "duka")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np                                                        # noqa: E402
import pandas as pd                                                       # noqa: E402
import common as C                                                        # noqa: E402

OUT = os.path.join(C.RESULTS, "AUT15_costcut")
HEAD = {"AUT01": "GOLD", "AUT02": "GOLD", "AUT03": "GOLD", "AUT04": "GOLD", "AUT05": "GOLD", "AUT06": "ALL", "AUT07": "ALL",
        "AUT08": "ALL", "AUT09": "ALL", "AUT10": "ALL", "AUT12": "ALL", "AUT13": "ALL", "AUT14": "GOLD", "AUT11": "FX24"}
STG = ["EXPLORE", "VALIDATE", "STRESS"]


def a11_table():
    s = pd.read_csv(os.path.join(OUT, "_work", "A11_summary.csv"))
    rows = []
    for (v, inst), g in s.groupby(["variant", "inst"]):
        for st in STG:
            h = g[g.stage == st]
            if len(h) == 0:
                rows.append(dict(key="AUT11__" + v, study="AUT11", label=v, view=inst, stage=st, n=0))
                continue
            h = h.iloc[0]
            rows.append(dict(key="AUT11__" + v, study="AUT11", label=v, view=inst, stage=st, n=int(h.n), n_saved=int(h.n),
                             edge0=h.edge0, net1=h.net, saved_net=h.net, paid_by_study=h.cost, paid_my_env=h.cost,
                             buy0=h.buy0, sell0=h.sell0))
    return pd.DataFrame(rows)


HEAD_KEY = {"AUT01__R2_shape_filter": "ALL"}
#: same orders as another row (an earlier EXPLORE-only file superseded by an all-stage rebuild): counted once
DUPLICATE = {"AUT04__R0_all_follow", "AUT04__LONDON_4x4_best", "AUT06__EXPLORE_baseline", "AUT12__E_LFIX_AM"}


def build_marks(tab):
    tab = tab.copy()
    tab["key"] = tab["key"].replace({"AUT07__FVG_FILL_base_VS": "AUT07__FVG_FILL_base", "AUT01__LIVE_AS_SHIPPED_STRESS": "AUT01__LIVE_AS_SHIPPED"})
    tab["n"] = tab["n"].fillna(0)
    tab = tab.sort_values("n", ascending=False).drop_duplicates(["key", "view", "stage"]).sort_values(["key", "view", "stage"])
    out = []
    for (key, view), g in tab.groupby(["key", "view"]):
        g = g.set_index("stage").reindex(STG)
        study = key.split("__")[0]
        n = g.n.fillna(0).astype(int)
        e = g.edge0
        measured = [s for s in STG if n[s] > 0]
        pos_all = all((n[s] > 0 and e[s] > 0) for s in STG)
        n500 = all(n[s] >= 500 for s in STG)
        buy_ok = all((g.buy0[s] >= 0) for s in STG if n[s] > 0) if measured else False
        sell_ok = all((g.sell0[s] >= 0) for s in STG if n[s] > 0) if measured else False
        paid = g.paid_my_env
        ratio = min(((e[s] / paid[s]) if (n[s] > 0 and paid[s] and paid[s] > 0) else np.nan) for s in STG) if len(measured) == 3 else np.nan
        out.append(dict(key=key, study=study, label=g.label.dropna().iloc[0] if "label" in g and g.label.notna().any() else key.split("__", 1)[1],
                        view=view, headline=(HEAD_KEY.get(key, HEAD.get(study)) == view), stages_measured=len(measured),
                        n_E=n["EXPLORE"], n_V=n["VALIDATE"], n_S=n["STRESS"],
                        edge0_E=e["EXPLORE"], edge0_V=e["VALIDATE"], edge0_S=e["STRESS"],
                        net_today_E=g.net1["EXPLORE"], net_today_V=g.net1["VALIDATE"], net_today_S=g.net1["STRESS"],
                        saved_net_E=g.saved_net["EXPLORE"], saved_net_V=g.saved_net["VALIDATE"], saved_net_S=g.saved_net["STRESS"],
                        paid_E=paid["EXPLORE"], paid_V=paid["VALIDATE"], paid_S=paid["STRESS"],
                        worst_edge0=np.nanmin(e.values) if len(measured) == 3 else np.nan,
                        pos_all3=bool(pos_all), n500_all3=bool(n500), buy_nonneg_all=bool(buy_ok), sell_nonneg_all=bool(sell_ok),
                        worst_ratio=ratio))
    o = pd.DataFrame(out)
    o["duplicate"] = o.key.isin(DUPLICATE)
    return o


if __name__ == "__main__":
    tab = pd.read_csv(os.path.join(OUT, "partA_stage_table_all.csv"))
    tab = pd.concat([tab, a11_table()], ignore_index=True)
    m = build_marks(tab)
    m.to_csv(os.path.join(OUT, "partA_marks.csv"), index=False)
    h = m[m.headline & ~m.duplicate]
    cnt = dict(variants_headline_view=len(h), measured_all_three=int((h.stages_measured == 3).sum()),
               edge_positive_all_three=int(h.pos_all3.sum()),
               and_500_trades_each_stage=int((h.pos_all3 & h.n500_all3).sum()))
    el = h[h.pos_all3 & h.n500_all3].copy()
    el["clean"] = el.buy_nonneg_all & el.sell_nonneg_all
    el = el.sort_values("worst_ratio", ascending=False)
    pick = el.groupby("study").head(2).sort_values("worst_ratio", ascending=False).head(6)
    cnt["picked"] = len(pick)
    print(json.dumps(cnt, indent=1))
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(el[["key", "view", "n_E", "n_V", "n_S", "edge0_E", "edge0_V", "edge0_S", "paid_E", "worst_ratio", "clean"]].round(3).to_string())
    print("PICKS")
    print(pick[["key", "view", "worst_edge0", "worst_ratio", "clean"]].round(3).to_string())
    json.dump(dict(counts=cnt, picks=pick[["key", "view"]].to_dict("records"), eligible=el.key.tolist()),
              open(os.path.join(OUT, "_work", "partB_picks.json"), "w"), indent=1)

"""Print the compact tables used to write the report (reads only AUT13 result files)."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "AUT13_leadlag")
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 50)

rows = []
for tg in ("XAUUSD", "EURUSD"):
    d = json.load(open(os.path.join(OUT, "full_%s.json" % tg)))
    for key, c in d.items():
        v, st = key.split("|")
        r = c["rule"]
        rt = c["round_trip_usd"]
        rows.append(dict(tg=tg, var=v, stage=st, k=c["k"], h=c["hold"], n=r["trades"], right=r["right_in_100"],
                         luck=c.get("luck_bar", {}).get("right_in_100"), band=c.get("luck_bar", {}).get("seed_band_points"),
                         usd=r["usd_per_trade"], gross=round(r["usd_per_trade"] + rt, 2), rt=rt, pf=r["profit_factor"],
                         luck_usd=c.get("luck_bar", {}).get("usd_per_trade"),
                         buy=r["buy_usd_per_trade"], sell=r["sell_usd_per_trade"],
                         rev=c["reversed_rule"]["usd_per_trade"], long=c["always_long"]["usd_per_trade"], short=c["always_short"]["usd_per_trade"],
                         coin=c["coin_flip"]["usd_per_trade"], d1=c["delay_1min"].get("usd_per_trade"), d2=c["delay_2min"].get("usd_per_trade"),
                         twin_pts=c["lookahead_twin"]["points"], twin_usd=c["lookahead_twin"]["usd"],
                         adv1=(c.get("adverse") or {}).get(list((c.get("adverse") or {"x": 0}).keys())[0], {}).get("usd_per_trade") if c.get("adverse") else None,
                         adv2=(c.get("adverse") or {}).get(list((c.get("adverse") or {"x": 0}).keys())[-1], {}).get("usd_per_trade") if c.get("adverse") else None))
df = pd.DataFrame(rows)
print(df.to_string())
df.to_csv(os.path.join(OUT, "variant_table.csv"), index=False)

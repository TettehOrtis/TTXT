"""Build data_duka/{SYM}_M1.parquet from the HistData.com M1 zips (data_duka/histdata_raw/{SYM}_{YYYY}.zip).

  .venv\\Scripts\\python.exe tools\\hist_build.py [--syms EURUSD,GBPUSD] [--shift-h 5]

- HistData ASCII M1 = BID prices. Timestamps are New York LOCAL time WITH DST (UTC-4 summer / UTC-5 winter), despite the
  site saying 'EST without DST': checked month by month vs data_zero 2021-22 (92-98% of minutes within 0.5 pip).
- Output schema = data_zero exactly: time (UTC, ms), open, high, low, close (bid), tick_volume (uint64), spread (int32 points).
- Spread = TODAY's Zero-account execution cost (we trade today, so the question is whether the old price action pays today's
  costs): the median data_zero spread per instrument x UTC hour-of-day. Crosses (no Zero history here): 1.5 x the median of
  the originals' hour profiles in pips. Historical spreads come later from the slow full Dukascopy job (data_duka_full/).
- Nothing on or after 2025-07-01 (locked TEST) is written.
"""
import argparse, glob, io, json, os, sys, zipfile
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data_duka", "histdata_raw")
OUT = os.path.join(ROOT, "data_duka")
ZERO = os.path.join(ROOT, "data_zero")
LOCK = pd.Timestamp("2025-07-01")
ORIG = "EURUSD GBPUSD USDJPY AUDUSD USDCAD USDCHF NZDUSD EURGBP EURJPY XAUUSD".split()
CROSSES = ("GBPJPY AUDJPY EURAUD EURCHF GBPCHF CADJPY NZDJPY AUDCAD AUDNZD EURCAD GBPAUD "
           "CHFJPY GBPCAD AUDCHF NZDCAD").split()


def point(sym):
    return 0.001 if ("JPY" in sym or sym == "XAUUSD") else 0.00001


def pip(sym):
    return 0.1 if sym == "XAUUSD" else (0.01 if "JPY" in sym else 0.0001)


def zero_profile(sym):
    """median Zero spread in points per UTC hour (24 values)."""
    z = pd.read_parquet(os.path.join(ZERO, f"{sym}_M1.parquet"), columns=["time", "spread"])
    z = z[z.time < "2024-01-01"]
    prof = z.groupby(z.time.dt.hour).spread.median().reindex(range(24)).ffill().bfill()
    return prof.values.astype("float64")


def profiles():
    prof = {s: zero_profile(s) for s in ORIG}
    pips = np.median(np.vstack([prof[s] * point(s) / pip(s) for s in ORIG if s != "XAUUSD"]), axis=0)
    for s in CROSSES:
        prof[s] = 1.5 * pips * pip(s) / point(s)
    return prof


def read_year(zp):
    z = zipfile.ZipFile(zp)
    name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
    df = pd.read_csv(io.BytesIO(z.read(name)), sep=";", header=None,
                     names=["t", "open", "high", "low", "close", "vol"], dtype={"t": str})
    df["time"] = pd.to_datetime(df.t, format="%Y%m%d %H%M%S")
    return df.drop(columns="t")


def build(sym, prof, shift_h=0):
    files = sorted(glob.glob(os.path.join(RAW, f"{sym}_*.zip")))
    if not files:
        return None
    df = pd.concat([read_year(f) for f in files], ignore_index=True)
    if shift_h:
        df["time"] = df.time + pd.Timedelta(hours=shift_h)
    else:   # New York local (EST/EDT) -> UTC
        t = df.time.dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT")
        df = df[t.notna().values]
        df["time"] = t[t.notna()].dt.tz_convert("UTC").dt.tz_localize(None).values
    df = df[df.time < LOCK].sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
    bad = (df.high < df[["open", "close"]].max(axis=1)) | (df.low > df[["open", "close"]].min(axis=1))
    df.loc[bad, "high"] = df.loc[bad, ["open", "high", "close"]].max(axis=1)
    df.loc[bad, "low"] = df.loc[bad, ["open", "low", "close"]].min(axis=1)
    spread = np.rint(prof[df.time.dt.hour.values]).astype("int32")
    out = pd.DataFrame({
        "time": df.time.astype("datetime64[ms]").values,
        "open": df.open.astype("float64").values, "high": df.high.astype("float64").values,
        "low": df.low.astype("float64").values, "close": df.close.astype("float64").values,
        "tick_volume": np.maximum(df.vol.fillna(0).values, 1).astype("uint64"),
        "spread": spread,
    })
    p = os.path.join(OUT, f"{sym}_M1.parquet")
    tmp = p + ".tmp"
    out.to_parquet(tmp, index=False, engine="pyarrow", coerce_timestamps="ms")
    os.replace(tmp, p)
    return dict(sym=sym, rows=len(out), first=str(out.time.iloc[0]), last=str(out.time.iloc[-1]), years=len(files),
                ohlc_fixed=int(bad.sum()), spread_pips_by_hour=[round(float(x) * point(sym) / pip(sym), 2) for x in prof],
                mb=round(os.path.getsize(p) / 1e6, 1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default="")
    ap.add_argument("--shift-h", type=int, default=0)   # 0 = New York local -> UTC (correct)
    a = ap.parse_args()
    syms = a.syms.split(",") if a.syms else ORIG + CROSSES
    prof = profiles()
    infos = []
    for s in syms:
        i = build(s, prof[s], a.shift_h)
        print(json.dumps({k: v for k, v in i.items() if k != "spread_pips_by_hour"}) if i else f"{s}: no files", flush=True)
        if i:
            infos.append(i)
    os.makedirs(os.path.join(ROOT, "smc_bot", "results", "DATA2"), exist_ok=True)
    with open(os.path.join(ROOT, "smc_bot", "results", "DATA2", "hist_build_info.json"), "w") as f:
        json.dump(infos, f, indent=1)

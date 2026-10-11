# Truncation test: every value at bar k computed on data[:cut] must equal the full-data value for k<cut.
import os,sys; sys.path.insert(0,"smc_bot"); sys.path.insert(0,"research")
import numpy as np, pandas as pd
from lib import load, pip_size
from smc_core import resample, structure, build_zones, first_touch, fvgs
m1=load("EURUSD","2024-01-01"); m1=m1[m1.time<"2024-07-01"].reset_index(drop=True)
m1["spr"]=m1.spr.clip(lower=0)*1e-4
bad=0
for tf,N in (("M5",3),("M30",3),("H1",5)):
    b=resample(m1,tf); full=structure(b,N)
    rng=np.random.default_rng(0)
    for cut in rng.integers(300,len(b)-10,25):
        tr=structure(b.iloc[:cut].reset_index(drop=True),N)
        for key in ("trend","evt","ob_lo","ob_hi","leg_i","lsh","lsl","lsh_i","lsl_i"):
            a,f=tr[key],full[key][:cut]
            eq=np.array_equal(a,f) if a.dtype.kind in "iu" else np.allclose(a,f,equal_nan=True)
            if not eq: bad+=1; print("MISMATCH",tf,cut,key,np.where(~np.isclose(a.astype(float),f.astype(float),equal_nan=True))[0][:5])
    # zones created before cut must be identical
    z=build_zones(b,full,96)
    for cut in rng.integers(300,len(b)-10,10):
        bt=b.iloc[:cut].reset_index(drop=True); zt=build_zones(bt,structure(bt,N),96)
        zf=z[z.t_created<=bt.tclose.iloc[-1]].reset_index(drop=True)
        if len(zt)!=len(zf) or not np.allclose(zt[["lo","hi"]].values,zf[["lo","hi"]].values): bad+=1; print("ZONE MISMATCH",tf,cut,len(zt),len(zf))
# resample: M1 bar open-time alignment: H1 bar's tclose covers minutes [time, time+60)
h1=resample(m1,"H1"); r=h1.iloc[100]
mm=m1[(m1.time>=r.time)&(m1.time<r.tclose)]
print("H1 bar check high/low/close match:",mm.high.max()==r.high,mm.low.min()==r.low,mm.close.iloc[-1]==r.close)
# swing confirmation delay: first k where structure lsh_i==s must be s+N
b=resample(m1,"M5"); st=structure(b,3)
first={}
for k,s in enumerate(st["lsh_i"]):
    if s>=0 and s not in first: first[s]=k
d=np.array([k-s for s,k in first.items()]); print("swing known delay (bars) min/max:",d.min(),d.max())
print("TOTAL MISMATCHES",bad)

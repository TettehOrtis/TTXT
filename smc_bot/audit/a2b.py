import os,sys; sys.path.insert(0,"smc_bot"); sys.path.insert(0,"research")
import numpy as np, pandas as pd
from lib import load
from smc_core import resample, structure, build_zones
m1=load("EURUSD","2024-01-01"); m1=m1[m1.time<"2024-07-01"].reset_index(drop=True)
bad=0
for tf,N in (("M5",3),("M30",3),("H1",5)):
    b=resample(m1,tf); z=build_zones(b,structure(b,N),96)
    for cut in np.random.default_rng(1).integers(300,len(b)-10,10):
        bt=b.iloc[:cut].reset_index(drop=True); zt=build_zones(bt,structure(bt,N),96)
        zf=z[z.t_created<=bt.tclose.iloc[-1]]
        key=["t_created","kind","dir","lo","hi"]
        A=zt[key].sort_values(key).reset_index(drop=True); B=zf[key].sort_values(key).reset_index(drop=True)
        if not A.equals(B): bad+=1; print("mismatch",tf,cut)
print("zone set mismatches:",bad)

import pandas as pd, numpy as np
pd.set_option("display.width",250); pd.set_option("display.max_columns",60)
df=pd.read_parquet("smc_bot/out/setups_EURUSD.parquet")
print(df.status.value_counts())
t=df[df.status=="traded"].copy()
p=1e-4
t["sl_ok"]=np.where(t.dir==1,t.sl<t.entry,t.sl>t.entry)
t["tp_ok"]=np.where(t.dir==1,t.tp>t.entry,t.tp<t.entry)
print("sl side ok",t.sl_ok.mean(),"tp side ok",t.tp_ok.mean())
t["fill_minus_entry_pips"]=(t.fill-t.entry)*t.dir/p
print(t.fill_minus_entry_pips.describe())
t["rr_fill"]=(t.tp-t.fill).abs()/(t.fill-t.sl).abs()
print(t[["rr","rr_fill","risk_pips","risk_atr5","tp_atr_h1","hold_h","atr5_pips","bars_to_choch"]].describe(percentiles=[.1,.25,.5,.75,.9]).round(2))
print(t.exit_why.value_counts())
print(t.groupby("exit_why").r_net.describe())
print("dir",t.groupby("dir").r_net.agg(["size","mean",lambda x:(x>0).mean()]))
print("entry_kind",t.groupby("entry_kind").r_net.agg(["size","mean",lambda x:(x>0).mean()]))
print("zone_kind",t.groupby("zone_kind").r_net.agg(["size","mean",lambda x:(x>0).mean()]))
t["rrb"]=pd.cut(t.rr,[2,3,4,6,10,1e9])
print(t.groupby("rrb").r_net.agg(["size","mean",lambda x:(x>0).mean()]))
# fraction exiting SL within first minutes
t["hold_m"]=t.hold_h*60
print("SL hold minutes quantiles", t[t.exit_why=="SL"].hold_m.quantile([.1,.25,.5,.75,.9]).round(1).to_dict())
print("SL on fill bar (hold 0):",(t.hold_m==0).sum())
# expected winrate if random walk: 1/(1+rr)
print("mean breakeven winrate 1/(1+rr_fill):",(1/(1+t.rr_fill)).mean())
for c in ["h1_ema_ok","m30_ema_ok","m5_ema_ok","discount_ok","swept","m15_agree"]:
    print(c,t.groupby(c).r_net.agg(["size","mean",lambda x:(x>0).mean()]).round(3).to_dict("index"))
print(df.status.groupby(df.t_setup.dt.year).value_counts().unstack())

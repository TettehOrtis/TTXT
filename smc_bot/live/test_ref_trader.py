"""Tests for ref_trader.py with a MOCK broker (no MetaTrader5 terminal call; only its constants are imported).

  .venv/Scripts/python.exe smc_bot/live/test_ref_trader.py            # all mock tests
  .venv/Scripts/python.exe smc_bot/live/test_ref_trader.py smoke      # + read-only DRY-RUN smoke test on the live terminal

The smoke test attaches to the running terminal exactly like the scanner (read-only), runs one dry-run cycle with
mt5.order_send / mt5.order_check replaced by functions that raise, and prints the state and the request it would send.
"""
import os, sys, json, shutil, tempfile, unittest
from collections import defaultdict
from types import SimpleNamespace as NS
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ref_trader as RT                                          # noqa: E402
import MetaTrader5 as mt5                                        # noqa: E402  (constants only in the mock tests)

ep, PAIRS = RT.ep, RT.PAIRS
PX = dict(EURUSD=1.17000, GBPUSD=1.34000, USDJPY=150.000, AUDUSD=0.66000, USDCAD=1.39000, USDCHF=0.80000,
          NZDUSD=0.58000, EURJPY=175.000, XAUUSD=3800.000)
TV = dict(USDJPY=0.6351747, EURJPY=0.6351747, USDCAD=0.7045975, USDCHF=1.1985091, XAUUSD=0.1)
COMM_PER_LOT = 5.0                                               # $ per lot, charged on the entry deal (Zero-like)


def spec_for(p):
    d = 3 if ("JPY" in p or p == "XAUUSD") else 5
    pt = 10.0 ** -d
    tv = TV.get(p, 1.0)
    return NS(digits=d, point=pt, volume_min=0.01, volume_step=0.01, volume_max=200.0, trade_tick_size=pt,
              trade_tick_value=tv, trade_tick_value_loss=tv, filling_mode=3, expiration_mode=15,
              trade_stops_level=0, trade_freeze_level=0)


class MockGW:
    """In-memory broker with the MT5Gateway interface. Orders/positions/deals/history behave like MT5's."""

    def __init__(self, t, live=True):
        self.t, self.live, self.offset, self.magic = float(ep(t)), live, 0.0, RT.MAGIC
        self.specs = {p + "z": spec_for(p) for p in PAIRS}
        self.ticks, self.bars = {}, {}
        self._orders, self._pos, self._hist, self._deals = {}, {}, {}, []
        self.sent, self.fail, self.connected = [], [], True
        self._tk, self._dk = 5000, 90000
        for p in PAIRS:
            self.set_px(p, PX[p])
            self.make_bars(p, PX[p], ep(t) - 2 * 86400, 6 * 86400)

    # ---- test controls
    def set_px(self, pair, bid, spread_pts=2):
        s = self.specs[pair + "z"]
        self.ticks[pair + "z"] = NS(bid=bid, ask=round(bid + spread_pts * s.point, s.digits), time=int(self.t))

    def make_bars(self, pair, px, t0, span_s):
        t = pd.date_range(pd.Timestamp(t0, unit="s").floor("min"), periods=int(span_s // 60), freq="min")
        self.bars[pair + "z"] = pd.DataFrame(dict(time=t, open=px, high=px, low=px, close=px, spr=0.0))

    def set_bar(self, pair, t, **kw):
        b = self.bars[pair + "z"]
        i = b.index[b.time == pd.Timestamp(t)]
        for k, v in kw.items():
            b.loc[i, k] = v

    def advance(self, sec):
        self.t += sec
        for s, tk in self.ticks.items():
            tk.time = int(self.t)

    def _new_ticket(self):
        self._tk += 1
        return self._tk

    def _deal(self, pos, entry, price, reason, profit=0.0, commission=0.0, comment=""):
        self._dk += 1
        d = NS(ticket=self._dk, order=pos.ticket, position_id=pos.ticket, symbol=pos.symbol,
               type=pos.type if entry == mt5.DEAL_ENTRY_IN else 1 - pos.type, entry=entry, reason=reason,
               volume=pos.volume, price=price, profit=profit, commission=commission, swap=0.0, fee=0.0,
               magic=pos.magic, comment=comment, time=int(self.t), time_msc=int(self.t * 1000))
        self._deals.append(d)
        return d

    def open_position(self, sym, side, vol, price, sl, tp, comment, magic=None, ticket=None):
        tk = ticket or self._new_ticket()
        p = NS(ticket=tk, symbol=sym, type=0 if side == 1 else 1, price_open=price, sl=sl, tp=tp, volume=vol,
               magic=self.magic if magic is None else magic, comment=comment, time=int(self.t), profit=0.0, swap=0.0,
               price_current=price)
        self._pos[tk] = p
        d = self._deal(p, mt5.DEAL_ENTRY_IN, price, mt5.DEAL_REASON_EXPERT, commission=-COMM_PER_LOT * vol,
                       comment=comment)
        return p, d

    def close_position(self, tk, price, reason=mt5.DEAL_REASON_SL):
        p = self._pos.pop(tk)
        s = self.specs[p.symbol]
        d = 1 if p.type == 0 else -1
        prof = round((price - p.price_open) * d / s.trade_tick_size * s.trade_tick_value * p.volume, 2)
        return self._deal(p, mt5.DEAL_ENTRY_OUT, price, reason, profit=prof,
                          comment={mt5.DEAL_REASON_SL: "[sl]", mt5.DEAL_REASON_TP: "[tp]"}.get(reason, ""))

    def fill_order(self, tk, price=None):
        o = self._orders.pop(tk)
        price = o.price_open if price is None else price
        o.state, o.time_done, o.position_id = mt5.ORDER_STATE_FILLED, int(self.t), tk
        self._hist[tk] = o
        side = 1 if o.type == mt5.ORDER_TYPE_BUY_LIMIT else -1
        return self.open_position(o.symbol, side, o.volume_initial, price, o.sl, o.tp, o.comment, ticket=tk)

    def expire_order(self, tk):
        o = self._orders.pop(tk)
        o.state, o.time_done = mt5.ORDER_STATE_EXPIRED, int(self.t)
        self._hist[tk] = o

    # ---- MT5Gateway interface
    def now(self):
        return self.t

    def ensure(self):
        return self.connected

    def account(self):
        return NS(balance=450.0, equity=450.0)

    def spec(self, s):
        return self.specs[s]

    def tick(self, s):
        return self.ticks.get(s)

    def loss_per_lot(self, s, dist):
        i = self.specs[s]
        return dist / i.trade_tick_size * i.trade_tick_value_loss

    def positions(self):
        return [p for p in self._pos.values() if p.magic == self.magic]

    def orders(self):
        return [o for o in self._orders.values() if o.magic == self.magic]

    def history_orders(self, t0, t1):
        return [o for o in self._hist.values() if t0 <= o.time_setup <= t1 and o.magic == self.magic]

    def history_order(self, tk):
        return self._hist.get(tk)

    def deals_for_position(self, pid):
        return [d for d in self._deals if d.position_id == pid]

    def deals_between(self, t0, t1):
        return [d for d in self._deals if t0 <= d.time <= t1 and d.magic == self.magic]

    def deal_position(self, deal):
        return next((d.position_id for d in self._deals if d.ticket == deal), None)

    def m1(self, s, t_from, closed_only=True):
        b = self.bars[s]
        tt = b.time.values.astype("datetime64[s]").astype(np.int64)
        k = (tt >= t_from) & ((tt + 60 <= self.t) if closed_only else (tt <= self.t))
        return b[k].reset_index(drop=True)

    def last_error(self):
        return (1, "Success")

    def send(self, req):
        if not self.live:
            raise RuntimeError("dry-run: order_send is disabled")
        self.sent.append(dict(req))
        mode = self.fail.pop(0) if self.fail else None
        if mode == "none_noexec":
            return None
        if isinstance(mode, int):
            return NS(retcode=mode, order=0, deal=0, price=0.0, volume=0.0, comment="mock reject")
        r = self._exec(req)
        if mode == "none_exec":
            return None
        if mode == "timeout_exec":
            return NS(retcode=mt5.TRADE_RETCODE_TIMEOUT, order=0, deal=0, price=0.0, volume=0.0, comment="timeout")
        return r

    def _exec(self, req):
        a = req["action"]
        if a == mt5.TRADE_ACTION_PENDING:
            tk = self._new_ticket()
            o = NS(ticket=tk, symbol=req["symbol"], type=req["type"], price_open=req["price"], sl=req["sl"],
                   tp=req["tp"], volume_initial=req["volume"], volume_current=req["volume"], magic=req["magic"],
                   comment=req["comment"], time_setup=int(self.t), time_expiration=req.get("expiration", 0),
                   type_time=req["type_time"], type_filling=req["type_filling"], state=mt5.ORDER_STATE_PLACED,
                   position_id=0, time_done=0, price_current=self.ticks[req["symbol"]].bid)
            self._orders[tk] = o
            self._hist[tk] = o
            return NS(retcode=mt5.TRADE_RETCODE_PLACED, order=tk, deal=0, price=0.0, volume=req["volume"], comment="")
        if a == mt5.TRADE_ACTION_REMOVE:
            o = self._orders.pop(req["order"], None)
            if o is None:
                return NS(retcode=mt5.TRADE_RETCODE_INVALID, order=0, deal=0, price=0.0, volume=0.0, comment="no order")
            o.state, o.time_done = mt5.ORDER_STATE_CANCELED, int(self.t)
            self._hist[o.ticket] = o
            return NS(retcode=mt5.TRADE_RETCODE_DONE, order=o.ticket, deal=0, price=0.0, volume=0.0, comment="")
        if a == mt5.TRADE_ACTION_SLTP:
            p = self._pos[req["position"]]
            p.sl, p.tp = req["sl"], req["tp"]
            return NS(retcode=mt5.TRADE_RETCODE_DONE, order=0, deal=0, price=0.0, volume=0.0, comment="")
        if a == mt5.TRADE_ACTION_DEAL and "position" in req:
            d = self.close_position(req["position"], req["price"], mt5.DEAL_REASON_EXPERT)
            return NS(retcode=mt5.TRADE_RETCODE_DONE, order=d.order, deal=d.ticket, price=req["price"],
                      volume=req["volume"], comment="")
        if a == mt5.TRADE_ACTION_DEAL:
            side = 1 if req["type"] == mt5.ORDER_TYPE_BUY else -1
            p, d = self.open_position(req["symbol"], side, req["volume"], req["price"], req["sl"], req["tp"],
                                      req["comment"])
            self._hist[p.ticket] = NS(ticket=p.ticket, symbol=p.symbol, type=req["type"], price_open=req["price"],
                                      volume_initial=req["volume"], magic=req["magic"], comment=req["comment"],
                                      time_setup=int(self.t), state=mt5.ORDER_STATE_FILLED, position_id=p.ticket,
                                      time_done=int(self.t), sl=req["sl"], tp=req["tp"], time_expiration=0)
            return NS(retcode=mt5.TRADE_RETCODE_DONE, order=p.ticket, deal=d.ticket, price=req["price"],
                      volume=req["volume"], comment="")
        raise ValueError(req)


class FakeSource:
    """Stands in for LiveSource: per pair the current closed-bar time T and the REF orders the engine returns."""

    def __init__(self):
        self.T, self.orders, self.shadow_df, self.n_eval = {}, defaultdict(list), {}, 0

    def last_T(self, pair):
        return pd.Timestamp(self.T[pair]) if pair in self.T else None

    def evaluate(self, pair):
        self.n_eval += 1
        T = pd.Timestamp(self.T[pair])
        rec = [o for o in self.orders[pair] if T - pd.Timedelta(days=3) <= pd.Timestamp(o["t_signal"]) <= T]
        act = [o for o in rec if pd.Timestamp(o["t_signal"]) <= T < pd.Timestamp(o["valid_until"])]
        return dict(pair=pair, t_bar_close=str(T), stage=4 if act else 1, warnings=[], orders_recent=rec,
                    signals_active=act, signals_new=[o for o in act if pd.Timestamp(o["t_signal"]) == T],
                    bias=dict(txt="BUY"))

    def shadow(self, pair, T):
        return self.shadow_df.get(pair)


def sig(pair, t, side, entry, sl, tp):
    t = pd.Timestamp(t)
    return dict(pair=pair, t_signal=str(t), side=side, side_txt="BUY" if side == 1 else "SELL", entry=entry, sl=sl,
                tp=tp, rr=round((tp - entry) / (entry - sl), 2), valid_until=str(t + pd.Timedelta(minutes=60)),
                session="LDN", killzone="LDN", t_touch=str(t - pd.Timedelta(minutes=20)),
                poi_created=str(t - pd.Timedelta(hours=3)), poi_kind="OB", entry_why="t", sl_why="t", tp_why="t")


class Base(unittest.TestCase):
    T0 = "2026-09-30 10:35:03"                                   # Wednesday, 3 s after the 10:35 M5 close

    def setUp(self):
        self.dirs = []

    def tearDown(self):
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)

    def mk(self, t=None, live=True, out=None, gw=None, src=None, **cfg):
        gw = gw or MockGW(t or self.T0, live)
        src = src or FakeSource()
        if out is None:
            out = tempfile.mkdtemp(prefix="reftr_")
            self.dirs.append(out)
        tr = RT.Trader(gw, RT.Config(live=live, **cfg), src, out_dir=out)
        return gw, src, tr, out

    def signal(self, gw, src, tr, pair, t, side, entry, sl, tp, cycle=True):
        src.orders[pair].append(sig(pair, t, side, entry, sl, tp))
        src.T[pair] = str(pd.Timestamp(t))
        if cycle:
            tr.cycle()
        return tr.st["signals"][RT.sid_of(pair, t, side)]

    def csv(self, out, name):
        f = os.path.join(out, name)
        return pd.read_csv(f) if os.path.exists(f) else pd.DataFrame()


# ============================================================================================== tests
class TestRequest(Base):
    def test_buy_limit_request_fields(self):
        gw, src, tr, out = self.mk()
        rec = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        self.assertEqual(len(gw.sent), 1)
        r = gw.sent[0]
        self.assertEqual(r["action"], mt5.TRADE_ACTION_PENDING)
        self.assertEqual(r["symbol"], "EURUSDz")
        self.assertEqual(r["type"], mt5.ORDER_TYPE_BUY_LIMIT)
        self.assertAlmostEqual(r["volume"], 0.05)                # $5 / (10 pips x $10/pip/lot)
        self.assertEqual((r["price"], r["sl"], r["tp"]), (1.169, 1.168, 1.172))
        self.assertEqual(r["magic"], 770077)
        self.assertEqual(r["comment"], "REF-EURUSD-2609301035B")
        self.assertLessEqual(len(r["comment"]), 31)
        self.assertEqual(r["type_time"], mt5.ORDER_TIME_SPECIFIED)
        self.assertEqual(r["expiration"], int(ep("2026-09-30 11:35:00")))
        self.assertEqual(r["type_filling"], mt5.ORDER_FILLING_RETURN)
        self.assertEqual(rec["status"], RT.PENDING)
        self.assertEqual(rec["expiry_mode"], "broker")
        o = self.csv(out, "orders.csv")
        self.assertEqual(o.iloc[0].status, "PENDING")
        for c in ("t_zone", "t_touch", "t_signal", "valid_until", "sl_why", "tp_why", "rr", "lot", "risk_usd",
                  "t_sent", "t_placed", "retcodes", "request"):
            self.assertIn(c, o.columns)
        self.assertIn("10008", o.iloc[0].retcodes)

    def test_sell_limit_and_rounding(self):
        gw, src, tr, out = self.mk()
        self.signal(gw, src, tr, "USDJPY", "2026-09-30 10:35", -1, 150.1004, 150.2504, 149.6004)
        r = gw.sent[0]
        self.assertEqual(r["type"], mt5.ORDER_TYPE_SELL_LIMIT)
        self.assertEqual((r["price"], r["sl"], r["tp"]), (150.1, 150.25, 149.6))
        self.assertAlmostEqual(r["volume"], 0.05)                # 15 pips x 0.635 $/point/lot = $95.3/lot

    def test_gtc_fallback_when_specified_expiry_unsupported(self):
        gw, src, tr, out = self.mk()
        gw.specs["EURUSDz"].expiration_mode = 1 | 2              # GTC + DAY only
        rec = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        r = gw.sent[0]
        self.assertEqual(r["type_time"], mt5.ORDER_TIME_GTC)
        self.assertNotIn("expiration", r)
        self.assertEqual(rec["expiry_mode"], "self")

    def test_marketable_limit_becomes_market_order(self):
        gw, src, tr, out = self.mk()                             # ask 1.17002 <= entry 1.17010: simulate fills at open
        rec = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.17010, 1.16910, 1.17310)
        r = gw.sent[0]
        self.assertEqual(r["action"], mt5.TRADE_ACTION_DEAL)
        self.assertEqual(r["type"], mt5.ORDER_TYPE_BUY)
        self.assertEqual(r["price"], 1.17002)
        self.assertEqual((r["sl"], r["tp"]), (1.1691, 1.1731))
        self.assertEqual(r["type_filling"], mt5.ORDER_FILLING_IOC)
        self.assertAlmostEqual(r["volume"], 0.05)                # stop from the market price: 9.2 pips -> 0.054 -> 0.05
        self.assertEqual(rec["status"], RT.FILLED)
        self.assertEqual(rec["order_kind"], "market")

    def test_gap_beyond_stop_and_tp_already_traded_are_skipped(self):
        gw, src, tr, out = self.mk()
        gw.set_px("EURUSD", 1.16850)
        rec = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.17000, 1.16900, 1.17300)
        self.assertEqual(rec["status"], RT.SKIPPED)
        self.assertTrue(rec["status_reason"].startswith("gap"))
        gw.set_px("GBPUSD", 1.33850)
        rec = self.signal(gw, src, tr, "GBPUSD", "2026-09-30 10:35", -1, 1.34200, 1.34300, 1.33900)
        self.assertTrue(rec["status_reason"].startswith("tp_reached"))
        self.assertEqual(gw.sent, [])
        self.assertEqual(len(self.csv(out, "skips.csv")), 2)

    def test_invalid_fill_falls_back_to_next_filling_mode(self):
        gw, src, tr, out = self.mk()
        gw.fail = [mt5.TRADE_RETCODE_INVALID_FILL]
        rec = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        self.assertEqual([r["type_filling"] for r in gw.sent], [mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_IOC])
        self.assertEqual(rec["status"], RT.PENDING)


class TestSizing(Base):
    def test_size_lot_function(self):
        f = lambda per_lot: RT.size_lot(5.0, per_lot, 0.01, 0.01, 200, 2.0)          # noqa: E731
        self.assertEqual(f(100.0)[0], 0.05)
        self.assertEqual(f(30.0)[0], 0.16)                         # 0.1667 floored
        self.assertEqual(f(300.0)[0], 0.01)                        # exactly min-lot size by floor(0.0167)
        lot, note, risk = f(800.0)
        self.assertEqual(lot, 0.01); self.assertIn("min lot", note); self.assertAlmostEqual(risk, 8.0)
        lot, note, risk = f(1200.0)
        self.assertEqual(lot, 0.0); self.assertIn("would risk $12.00", note)
        self.assertEqual(f(0.0)[0], 0.0)

    def test_jpy_gold_chf_through_trader(self):
        cases = [("USDJPY", 1, 150.000, 149.850, 150.600, 0.05),     # $95.28/lot
                 ("EURJPY", -1, 175.100, 175.400, 174.100, 0.02),    # 30 pips: $190.6/lot -> 0.026
                 ("USDCHF", 1, 0.79900, 0.79800, 0.80300, 0.04),     # $119.85/lot
                 ("XAUUSD", 1, 3799.000, 3796.000, 3810.000, 0.01),  # $3 stop: $300/lot -> 0.0167
                 ("NZDUSD", 1, 0.57970, 0.57940, 0.58100, 0.16)]     # 3 pips: $30/lot
        gw, src, tr, out = self.mk(max_open=9)
        for p, d, e, sl, tp, want in cases:
            n = len(gw.sent)
            self.signal(gw, src, tr, p, "2026-09-30 10:35", d, e, sl, tp)
            self.assertEqual(len(gw.sent), n + 1, p)
            self.assertAlmostEqual(gw.sent[-1]["volume"], want, msg=p)

    def test_gold_min_lot_accept_and_skip(self):
        gw, src, tr, out = self.mk()
        rec = self.signal(gw, src, tr, "XAUUSD", "2026-09-30 10:35", 1, 3799.000, 3791.000, 3830.000)  # $8 at 0.01
        self.assertAlmostEqual(gw.sent[-1]["volume"], 0.01)
        self.assertAlmostEqual(rec["risk_usd"], 8.0)
        gw2, src2, tr2, out2 = self.mk()
        rec = self.signal(gw2, src2, tr2, "XAUUSD", "2026-09-30 10:35", 1, 3799.000, 3787.000, 3840.000)  # $12 > 2x5
        self.assertEqual(gw2.sent, [])
        self.assertTrue(rec["status_reason"].startswith("sizing"))


class TestOnePerPair(Base):
    def test_pending_limit_blocks_pair_other_pair_free(self):
        gw, src, tr, out = self.mk()
        self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        gw.advance(300)
        rec2 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:40", 1, 1.16950, 1.16850, 1.17250)
        self.assertEqual(rec2["status"], RT.SKIPPED)
        self.assertTrue(rec2["status_reason"].startswith("backtest_busy"), rec2["status_reason"])
        rec3 = self.signal(gw, src, tr, "GBPUSD", "2026-09-30 10:40", 1, 1.33900, 1.33800, 1.34200)
        self.assertEqual(rec3["status"], RT.PENDING)
        self.assertEqual(len(gw.sent), 2)

    def test_live_position_blocks_even_if_backtest_twin_is_free(self):
        gw, src, tr, out = self.mk()
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        gw.fill_order(r1["ticket"])                               # live fill; the twin's bars never reach 1.169
        gw.advance(3900)                                          # 11:40:03, twin: not_filled, busy until 11:35
        tr.cycle()
        self.assertEqual(r1["status"], RT.FILLED)
        rec2 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 11:40", 1, 1.16950, 1.16850, 1.17250)
        self.assertTrue(rec2["status_reason"].startswith("live_busy"), rec2["status_reason"])
        self.assertEqual(len(gw.sent), 1)

    def test_unfilled_limit_keeps_pair_busy_until_expiry_after_tp_first_cancel(self):
        gw, src, tr, out = self.mk()
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        gw.set_bar("EURUSD", "2026-09-30 10:50", high=1.17250)   # TP traded while the limit is pending
        gw.advance(16 * 60)                                       # 10:51:03
        tr.cycle()
        self.assertEqual(r1["status"], RT.CANCELLED)
        self.assertEqual(r1["cancel_reason"], "TP_FIRST")
        self.assertEqual(gw.sent[-1]["action"], mt5.TRADE_ACTION_REMOVE)
        gw.advance(9 * 60)                                        # 11:00:03: live free, backtest busy until 11:35
        rec2 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 11:00", 1, 1.16950, 1.16850, 1.17250)
        self.assertTrue(rec2["status_reason"].startswith("backtest_busy"), rec2["status_reason"])
        gw.advance(40 * 60)                                       # 11:40:03
        rec3 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 11:40", 1, 1.16950, 1.16850, 1.17250)
        self.assertEqual(rec3["status"], RT.PENDING, rec3["status_reason"])

    def test_max_open(self):
        gw, src, tr, out = self.mk()
        for p in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"):
            px = PX[p]
            self.signal(gw, src, tr, p, "2026-09-30 10:35", 1, px - 0.001, px - 0.002, px + 0.003)
        rec = self.signal(gw, src, tr, "USDCAD", "2026-09-30 10:35", 1, 1.38900, 1.38800, 1.39200)
        self.assertTrue(rec["status_reason"].startswith("max_open"), rec["status_reason"])
        self.assertEqual(len(gw.sent), 4)


class TestTimeRules(Base):
    def test_expiry_self_cancel_and_broker_expiry(self):
        gw, src, tr, out = self.mk()
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        gw.advance(3600 - 3)                                       # 11:35:00: broker has not expired it yet
        tr.cycle()
        self.assertEqual(r1["status"], RT.PENDING)
        gw.advance(4)                                              # 11:35:04 > expire + 3 s grace
        tr.cycle()
        self.assertEqual(r1["status"], RT.CANCELLED)
        self.assertEqual(r1["cancel_reason"], "EXPIRY_SELF")
        # broker-side expiry is recognised from history
        r2 = self.signal(gw, src, tr, "GBPUSD", "2026-09-30 11:35", 1, 1.33900, 1.33800, 1.34200)
        gw.advance(3600)
        gw.expire_order(r2["ticket"])
        tr.cycle()
        self.assertEqual(r2["status"], RT.EXPIRED)

    def test_gtc_mode_cancels_exactly_at_expiry(self):
        gw, src, tr, out = self.mk()
        gw.specs["EURUSDz"].expiration_mode = 1
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        gw.advance(3600 - 3)                                       # exactly valid_until
        tr.cycle()
        self.assertEqual(r1["status"], RT.CANCELLED)

    def test_weekend_flatten(self):
        gw, src, tr, out = self.mk(t="2026-10-02 20:10:03")      # Friday
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-10-02 20:10", 1, 1.16900, 1.16800, 1.17200)
        r2 = self.signal(gw, src, tr, "GBPUSD", "2026-10-02 20:10", 1, 1.34010, 1.33900, 1.34400)  # marketable
        self.assertEqual((r1["status"], r2["status"]), (RT.PENDING, RT.FILLED))
        gw.advance(34 * 60 + 20)                                   # 20:44:23
        tr.cycle()
        self.assertEqual(len(gw.sent), 2)
        gw.advance(40)                                             # 20:45:03
        tr.cycle()
        acts = [(r["action"], r.get("position")) for r in gw.sent[2:]]
        self.assertIn((mt5.TRADE_ACTION_REMOVE, None), acts)
        self.assertIn((mt5.TRADE_ACTION_DEAL, r2["position_id"]), acts)
        self.assertEqual(r1["cancel_reason"], "WEEKEND")
        tr.cycle()                                                 # closing deal -> trade logged
        self.assertEqual(r2["status"], RT.CLOSED)
        t = self.csv(out, "trades.csv")
        self.assertEqual(t.iloc[0].exit_reason, "WEEKEND")
        rec = self.signal(gw, src, tr, "USDJPY", "2026-10-02 20:45", 1, 149.900, 149.800, 150.300)
        self.assertTrue(rec["status_reason"].startswith("market_closing"))

    def test_holiday_flatten(self):
        gw, src, tr, out = self.mk(t="2026-12-24 21:40:03")      # Thursday: FX closes 21:58
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-12-24 21:40", 1, 1.17010, 1.16900, 1.17400)
        self.assertEqual(r1["status"], RT.FILLED)
        gw.advance(6 * 60 + 50)                                   # 21:46:53 < 21:48
        tr.cycle()
        self.assertEqual(len(gw.sent), 1)
        gw.advance(70)                                            # 21:48:03
        tr.cycle()
        self.assertEqual(gw.sent[-1].get("position"), r1["position_id"])
        self.assertTrue(r1["close_reason"].startswith("HOLIDAY"))
        self.assertTrue(tr.flat_reason(ep("2026-12-24 21:48:03"), "XAUUSD").startswith("HOLIDAY"))
        self.assertTrue(tr.flat_reason(ep("2026-12-24 18:20:00"), "XAUUSD").startswith("HOLIDAY"))
        self.assertIsNone(tr.flat_reason(ep("2026-12-24 18:20:00"), "EURUSD"))


class TestRestartAndDuplicates(Base):
    def test_restart_with_state_resumes_without_resending(self):
        gw, src, tr, out = self.mk()
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        gw.advance(5)
        src2 = FakeSource(); src2.T, src2.orders = dict(src.T), src.orders
        _, _, tr2, _ = self.mk(out=out, gw=gw, src=src2)
        tr2.cycle()
        self.assertEqual(len(gw.sent), 1)
        self.assertEqual(tr2.st["signals"][r1["sid"]]["status"], RT.PENDING)
        self.assertEqual(tr2.st["signals"][r1["sid"]]["ticket"], r1["ticket"])

    def test_restart_without_state_adopts_by_magic_and_comment(self):
        gw, src, tr, out = self.mk()
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        p, _ = gw.open_position("GBPUSDz", -1, 0.03, 1.34000, 1.34200, 1.33400, "REF-GBPUSD-2609301030S")
        gw.open_position("AUDUSDz", 1, 0.10, 0.66000, 0.65900, 0.66300, "costtest")      # our magic, not REF
        os.remove(os.path.join(out, "state.json"))
        gw.advance(5)
        _, src2, tr2, _ = self.mk(out=out, gw=gw)
        tr2.cycle()
        a = tr2.st["signals"][r1["sid"]]
        self.assertEqual((a["status"], a["ticket"], a["adopted"]), (RT.PENDING, r1["ticket"], True))
        self.assertAlmostEqual(a["sl"], 1.168)
        b = tr2.st["signals"]["GBPUSD-2609301030S"]
        self.assertEqual((b["status"], b["position_id"]), (RT.FILLED, p.ticket))
        self.assertEqual(len(gw.sent), 1)                          # nothing sent; costtest position untouched
        gw.set_px("GBPUSD", 1.34200)
        gw.advance(60)
        gw.close_position(p.ticket, 1.34202, mt5.DEAL_REASON_SL)
        tr2.cycle()
        t = self.csv(out, "trades.csv")
        self.assertEqual(t.iloc[-1].exit_reason, "SL")
        self.assertAlmostEqual(t.iloc[-1].usd_gross, -6.06, places=2)   # 20.2 pips x $0.30
        self.assertAlmostEqual(t.iloc[-1].commission, -0.15, places=2)
        self.assertAlmostEqual(t.iloc[-1].usd_net, -6.21, places=2)

    def test_dry_run_does_not_adopt_live_orders(self):
        gw, src, tr, out = self.mk()
        self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        gw.live = False
        _, src2, trd, outd = self.mk(gw=gw, live=False)
        trd.cycle()
        self.assertEqual(trd.st["signals"], {})
        rec = self.signal(gw, src2, trd, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        self.assertTrue(rec["status_reason"].startswith("live_busy"), rec["status_reason"])

    def test_unknown_outcome_executed_is_adopted_not_resent(self):
        gw, src, tr, out = self.mk()
        gw.fail = ["none_exec"]                                   # order placed, but order_send returned None
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        self.assertEqual(r1["status"], RT.UNKNOWN)
        for _ in range(3):
            gw.advance(20)
            tr.cycle()
        self.assertEqual(r1["status"], RT.PENDING)
        self.assertEqual(len(gw.sent), 1)
        self.assertEqual(r1["ticket"], gw.orders()[0].ticket)
        src.T["EURUSD"] = "2026-09-30 10:40:00"                   # re-evaluation still returns the same order
        gw.advance(240)
        tr.cycle()
        self.assertEqual(len(gw.sent), 1)
        self.assertEqual(len(gw.orders()), 1)

    def test_unknown_timeout_retcode_executed(self):
        gw, src, tr, out = self.mk()
        gw.fail = ["timeout_exec"]
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        self.assertEqual(r1["status"], RT.UNKNOWN)
        gw.advance(2)
        tr.cycle()
        self.assertEqual((r1["status"], len(gw.sent), len(gw.orders())), (RT.PENDING, 1, 1))

    def test_unknown_outcome_not_executed_resent_once_then_abandoned(self):
        gw, src, tr, out = self.mk()
        gw.fail = ["none_noexec", "none_noexec"]
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        gw.advance(2); tr.cycle()
        gw.advance(2); tr.cycle()
        self.assertEqual(len(gw.sent), 1)                          # within grace: no resend
        for _ in range(3):
            gw.advance(12); tr.cycle()
        self.assertEqual(len(gw.sent), 2)                          # confirmed absent after grace: ONE resend
        for _ in range(6):
            gw.advance(12); tr.cycle()
        self.assertEqual(len(gw.sent), 2)
        self.assertEqual(r1["status"], RT.ABANDONED)

    def test_crash_between_state_write_and_send(self):
        gw, src, tr, out = self.mk()
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        with open(os.path.join(out, "state.json"), encoding="utf-8") as fh:
            st = json.load(fh)
        st["signals"][r1["sid"]].update(status="SENDING", ticket=None)   # as if we crashed before the response
        with open(os.path.join(out, "state.json"), "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        gw.advance(30)
        _, _, tr2, _ = self.mk(out=out, gw=gw, src=src)
        tr2.cycle()
        self.assertEqual(tr2.st["signals"][r1["sid"]]["status"], RT.PENDING)
        self.assertEqual(len(gw.sent), 1)


class TestMoneyAndLimits(Base):
    def test_daily_loss_stop_uses_ref_deals_only(self):
        gw, src, tr, out = self.mk()
        gw.advance(-2 * 3600)                                       # 08:35: a REF loser today
        p, _ = gw.open_position("EURUSDz", 1, 0.20, 1.17000, 1.16900, 1.17300, "REF-EURUSD-2609300835B")
        gw.close_position(p.ticket, 1.16895, mt5.DEAL_REASON_SL)    # -21.0 gross, -1.0 commission
        q, _ = gw.open_position("USDJPYz", 1, 1.0, 150.0, 149.0, 151.0, "costtest")
        gw.close_position(q.ticket, 149.0, mt5.DEAL_REASON_SL)      # large non-REF loss on our magic: ignored
        gw.advance(2 * 3600)
        rec = self.signal(gw, src, tr, "GBPUSD", "2026-09-30 10:35", 1, 1.33900, 1.33800, 1.34200)
        self.assertTrue(rec["status_reason"].startswith("daily_loss"), rec["status_reason"])
        self.assertIn("-22.00", rec["status_reason"])
        self.assertEqual(gw.sent, [])
        # the costtest-only case trades normally
        gw2, src2, tr2, _ = self.mk()
        gw2.advance(-3600)
        q, _ = gw2.open_position("USDJPYz", 1, 1.0, 150.0, 149.0, 151.0, "costtest")
        gw2.close_position(q.ticket, 149.0, mt5.DEAL_REASON_SL)
        gw2.advance(3600)
        rec = self.signal(gw2, src2, tr2, "GBPUSD", "2026-09-30 10:35", 1, 1.33900, 1.33800, 1.34200)
        self.assertEqual(rec["status"], RT.PENDING)

    def test_trade_row_from_broker_deals(self):
        gw, src, tr, out = self.mk()
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        gw.advance(120)
        gw.fill_order(r1["ticket"], 1.16898)                      # 0.2 pip better than the limit
        tr.cycle()
        self.assertEqual(r1["status"], RT.FILLED)
        gw.advance(3600)
        gw.close_position(r1["position_id"], 1.17200, mt5.DEAL_REASON_TP)
        tr.cycle()
        t = self.csv(out, "trades.csv").iloc[-1]
        self.assertEqual(t.exit_reason, "TP")
        self.assertAlmostEqual(t.slip_pips, -0.2, places=6)
        self.assertAlmostEqual(t.pips, 30.2, places=6)
        self.assertAlmostEqual(t.usd_gross, 15.1, places=2)
        self.assertAlmostEqual(t.commission, -0.25, places=2)
        self.assertAlmostEqual(t.usd_net, 14.85, places=2)
        self.assertAlmostEqual(t.R_net, round(14.85 / (0.05 * 9.8 * 10), 3), places=3)
        self.assertEqual(r1["status"], RT.CLOSED)

    def test_missing_sltp_is_reattached(self):
        gw, src, tr, out = self.mk()
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        p, _ = gw.fill_order(r1["ticket"])
        p.sl = p.tp = 0.0
        gw.advance(2)
        tr.cycle()
        self.assertEqual(gw.sent[-1]["action"], mt5.TRADE_ACTION_SLTP)
        self.assertEqual((p.sl, p.tp), (1.168, 1.172))


class TestDryRunAndTwin(Base):
    def test_dry_run_never_sends_and_logs_request(self):
        gw, src, tr, out = self.mk(live=False)
        rec = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.16900, 1.16800, 1.17200)
        self.assertEqual(rec["status"], RT.DRY)
        self.assertEqual(gw.sent, [])
        self.assertEqual(rec["request"]["type"], mt5.ORDER_TYPE_BUY_LIMIT)
        ev = self.csv(out, "order_events.csv")
        self.assertIn("would send", ev.iloc[-1].detail)
        gw.advance(300)                                            # the twin keeps the dry pair busy too
        rec2 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:40", 1, 1.16950, 1.16850, 1.17250)
        self.assertTrue(rec2["status_reason"].startswith("backtest_busy"))

    def test_twin_resolution_and_log(self):
        gw, src, tr, out = self.mk(live=False)
        r1 = self.signal(gw, src, tr, "EURUSD", "2026-09-30 10:35", 1, 1.17000, 1.16900, 1.17300)
        gw.set_bar("EURUSD", "2026-09-30 10:40", low=1.16995)     # fill (ask low = bid low + 0 spread)
        gw.set_bar("EURUSD", "2026-09-30 11:10", high=1.17310)    # TP
        gw.advance(40 * 60)
        tr.update_twins(force=True)
        self.assertEqual(r1["twin"]["status"], "filled")
        self.assertEqual(r1["twin"]["exit_reason"], "TP")
        self.assertTrue(r1["twin_final"])
        tw = self.csv(out, "twin.csv")
        self.assertEqual(tw.iloc[0].bt_exit_reason, "TP")
        self.assertAlmostEqual(tw.iloc[0].bt_R, (30 - 0.5) / 10, places=6)   # EURUSD commission 0.5 pip

    def test_resolve_rows_chain(self):
        out = pd.DataFrame([dict(status="filled", exit_reason="END", expire="2026-09-30 11:00"),
                            dict(status="not_filled", exit_reason=None, expire="2026-09-30 10:00"),
                            dict(status="skipped_busy", exit_reason=None, expire="2026-09-30 10:00")])
        r = RT.resolve_rows(out, pd.Timestamp("2026-09-30 12:00"), chain=True)
        self.assertEqual([x["resolved"] for x in r], [False, False, True])
        r = RT.resolve_rows(out, pd.Timestamp("2026-09-30 12:00"), chain=False)
        self.assertEqual([x["resolved"] for x in r], [False, True, True])

    def test_shadow_candidates_logged_and_labelled(self):
        gw, src, tr, out = self.mk(live=False)
        T = pd.Timestamp("2026-09-30 10:35")
        src.shadow_df["EURUSD"] = pd.DataFrame([dict(
            sym="EURUSD", t_signal=T, side=1, entry=1.17000, sl=1.16900, tp=1.17300, expire=T + pd.Timedelta(minutes=60),
            t_zone=T - pd.Timedelta(hours=5), zone_kind="FVG", ref_gate=False, bias_c=-1, dpos_raw=0.7, f_rr=1.1,
            f_dpos=0.7, ord=0)])
        src.T["EURUSD"] = str(T)
        tr.cycle()
        sh = self.csv(out, "shadow_candidates.csv")
        self.assertEqual(len(sh), 1)
        self.assertEqual(sh.iloc[0].cid, "EURUSD-2609301035B-2609300535F")
        gw.set_bar("EURUSD", "2026-09-30 10:40", low=1.16990)
        gw.set_bar("EURUSD", "2026-09-30 10:50", low=1.16890)     # SL
        gw.advance(3 * 3600)
        tr.update_shadow_labels()
        lab = self.csv(out, "shadow_labels.csv")
        self.assertEqual(lab.iloc[0].exit_reason, "SL")
        self.assertEqual(tr.st["shadow_open"], {})
        tr.cycle()                                                 # same candidate again: not logged twice
        self.assertEqual(len(self.csv(out, "shadow_candidates.csv")), 1)


# ============================================================================================== live smoke (read-only)
def smoke():
    """DRY-RUN against the running terminal: one cycle, state + requests printed. order_send/order_check are trapped."""
    import logging

    def trap(*a, **k):
        raise AssertionError("order_send/order_check called during the dry-run smoke test")
    mt5.order_send = trap
    mt5.order_check = trap
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    out = tempfile.mkdtemp(prefix="reftr_smoke_")
    cfg = RT.Config(live=False)
    gw = RT.connect(cfg)
    try:
        tr = RT.Trader(gw, cfg, RT.LiveSource(), out_dir=out)
        tr.cycle()
        tr.update_twins(force=True)
        tr.save()
        P, O = gw.positions(), gw.orders()
        print("\n=== SMOKE (dry-run, read-only) ===")
        print(tr.status_line(P, O))
        print(f"our magic {cfg.magic}: {len(P)} positions, {len(O)} pending orders (live terminal)")
        acct = gw.account()
        print(f"account: demo, balance {acct.balance:.2f} {acct.currency}")
        for r in sorted(tr.recs(), key=lambda r: r["t_signal"]):
            tw = r.get("twin") or {}
            print(f"  {r['sid']:22} {r['status']:8} {r['status_reason'][:70]:70} backtest-twin: {tw.get('status')}"
                  f" {tw.get('exit_reason') or ''} {'' if tw.get('R') is None else round(tw['R'], 2)}")
        for f in ("shadow_candidates.csv", "shadow_labels.csv", "twin.csv", "skips.csv"):
            p = os.path.join(out, f)
            print(f"  {f}: {len(pd.read_csv(p)) if os.path.exists(p) else 0} rows")
        shown = False
        for r in sorted(tr.recs(), key=lambda r: r["t_signal"], reverse=True):
            o = dict(t_signal=r["t_signal"], side=r["side"], entry=r["entry"], sl=r["sl"], tp=r["tp"], rr=r["rr"],
                     valid_until=r["valid_until"])
            rec, lot, note, risk, req, mk = tr.example_request(r["pair"], o)
            print(f"\nEXAMPLE REQUEST (never sent) for the latest REF order {rec['sid']}: "
                  f"{'MARKET (the limit would be marketable now)' if mk else 'LIMIT'}, lot {lot}, risk ${risk:.2f} {note}")
            print("  " + json.dumps(req, default=RT._jd))
            shown = True
            break
        if not shown:
            print("no REF order in the last 3 days -> no example request from a real signal")
        # SYNTHETIC orders at the current live prices/specs, only to show the exact request format and sizing
        now = pd.Timestamp(RT.iso(gw.now())).floor("5min")
        for pair, stop_pips in (("EURUSD", 10), ("USDJPY", 15), ("XAUUSD", 30)):
            t = gw.tick(pair + "z")
            if t is None or not t.bid:
                continue
            P_ = RT.pip(pair)
            e = t.bid - stop_pips * P_
            o = dict(t_signal=str(now), side=1, entry=e, sl=e - stop_pips * P_, tp=e + 3 * stop_pips * P_, rr=3.0,
                     valid_until=str(now + pd.Timedelta(minutes=60)))
            rec, lot, note, risk, req, mk = tr.example_request(pair, o)
            print(f"\nSYNTHETIC EXAMPLE (never sent): {pair} BUY LIMIT {stop_pips}-pip stop {stop_pips} pips below bid "
                  f"{t.bid}: lot {lot}, risk ${risk:.2f} {note}")
            print("  " + json.dumps(req, default=RT._jd))
        print(f"(smoke outputs in {out})")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "smoke":
        smoke()
    else:
        unittest.main(verbosity=2)

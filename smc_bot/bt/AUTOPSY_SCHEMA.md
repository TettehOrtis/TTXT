# REQUIRED OUTPUT — one JSON file per agent. Owner's instruction, 2026-10-07.

Every agent writes `smc_bot/results/<FOLDER>/autopsy.json` **in addition to** `report.md`. The owner reads
the JSON. Shabby work goes back for another pass.

## The three things the owner demands of every single variant

1. **What you did** — the rule as one mechanical sentence, and the measured result.
2. **Why it has no edge** — a named, evidenced reason. "It lost money" is not a reason. "Its direction
   call is worth nothing: always-long scores 44.5, always-short 36.9, the rule 41.0, a coin flip 40.7"
   is a reason.
3. **What you did to try to FIX that why, and what happened.** Every variant needs a `fix_attempts`
   chain. One attempt is not enough unless you can say why no second attempt exists. You keep going
   until either the reason is fixed, or you have run out of mechanical ideas, or another attempt would
   breach the overfitting caps — and you say which of those three ended it.

A variant with an empty `fix_attempts` list is incomplete work and will be sent back.

## Schema

```json
{
  "agent": "AUT08",
  "strategy": "Break of structure / CHoCH continuation",
  "feed": "dukascopy",
  "symbols": ["XAUUSD", "EURUSD"],
  "gold_spread_injected": 0.05,
  "bars_screened": true,
  "corrupt_bars_dropped": {"AUDJPY": 14155},
  "exam_opened": false,

  "conditions_tried": 36,
  "conditions_cap": 40,

  "variants": [
    {
      "id": "E1",
      "rule_sentence": "On H1, price closes beyond the last confirmed swing high; buy at the open of the next M1 bar.",
      "timeframes": {"signal": "H1", "entry": "M1", "trend": "H4"},

      "baseline": {
        "stage": "EXPLORE",
        "trades": 35398,
        "right_in_100": 37.2,
        "luck_bar": 38.97,
        "breakeven_needed": 43.5,
        "profit_factor": 0.789,
        "usd_per_trade": -1.76,
        "buy_right_in_100": 37.14,
        "buy_usd_per_trade": -1.72,
        "sell_right_in_100": 37.17,
        "sell_usd_per_trade": -1.81
      },

      "verdict": "no_edge",

      "why_no_edge": {
        "category": "negative_gross_edge",
        "one_sentence": "Price drifts slightly AGAINST the break after it is confirmed, so the gross edge is negative before costs are even charged.",
        "evidence": {
          "rule_vs_luck_bar_points": -1.77,
          "holds_in_validate": true,
          "holds_in_stress": true,
          "cost_share_of_shortfall": "3 of 6 points is cost; the other 3 is negative gross edge",
          "corroborating_prior_work": ["BT4 drift -0.02 to -0.13 ATR", "BT17 fade beat follow in 9 of 16"]
        }
      },

      "fix_attempts": [
        {
          "n": 1,
          "targets_which_why": "negative_gross_edge",
          "what_i_changed": "Required the break to clear a 40-bar extreme (a MAJOR break).",
          "why_i_thought_it_would_help": "SMC theory says only significant levels matter; a minor break is noise.",
          "explore": {"trades": 11851, "right_in_100": 36.53, "usd_per_trade": -1.93},
          "validate": null,
          "points_gained": -0.62,
          "usd_gained": -0.17,
          "outcome": "made_it_worse",
          "note": "Did not advance to VALIDATE because it failed on EXPLORE."
        },
        {
          "n": 2,
          "targets_which_why": "shape",
          "what_i_changed": "Moved the stop from 1.0 ATR to the origin of the impulse leg (median 5.4 ATR).",
          "why_i_thought_it_would_help": "A 1.0-ATR stop sits inside normal noise and gets clipped; the leg origin is where the idea is actually wrong.",
          "explore": {"trades": 32514, "right_in_100": 75.1, "profit_factor": 0.988, "usd_per_trade": -0.17},
          "validate": null,
          "points_gained": 37.8,
          "usd_gained": 1.13,
          "outcome": "improved_but_still_negative"
        },
        {
          "n": 3,
          "targets_which_why": "cost",
          "what_i_changed": "Combined the leg-origin stop with a 12:00-16:00 UTC window (refinement R1).",
          "why_i_thought_it_would_help": "The overlap was the only clock window where both the win rate and the dollars improved.",
          "explore": {"trades": 7717, "right_in_100": 75.0, "profit_factor": 1.081, "usd_per_trade": 1.1},
          "validate": {"trades": 9770, "right_in_100": 73.4, "profit_factor": 0.938, "usd_per_trade": -0.73},
          "stress": {"trades": 6558, "profit_factor": 0.912, "usd_per_trade": -0.85},
          "stress_adverse": {"usd_per_trade": -1.79},
          "points_gained": 37.8,
          "usd_gained": 2.86,
          "outcome": "made_money_on_explore_died_at_validate"
        }
      ],

      "fix_exhausted_because": "conditions_cap_reached",
      "fix_exhausted_detail": "36 of 40 conditions used. Every structure-quality condition in the catalogue made it worse; the only two that helped were clocks, and the best clock-plus-stop combination died at VALIDATE. A further attempt would be selecting on VALIDATE, which the brief forbids.",

      "best_surviving_version": null,

      "what_i_would_change": [
        "Stop using the break as a trigger; keep it as context and as the invalidation level.",
        "Put the stop at the impulse leg origin, never at the broken swing level.",
        "Block 17:00-23:00 UTC entirely.",
        "Test the fade with the same costs and the same confirmation lag."
      ]
    }
  ],

  "cross_variant_findings": [
    {
      "finding": "Every level-quality condition made results worse; only clocks helped.",
      "replicated_on": ["25 pairs", "3 stages", "6 EXPLORE years"]
    }
  ],

  "post_hoc_untested": [
    "Fading the break instead of following it — not in this brief, never measured here."
  ]
}
```

## Field rules

- `verdict`: one of `"edge_survives"`, `"no_edge"`, `"untestable_on_this_data"`.
- `why_no_edge.category`: one of
  `"cost"` (the gross edge is real but smaller than the round trip),
  `"negative_gross_edge"` (price drifts against the signal; worse than random),
  `"direction_call_worthless"` (always-long/always-short brackets the rule; it beats a coin flip by nothing),
  `"shape"` (the target/stop asks for a hit rate nothing delivers),
  `"regime_artifact"` (it was one direction of one market in one period),
  `"data_artifact"` (look-ahead, corrupt bars, missing spread, missing volume),
  `"untestable"` (the data cannot answer the question).
- `fix_attempts[].outcome`: one of `"fixed_and_survived"`, `"made_money_on_explore_died_at_validate"`,
  `"improved_but_still_negative"`, `"no_change"`, `"made_it_worse"`.
- `fix_exhausted_because`: one of `"reason_fixed"`, `"out_of_mechanical_ideas"`, `"conditions_cap_reached"`,
  `"would_require_tuning_on_validate"`.
- Every number is a real measured number. `null` where not run, never a guess, never a placeholder.
- `points_gained` and `usd_gained` are measured against that variant's own baseline.

## The mandatory controls, per variant

Each variant's JSON must carry these or say why not:

1. **Luck bar**, shape-matched and hour-matched. If it is not shape-matched, say so — AUT08 correctly
   flagged one that was not.
2. **Always-long / always-short / coin flip**, so the direction call is a pick between measured outcomes.
   AUT05 invented this and it has since overturned conclusions in two other agents' work. Mandatory now.
3. **Buy and sell reported separately.** EXPLORE is 1.8 years of one-directional gold.
4. **Look-ahead size with its sign**, from filling at the signal bar's own open. "No difference" is not
   proof of cleanliness; it can be two biases cancelling.

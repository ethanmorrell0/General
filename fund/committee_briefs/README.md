# Investment committee: how a run convenes it

The rules choose the trades. The committee checks every **buy** for things the rules
can't see (news, bad data, concentration) and can only **approve**, **delay** (7 days)
or **veto** (30 days) it. It never picks stocks and never blocks a sale.

Run it after `python3 -m fund.run daily` and before writing the day's note, only when
`python3 -m fund.committee needed` prints `yes` (a rebalance or first build with buys
that haven't been reviewed). Otherwise skip this whole section.

1. `python3 -m fund.committee packet` writes `fund/state/review_packet.json`: the buys,
   three pie-B reserves (next in line if a buy is vetoed), the fund's holdings and
   sector/country mix, price data checks, and the market backdrop.
2. **Analysts (in parallel).** Start three subagents with the Agent tool
   (general-purpose) in one message. Give each the full text of its brief and the full
   packet JSON, and ask for the JSON report its brief defines and nothing else:
   `analyst_news.md`, `analyst_data.md`, `analyst_risk.md`.
3. **Reviewer.** Start one subagent with `reviewer.md`, the packet and the three
   reports. It checks the evidence and returns one consolidated report.
4. **CIO.** Start one subagent with `cio.md`, the packet and the reviewer's report. It
   returns the verdict JSON. Save it to `fund/state/verdict.json`.
5. `python3 -m fund.committee apply fund/state/verdict.json` swaps out anything
   vetoed or delayed (pie A's go to the All-World core, pie B's to the first approved
   reserve, else cash) and records the review.
6. The day's note (step 6 of the daily routine) says in one sentence what the
   committee decided, naming any vetoed or delayed buy and why.

If an agent fails or returns something unusable, retry it once. If it still fails,
write a verdict that approves everything with the summary "Committee review
incomplete: <which step failed>" so the rule-based decision still goes out, and say so
in the note.

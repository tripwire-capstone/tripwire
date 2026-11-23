# Cluster-quality experiments (phase 1)

Scored with `./score run --runs park2 park4 tau1 --out out/<name>` (scorer-v1, hash 3045cff0…).
Keep rule: park F0.5 average for Issues or User frustration up by more than 0.05 with the other
not down, answer-key precision >= 0.85, tau1 Issues % unclustered not higher. Noise is about
±0.05 per run at identical settings.

## Baseline (`out/baseline`, commit 036d9bd, threshold 0.24, prompt v5)

| run | Issues P / R / F0.5 | frustration P / R / F0.5 | Issues clusters, % unclustered |
|---|---|---|---|
| park2 | 0.95 / 0.60 / 0.849 | 0.96 / 0.49 / 0.808 | 12, 26.5% |
| park4 | 0.88 / 0.47 / 0.754 | 0.92 / 0.57 / 0.817 | 11, 29.6% |
| **park avg** | **F0.5 0.80** | **F0.5 0.81** | |
| tau1 | - | - | 4, **55.3%** |

Diagnosis (replay at 0.24, labeled events only): same-cause clusters with centers well inside the
threshold of each other never merge (park2 frustration: large_party C13/C7 0.11 apart, glacier
C18/C19 0.17; park4 Issues: date_shift C18/C19 0.15). The one cross-cause pair under 0.24 is
park4 frustration decoy/large_party at 0.22. Most unclustered labeled events sit 0.25-0.30 from
the right cluster's center.

## Exp 1: merge close clusters (reverted)

- **Hypothesis:** clusters form next to each other from successive pool groups, then their
  centers drift together; nothing compares clusters, so one cause stays split.
- **Change:** new step 6 `merge.py`: within a Signal, merge the closest pair of leaf clusters
  whose centers are <= `CLUSTER_MERGE_THRESHOLD` (0.20, between the same-cause pairs at
  0.11-0.17 and the cross-cause pair at 0.22), recompute the center, repeat. Older cluster
  survives; the absorbed row is kept (no delete) with no members, no center, `parent_id` = survivor.
  6 new tests; full suite 768 passed.
- **Scores (`out/exp1-merge020`):** park2 Issues 0.845, frustration 0.887; park4 Issues 0.775,
  frustration 0.817. **Park avg Issues 0.81 (+0.01), frustration 0.85 (+0.04).** Precision
  0.86-0.98. tau1 Issues unclustered 55.3% (same).
- **Verdict:** reverted: +0.04 is under the +0.05 bar (all the gain is park2; park4
  frustration had no close pair). Patch kept outside the repo for reuse.
- **Cost:** $0.

## Exp 2: join radius larger than the grouping threshold (reverted)

- **Hypothesis:** most unclustered labeled events sit 0.25-0.30 from the right cluster's center;
  joining uses the same 0.24 as pool grouping. A wider join radius alone should recover them
  without the looser pool grouping that cost precision in the Phase 0 threshold sweep.
- **Change:** `CLUSTER_JOIN_THRESHOLD` = 0.28 used by assign (step 2); grouping stays at 0.24.
  Full suite: 761 passed, 1 failed (`test_run_pass_gives_assign_and_grouping_the_same_threshold`,
  which pins the old Q8 rule this experiment changes; left as is since the change was reverted).
- **Scores (`out/exp2-join028`):** park2 Issues 0.831 (P .91 R .62), frustration 0.852;
  park4 Issues 0.788 (**P 0.80** R .73), frustration 0.830. **Park avg Issues 0.81 (+0.01),
  frustration 0.84 (+0.03).** tau1 Issues unclustered 51.4% (down from 55.3%).
- **Verdict:** reverted: under the +0.05 bar, and park4 Issues precision falls below 0.85.
  Recall gains are real (park4 Issues R .47 -> .73) but F0.5 weighs the precision loss as much.
- **Cost:** $0.

## Exp 3: summarizer prompt v6, open with the trigger (KEPT)

- **Hypothesis:** v5 summaries of one cause split by what the assistant did next. Timeouts come
  out as "retries the same lookup after a timeout" or "describes the park without tool support";
  the shared trigger (the tool timed out) sits mid-sentence or only in the unembedded `cause`
  field. Opening every problem summary with its trigger should pull these together.
- **Change:** `prompts/summarize_v6.md` (+ schema, same as v5). Summary rule: "For a problem or a
  reaction, open with the cause, then what the assistant did about it ... Findings set off by the
  same cause must open the same way, even when the assistant's responses differ. Open with the
  assistant only when nothing before its own step set the finding off." `cause` asks for the
  earliest trigger; the first example is reworded to match. Suite 762 passed.
- **Scores (`out/exp3-v6`, `--as-version 6 --signals Issues "User frustration"`):**

  | run | Issues P / R / F0.5 | frustration P / R / F0.5 |
  |---|---|---|
  | park2 | 0.93 / 0.78 / 0.897 | 0.95 / 0.50 / 0.807 |
  | park4 | 0.94 / 0.76 / 0.894 | 0.93 / 0.61 / 0.840 |
  | **park avg** | **0.90 (+0.10)** | **0.82 (+0.01)** |

  tau1 Issues unclustered 52.5% (from 55.3%); tau1 Issues agreement P 0.84 (from 0.90), R 0.54 (same).
- **Control (`out/control-v5-resummarized`, `--as-version 5`):** re-summarizing with v5 itself
  gives Issues 0.80 / frustration 0.81 (park2 0.803 / 0.836, park4 0.801 / 0.793), i.e. the
  baseline. The gain is the prompt, not the act of re-summarizing.
- **Verdict:** kept. Both park runs improve on Issues (+0.05 and +0.14), precision stays >= 0.93,
  tau coverage improves. `SUMMARIZER_PROMPT_VERSION` default 5 -> 6 (settings.py, .env.example,
  test_settings.py).
- **Cost:** $0.54 (v6) + $0.49 (control) = $1.03. Per park run ~$0.20, not the ~$0.11 estimate.
  Running total: **$1.03**.
- **Note for later experiments:** the dumps hold v5 summaries, so anything on top of v6 must be
  scored with `--as-version 6` (~$0.54 per experiment).

## Exp 4: v6 + merge close clusters (reverted)

- **Hypothesis:** with v6 fixing the symptom-first wording, the remaining split (frustration
  stayed at 0.82) is the drift problem from Exp 1, so the merge step should now add its gain on top.
- **Change:** Exp 1's patch (merge.py, `CLUSTER_MERGE_THRESHOLD` 0.20, step 6) on top of v6.
  Suite 768 passed.
- **Scores (`out/exp4-v6-merge020`, `--as-version 6`), vs Exp 3:**

  | run | Issues F0.5 | frustration F0.5 |
  |---|---|---|
  | park2 | 0.902 (was 0.897) | 0.875 (was 0.807) |
  | park4 | 0.852 (was 0.894) | 0.864 (was 0.840) |
  | **park avg** | **0.88 (-0.02)** | **0.87 (+0.046)** |

  Precision 0.89-0.99. tau1 Issues unclustered 53.6% (was 52.5%).
- **Verdict:** reverted: frustration gain under +0.05, Issues down, tau coverage worse. Caveat:
  the comparison crosses two separate v6 re-summarizations, whose noise the v5 control puts at
  about ±0.03 per run, so this neither proves nor rules out a small merge benefit.
- **Cost:** $0.20 (cheaper than Exp 3 for the same calls, likely prompt caching). Running total: **$1.23**.

## Exp 5: v6 at threshold 0.26 (reverted, close call)

- **Hypothesis:** v6 changes how far apart summaries sit (v4 -> v5 moved the best threshold from
  0.18 to 0.24); under v5, 0.26 already helped frustration (0.86) but not Issues. With v6 holding
  Issues, 0.26 might lift frustration without the Issues cost.
- **Change:** none in code; `--threshold 0.26 --as-version 6` (would mean default 0.24 -> 0.26).
- **Scores (`out/exp5-v6-t026`), vs Exp 3:**

  | run | Issues P / R / F0.5 | frustration P / R / F0.5 |
  |---|---|---|
  | park2 | 0.90 / 0.74 / 0.860 (was 0.897) | 0.94 / 0.67 / 0.866 (was 0.807) |
  | park4 | 0.94 / 0.78 / 0.899 (was 0.894) | 0.95 / 0.71 / 0.888 (was 0.840) |
  | **park avg** | **0.88 (-0.016)** | **0.88 (+0.053)** |

  tau1 Issues unclustered **44.1%** (was 52.5%); tau1 Issues agreement P **0.71** (was 0.84), R 0.61.
- **Verdict:** reverted by the rule (Issues drops, even if within noise). The frustration gain
  is large and consistent across both park runs, and tau coverage improves a lot, but tau
  agreement precision falls, so tau clusters got broader. A user decision, ideally after more runs.
- **Cost:** $0.20. Running total: **$1.43**.

## Outcome

Kept: summarizer prompt v6 (`SUMMARIZER_PROMPT_VERSION` = 6). Park F0.5 Issues 0.80 -> 0.90,
frustration 0.81 -> 0.82; precision >= 0.93; tau1 Issues unclustered 55.3% -> 52.5%.
Reverted: cluster merge (Exp 1, Exp 4), separate join radius (Exp 2), threshold 0.26 on v6 (Exp 5).
Total spend $1.43 of $10.

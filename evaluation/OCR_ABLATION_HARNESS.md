# Bounded OCR ablation harness

This development-only harness measures the marginal contribution of bounded
OCR, crop/layout, geometry, orientation, and secondary-engine techniques. It
does not change the submitted runtime and it does not claim an unseen,
private, or official leaderboard score.

## Measurement contract

- `scripts/ocr_ablation.py run` is label-blind. It receives PDFs and one
  registered variant, but no truth file.
- `scripts/ocr_ablation.py report` runs only after predictions have been
  frozen. It scores them with the repository's official evaluator.
- Every variant differs from the baseline in exactly one declared boolean
  setting. Unknown settings, blanket high-DPI changes, and unconditional
  full-page secondary OCR are not registered.
- Run the baseline and each variant at least twice from fresh processes with
  the same source revision, PDF tree, worker count, and metrics method.
- Determinism requires byte-identical prediction files and byte-identical
  official evaluator results across repetitions.
- A technique is recommendation-eligible only when both sides of the
  comparison are deterministic and complete, its enabled form has positive
  score gain, and catastrophic false approvals do not increase.
- `score_gain_per_cpu_second` is the enabled technique's score contribution
  divided by the median CPU seconds of the complete run where it is enabled.
  The report also records incremental CPU efficiency when the median
  incremental cost is positive.

The built-in removal ablations cover selective PSM 6, cross-view consensus,
the normalized fee threshold crop, the normalized sparse-intake crop,
orientation retry, visible applicant-scope repair, risk-row geometry, and
RapidOCR routed only to unresolved output fields. Two additional removal
ablations independently measure bounded render-time deskew and the combined
visible stamp/correction/watermark/strikethrough cue path. Their target fields
match the Work Order's priority fields.

## Reproducible commands

List the exact baseline and one-variable variants:

```bash
python3 scripts/ocr_ablation.py list-variants
```

Run two fresh-process baseline repetitions:

```bash
python3 scripts/ocr_ablation.py run \
  --variant baseline \
  --benchmark-id public-full-1000-ocr-ablation-v1 \
  --source-revision <commit-sha> \
  --repeat 1 \
  --input-dir data/train \
  --predictions /tmp/ocr-ablation/baseline-1.jsonl \
  --observation /tmp/ocr-ablation/baseline-1.observation.json

python3 scripts/ocr_ablation.py run \
  --variant baseline \
  --benchmark-id public-full-1000-ocr-ablation-v1 \
  --source-revision <commit-sha> \
  --repeat 2 \
  --input-dir data/train \
  --predictions /tmp/ocr-ablation/baseline-2.jsonl \
  --observation /tmp/ocr-ablation/baseline-2.observation.json
```

Repeat those commands for each variant from `list-variants`, changing
`--variant`, `--repeat`, and the two output paths. Then create the aggregate
report by passing every observation:

```bash
python3 scripts/ocr_ablation.py report \
  --truth data/train_labels.csv \
  --observation /tmp/ocr-ablation/baseline-1.observation.json \
  --observation /tmp/ocr-ablation/baseline-2.observation.json \
  --observation /tmp/ocr-ablation/<variant>-1.observation.json \
  --observation /tmp/ocr-ablation/<variant>-2.observation.json \
  --output-json /tmp/ocr-ablation/report.json \
  --output-markdown /tmp/ocr-ablation/report.md
```

The JSON report is authoritative. The Markdown renderer contains aggregate
scores, deterministic/runtime evidence, target-field point deltas, safety
gates, a ranked recommendation, and an explicit ledger of unmeasured
variants. It intentionally contains no per-case identifiers.

## Current evidence state

The first bounded public diagnostic is recorded in
[`OCR_ABLATION_REPORT_BOUNDED_V1.md`](OCR_ABLATION_REPORT_BOUNDED_V1.md) and
its compact aggregate JSON companion. All eight variants and the baseline
have two byte-identical, complete repetitions. Targeted RapidOCR is the
measured first implementation-review choice, fee-row consensus is second, and
orientation retry is rejected as a winner on this cohort. The report is
explicitly public, label-exposed diagnostic evidence; WO-15 still owns
group-exclusive robustness and production-promotion gates.
That report does not isolate renderer deskew or the combined visible
status-cue path. The harness now registers both routes for a clean V2
measurement; WO-14 remains in progress until those results are recorded.

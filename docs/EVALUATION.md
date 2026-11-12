# Evaluation

This page documents the existing evaluation snapshot in [EVAL_SUMMARY.txt](EVAL_SUMMARY.txt). It does not report a new benchmark run.

## Recorded results

| Model | Language-match check | Responses with no detected tool-check errors | Checked code blocks | Checked blocks with errors |
| --- | --- | --- | --- | --- |
| QAZTECH Azure Engineer 8B | 99% | 76% | 294 | 21% |
| Qwen3-8B base | 98% | 65% | 380 | 22% |

The recorded comparison uses 200 held-out examples per model and the same prompt. The response-level tool-check rate differs by 11 percentage points in this snapshot.

## What the metrics mean

The implementation is in [pipeline/eval_models.py](../pipeline/eval_models.py), with checks imported from [pipeline/validate.py](../pipeline/validate.py).

| Metric | Calculation and scope |
| --- | --- |
| Language-match check | Answers whose detected language matches the requested language, divided by all answers. The script also counts an undetermined language as a match. This is a heuristic, not a fluency score. |
| Responses with no detected tool-check errors | Responses with at least one checked block and zero flagged blocks, divided by responses with at least one checked block. Unchecked code is outside this measure. |
| Checked blocks with errors | Checked blocks with one or more detected issues, divided by all checked blocks. A response may contain multiple blocks. |
| Reasoning-trace check | Count of answers containing the literal `<think>` marker. Both recorded counts are zero. |

The checked-block totals differ between models because their answers contain different numbers of supported code blocks. The snapshot does not include the count of responses with at least one checked block, so that response-level denominator cannot be recovered from the summary alone.

## Tools and inference settings

The original README records Azure CLI help checks, Az PowerShell modules, `bicep build` and `terraform validate`, with azurerm 5.10 / azapi 2.13.

The evaluation script uses temperature 0.3, an 8,192-token context, a 3,000-token output limit and `think=False`. It compares `qaztech-platform/azure-engineer:8b` with `qwen3:8b`.

## Interpretation and reproduction

- The held-out split comes from the project's data-generation process. It is an in-distribution evaluation, not an independent benchmark.
- A tool-check pass does not establish valid resource names, correct values, secure configuration or successful deployment. Some code-block types are not checked.
- Language checks do not establish translation quality or native-speaker fluency.
- The repository includes the evaluation script and a summary. It does not include the per-example output file, the full evaluation dataset or a pinned inference environment. Exact reproduction requires those artifacts and the tool environment used for the original run.
- Generation failures are recorded by the script. The summary does not separately enumerate failures, so it does not establish an inference reliability rate.

Use the snapshot as evidence for this particular comparison and inspect generated infrastructure changes before applying them.

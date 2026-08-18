# CAG Stress Test — Report

> Empirical map of where this CAG breaks. Filled in by hand after running
> `uv run python -m evals.stress.run --http http://localhost:8000` against
> a real estimator (real LLM, real PDFs). The skeleton ships pre-populated
> with the shape the alumno is expected to produce; replace the
> placeholders with numbers from your CSV.

**Run parameters used (fill in):**

| Setting               | Value                              |
|-----------------------|------------------------------------|
| Model                 | `gpt-4o-mini` (PRIMARY_MODEL)      |
| Scenarios             | growing, pivot, contradiction      |
| Attachment sizes (KB) | 0, 5, 20, 50, 100                  |
| Repeats per cell      | 3                                  |
| Latency budget        | 8000 ms                            |
| Cost budget per turn  | $0.02                              |
| Total turns observed  | _<n>_ rows in `results.csv`        |
| Wall-clock to run     | _<minutes>_                        |

---

## 1. Summary table

One row per `(scenario, attachment_size_kb)` cell, averaged across the 3 repeats.
**Numbers below are placeholders — replace from the runner's printed summary.**

| Scenario       | KB  | n   | P50 ms | P95 ms | Total $ | Drift pass % |
|----------------|----:|----:|-------:|-------:|--------:|-------------:|
| growing        |   0 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| growing        |   5 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| growing        |  20 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| growing        |  50 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| growing        | 100 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| pivot          |   0 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| pivot          |   5 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| pivot          |  20 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| pivot          |  50 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| pivot          | 100 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| contradiction  |   0 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| contradiction  |   5 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| contradiction  |  20 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| contradiction  |  50 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |
| contradiction  | 100 |  60 |  _xxx_ |  _xxx_ |  _x.xx_ |       _xx.x_ |

> Note on the 100 KB column: the extractor caps each attachment at
> `MAX_ATTACHMENT_CHARS = 60_000`. The build script generated ~102 K chars,
> so this column measures the *truncated* regime, not the literal 100 KB
> regime. Expect `attachments_total_chars` ≈ 60000 in the CSV for that cell.

---

## 2. Three curves (as tables)

### 2a. Latency vs context size

`tokens_in` is the cleanest proxy for "how much context the model swallowed
this turn" (it includes system prompt + history + transcript + extracted
attachment text). Bucket by tokens_in, take median latency.

| `tokens_in` bucket | Median latency (ms) | n  |
|--------------------|--------------------:|---:|
|   0–999            |             _xxx_   | _x_|
| 1000–2999          |             _xxx_   | _x_|
| 3000–6999          |             _xxx_   | _x_|
| 7000–14999         |             _xxx_   | _x_|
| 15000+             |             _xxx_   | _x_|

### 2b. Cost accumulated vs turn index (per scenario, size = 0)

Cumulative `cost_usd` summed across turns, averaged across repeats.

| Turn | growing $ | pivot $ | contradiction $ |
|-----:|----------:|--------:|----------------:|
|    1 |   _x.xx_  | _x.xx_  |        _x.xx_   |
|    5 |   _x.xx_  | _x.xx_  |        _x.xx_   |
|   10 |   _x.xx_  | _x.xx_  |        _x.xx_   |
|   15 |   _x.xx_  | _x.xx_  |        _x.xx_   |
|   20 |   _x.xx_  | _x.xx_  |        _x.xx_   |

### 2c. Memory drift pass rate vs turn index

For each turn N ≥ 2, did the turn-1 fact (project_name) still appear in the
session snapshot? Pooled across repeats and scenarios (size = 0).

| Turn N | Drift pass rate (%) | n  |
|-------:|--------------------:|---:|
|    2   |              _xx.x_ | _x_|
|    5   |              _xx.x_ | _x_|
|   10   |              _xx.x_ | _x_|
|   15   |              _xx.x_ | _x_|
|   20   |              _xx.x_ | _x_|

---

## 3. Reading: where the CAG starts to break

**Paragraph 1 — the dominant failure mode** (~5 lines).

> Replace this with your own reading. Anchor it to the actual numbers above.
> Examples of what to write:
>
> *"In my run, the turn-1 project_name survives 100% of the time through
> turn ~N because the heuristic anchor detector promotes the first turn as
> an anchor (its mention of 'NDA' / 'budget' / etc. triggers a rule), but
> drops to ~M% by turn 20 in the contradiction scenario because the
> summarizer rewrites the section that used to carry the name."*
>
> *"P95 latency stays under 4s until tokens_in crosses ~5000; from there
> it jumps to ~12s, well over my 8s budget. The 50 KB and 100 KB
> attachment cells fail the latency budget on every turn — the cost of
> shipping the extracted text through the wire and into the model is
> dominant."*

**Paragraph 2 — what this implies for the RAG decision** (~5 lines).

> Replace this with your own conclusion. Cite the curves. Examples:
>
> *"If RAG retrieves ≤2 K tokens of the 60 K attachment per turn, the
> latency curve flattens at the level I see for the 5 KB cell (~Y ms).
> Cost per turn drops by ~Z× because the input token bill is dominated by
> the system + attachment, not the history. The price of RAG is one extra
> embedding call per attachment ingestion plus a retrieval call per turn —
> for sessions over ~T turns, the CAG already costs more than that
> overhead."*
>
> *"The pivot scenario shows that even without large attachments,
> memory_drift starts failing at turn ~K. RAG over the historical turns
> would let the model query 'what stack did we agree on?' instead of
> relying on whatever the summarizer chose to keep — that's the second
> argument for the switch."*

---

## 4. Four claims to defend

(Replace `___` with your own values; this is what you bring to the live
session.)

1. My CAG starts degrading between turns ___ and ___ because ___.
2. Cost per turn grows with O(___) because each turn re-injects ___.
3. The dominant latency bottleneck is ___ because ___.
4. To cut context by 50% without losing recall, I'd attack ___ first
   because my data shows it contributes ___ to the recall.

---

## 5. Reproducibility

```bash
# Rebuild the fixtures (deterministic; same paragraph, same byte counts).
uv run python -m evals.stress.fixtures.build_pdfs

# Real run against a live estimator.
docker compose up --build -d
curl -sf http://localhost:8000/health

uv run python -m evals.stress.run \
    --http http://localhost:8000 \
    --scenarios growing,pivot,contradiction \
    --attachment-sizes 0,5,20,50,100 \
    --repeats 3 \
    --latency-budget-ms 8000 \
    --cost-budget-usd 0.02 \
    --output evals/stress/results.csv

# Quick sanity check.
wc -l evals/stress/results.csv          # ≥ 50 rows + header
head -1 evals/stress/results.csv        # 22-column schema
```

The `results.csv` produced is gitignored (regenerated per run). This
`REPORT.md` is the artefact that ships to the directo.

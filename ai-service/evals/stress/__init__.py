"""Stress test scaffolding for the CAG (Cache-Augmented Generation) pipeline.

Materialises the Session 6 pre-exercise: instrument the conversational
estimator under load and produce a CSV + REPORT.md that quantifies where the
context-augmented approach starts to degrade — latency, cost per turn,
recall drift across many turns and growing attachments.

The three submodules mirror the exercise's blocks:

- ``scenarios``  — synthetic multi-turn conversations with fact-trackers.
- ``metrics``    — ``LatencyBudgetMetric``, ``CostBudgetMetric``,
                   ``MemoryDriftMetric`` (read ``TurnObservation`` + session
                   snapshot, not ``EstimationResult`` — hence kept apart
                   from ``evals.metrics``).
- ``run``        — CLI runner that orchestrates scenarios × attachment
                   sizes × repeats and writes ``results.csv``.

The PDFs consumed by ``run`` live under ``evals/stress/fixtures/`` and are
generated on demand by ``fixtures/build_pdfs.py`` (the PDFs themselves are
gitignored; only the build script is committed).
"""

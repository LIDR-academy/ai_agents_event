# Embedding sanity check

Model: `text-embedding-3-small` (1536 dims, default dimension).
Generated with `scripts/compare.py` (cosine computed by hand, stdlib only).
Run date: 2026-05-27.

| Pair | Text A | Text B | Cosine | Expectation |
|------|--------|--------|--------|-------------|
| A — near | "OAuth 2.0 authentication backend with JWT tokens for fintech mobile app" | "Authorization service using JSON Web Tokens for a banking application" | **0.5957** | high (> 0.6) |
| B — unrelated | "OAuth 2.0 authentication backend with JWT tokens for fintech mobile app" | "Database migration from MySQL to PostgreSQL with zero downtime" | **0.1920** | low (< 0.4) |
| C — generic | "Backend services" | "API development" | **0.5407** | no fixed expectation |

## Commentary

Pair B behaves exactly as intuition predicts: two unrelated engineering topics sit at 0.19,
comfortably under 0.4 — the embeddings clearly separate "auth for fintech" from "DB migration".

Pair A is the interesting one: at **0.5957 it lands just *below* the 0.6 orientative threshold**,
even though both texts describe the same concept (token-based authn/authz for banking). The two
sentences share almost no surface vocabulary ("OAuth/JWT/fintech" vs "Authorization/JSON Web
Tokens/banking"), so the model has to bridge them purely semantically — and it nearly does, but
not past the round 0.6 line. A good reminder that similarity thresholds are model- and
phrasing-dependent, not universal constants.

Pair C is the real talking point: two short, generic phrases ("Backend services" / "API
development") score **0.5407 — almost as high as the genuinely-near Pair A**. Short, vague text
carries little discriminating signal, so generic-vs-generic looks deceptively "close". This is
exactly why our chunker prepends a contextual header (project + sector + tech) to each component:
without that context, many budget components would collapse into the same fuzzy "backend-ish"
region of the embedding space. Worth discussing live alongside chunking strategies.

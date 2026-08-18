"""Foundation layer — provider-agnostic plumbing.

LLM wrapper, prompt rendering, guardrails, attachment extraction and
persistence. No AI-architecture opinion lives here: every other layer may
depend on ``foundation``; ``foundation`` depends only on ``app.config``.
"""

"""Agentic generation — the Actor-Critic-Boss loop.

``boss.py`` orchestrates iterative refinement; ``critic.py`` is the read-only
auditor. This layer MAY import ``app.generation.conversation`` (the multi-turn
substrate it runs on); the reverse is forbidden.
"""

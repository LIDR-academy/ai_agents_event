"""Domain layer — the estimation contract and the conductor.

``schemas/`` is the public contract Instructor enforces and Rails consumes.
``estimation_service.py`` holds :class:`EstimationService`, the single
composition point where the generation layers (cag, rag, agentic,
conversation) are wired together.
"""

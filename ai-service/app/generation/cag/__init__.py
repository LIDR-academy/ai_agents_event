"""Cache-augmented generation — exact then semantic lookup.

Both caches live here so CAG reads as one package: an exact SHA-256 lookup
(``exact.py``) tried first, then a vector-similarity lookup (``semantic.py``).
This unifies what used to be split across ``services/cache.py`` and
``cache/semantic.py``.
"""

from app.generation.cag.exact import EstimationCache
from app.generation.cag.semantic import EstimationSemanticCache

__all__ = ["EstimationCache", "EstimationSemanticCache"]

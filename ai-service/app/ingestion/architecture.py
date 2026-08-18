"""Architecture decision: CAG, RAG or hybrid? (Article 1 of the module).

This module is the executable form of the sub-block 2.1 decision. It is *not*
wired into the FastAPI request path — like ``catalog/inspect.py`` it is a CLI
you run to reason about the corpus before any ingestion happens:

    python -m app.ingestion.architecture

Two pieces, straight from Article 1:

* :class:`CAGViability` — the four CAG *constraints* (context window, cost,
  latency, lost-in-the-middle). ``viable`` is an ``all([...])`` on purpose:
  the four are an **AND**, not an OR. One red constraint kills CAG.
* :func:`recommend_architecture` — evaluates the four *decision axes* (volume,
  refresh frequency, traceability, access control) and returns
  ``"CAG" | "Hybrid" | "RAG"``.

Fine-tuning is deliberately absent: it is an orthogonal layer (it teaches *how*
to answer, it does not add *new data* nor solve traceability).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal

# Below this many tokens we assume the model still "uses the middle" and stays
# under a reasonable latency SLO. Above it, lost-in-the-middle and latency bite.
_CENTER_RECALL_TOKEN_BUDGET = 100_000
# A corpus fits CAG only if it leaves headroom in the window (we use 70%).
_USABLE_WINDOW_FRACTION = 0.7
# Prefix caching only pays off if the corpus is stable for at least a week.
_CACHE_FRIENDLY_REFRESH_DAYS = 7


@dataclass
class CAGViability:
    """The four CAG constraints. ``viable`` is an AND of all four."""

    context_window_ok: bool  # ¿cabe el corpus completo en el context window?
    cost_ok: bool  # ¿el coste por turno con prefix caching es asumible?
    latency_ok: bool  # ¿la latencia con el corpus completo se queda bajo SLO?
    lost_in_the_middle_ok: bool  # ¿los benchmarks muestran que el modelo USA el centro?

    @property
    def viable(self) -> bool:
        return all(
            [
                self.context_window_ok,
                self.cost_ok,
                self.latency_ok,
                self.lost_in_the_middle_ok,
            ]
        )

    def failing_constraints(self) -> list[str]:
        """Names of the constraints in red — empty iff ``viable``."""
        return [
            name
            for name, ok in (
                ("context_window", self.context_window_ok),
                ("cost", self.cost_ok),
                ("latency", self.latency_ok),
                ("lost_in_the_middle", self.lost_in_the_middle_ok),
            )
            if not ok
        ]


@dataclass
class CorpusProfile:
    name: str
    estimated_tokens: int  # volumen total estimado
    refresh_frequency_days: float  # cada cuánto cambian los datos
    traceability_required: bool  # ¿hay que poder citar la fuente?
    access_control_required: bool  # ¿hay que filtrar por permisos por usuario?


@dataclass
class ModelProfile:
    name: str
    context_window: int  # tokens
    cost_per_1k_input: float  # USD
    prefix_caching: bool  # ¿soporta caché de prefijo?


def assess_cag_viability(corpus: CorpusProfile, model: ModelProfile) -> CAGViability:
    """Derive the four CAG constraints from the corpus + model profiles.

    Heuristics kept deliberately simple — the point is pedagogical clarity, not
    a calibrated cost model.
    """
    fits = corpus.estimated_tokens <= model.context_window * _USABLE_WINDOW_FRACTION
    return CAGViability(
        context_window_ok=fits,
        cost_ok=model.prefix_caching
        and corpus.refresh_frequency_days >= _CACHE_FRIENDLY_REFRESH_DAYS,
        latency_ok=corpus.estimated_tokens <= _CENTER_RECALL_TOKEN_BUDGET,
        lost_in_the_middle_ok=corpus.estimated_tokens <= _CENTER_RECALL_TOKEN_BUDGET,
    )


def recommend_architecture(
    corpus: CorpusProfile, model: ModelProfile
) -> Literal["CAG", "Hybrid", "RAG"]:
    """Recommend an architecture over the four decision axes."""
    # Eje 1: VOLUMEN. ¿Cabe con holgura en la ventana?
    fits_in_window = corpus.estimated_tokens <= model.context_window * _USABLE_WINDOW_FRACTION
    # Eje 2: FRECUENCIA. Cambios más frecuentes que semanales rompen prefix caching.
    cache_friendly = corpus.refresh_frequency_days >= _CACHE_FRIENDLY_REFRESH_DAYS
    # Eje 3: TRAZABILIDAD. Sin retriever no hay metadata de fuente que citar.
    traceability_doable = not corpus.traceability_required
    # Eje 4: CONTROL DE ACCESO. CAG no filtra por usuario antes del prompt.
    access_control_doable = not corpus.access_control_required

    viable_for_cag = all(
        [fits_in_window, cache_friendly, traceability_doable, access_control_doable]
    )
    if viable_for_cag:
        return "CAG"
    # Si el corpus cabe y es estable pero falla trazabilidad/acceso, el modo
    # híbrido (CAG para lo estable + RAG para lo sensible/citable) es razonable.
    if fits_in_window and cache_friendly:
        return "Hybrid"
    return "RAG"


# --- El Proyecto 2, con números defendibles (Article 1) ---------------------

PROYECTO_2 = CorpusProfile(
    name="Proyecto 2",
    estimated_tokens=250_000,  # presupuestos + transcripciones + tarifas + adendas
    refresh_frequency_days=7,  # cierre comercial semanal
    traceability_required=True,  # legal exige citar la fuente
    access_control_required=True,  # info confidencial cliente
)

CURRENT_MODEL = ModelProfile(
    name="gpt-4o-mini",
    context_window=128_000,
    cost_per_1k_input=0.00015,
    prefix_caching=True,
)


def _main(argv: list[str]) -> int:
    """CLI: ``python -m app.ingestion.architecture``."""
    corpus, model = PROYECTO_2, CURRENT_MODEL
    viability = assess_cag_viability(corpus, model)
    recommendation = recommend_architecture(corpus, model)

    print(
        f"Corpus '{corpus.name}': {corpus.estimated_tokens:,} tokens, "
        f"refresh cada {corpus.refresh_frequency_days:g} días, "
        f"trazabilidad={corpus.traceability_required}, "
        f"control_acceso={corpus.access_control_required}"
    )
    print(
        f"Modelo '{model.name}': ventana {model.context_window:,} tokens, "
        f"prefix_caching={model.prefix_caching}"
    )
    print()
    if viability.viable:
        print("Viabilidad CAG: viable (las 4 restricciones en verde)")
    else:
        print("Viabilidad CAG: NO viable")
        print(f"  Restricciones en rojo: {', '.join(viability.failing_constraints())}")
    print(f"Recomendación arquitectónica: {recommendation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))

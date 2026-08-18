import { Controller } from "@hotwired/stimulus"

// UX sugar for the Chunking Lab form: a live cost/time hint that reacts to the
// strategy checkboxes, and canned-query chips that append queries[] inputs.
// The form submits standard params either way — nothing here is load-bearing.
export default class extends Controller {
  static targets = ["strategy", "hint", "queriesContainer"]

  connect() {
    this.refreshHint()
  }

  refreshHint() {
    if (!this.hasHintTarget) return

    const checked = this.strategyTargets.filter((box) => box.checked)
    const tiers = checked.map((box) => box.dataset.costTier)

    if (checked.length === 0) {
      this.hintTarget.textContent = "Selecciona al menos una estrategia."
    } else if (tiers.includes("expensive")) {
      this.hintTarget.textContent =
        "Incluye estrategias de pago (LLM): el run puede tardar varios minutos y costar ~$0.15. Se guarda para no re-pagar."
    } else if (tiers.includes("cheap")) {
      this.hintTarget.textContent =
        "Incluye 'semantic' (embeddings extra en ingesta): coste de céntimos, segundos de duración."
    } else {
      this.hintTarget.textContent = "Selección actual: estrategias gratuitas — el run tarda segundos."
    }
  }

  addQuery(event) {
    const query = event.currentTarget.dataset.query
    if (!query || !this.hasQueriesContainerTarget) return

    const existing = Array.from(
      this.queriesContainerTarget.querySelectorAll("input[name='queries[]']")
    )

    // Reuse the first empty input before appending a new row.
    const empty = existing.find((input) => input.value.trim() === "")
    if (empty) {
      empty.value = query
      return
    }
    if (existing.some((input) => input.value.trim() === query)) return

    const input = existing[existing.length - 1].cloneNode()
    input.value = query
    this.queriesContainerTarget.appendChild(input)
  }
}

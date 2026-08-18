import { Controller } from "@hotwired/stimulus"

// Connects to data-controller="form-loading"
// Swaps the submit button into a busy state and shows a status panel with
// a rolling phase message + elapsed timer while the server processes the
// request. The form does a regular synchronous submit (local: true) and Rails
// renders show.html.erb when the FastAPI estimator responds with the
// structured payload.
//
// Re-enabled automatically on the next page render (success → show view,
// validation error → server re-renders new view, network error → new view
// with flash).
export default class extends Controller {
  static targets = ["submit", "label", "spinner", "statusPanel", "phaseText", "timer"]

  PHASES = [
    { at: 0,  text: "Conectando con el servicio IA…" },
    { at: 2,  text: "Enviando transcripción al LLM…" },
    { at: 8,  text: "Esperando respuesta estructurada (puede tardar hasta 90 s)…" },
    { at: 30, text: "El LLM sigue procesando — Instructor puede reintentar validators…" },
    { at: 90, text: "Casi listo, terminando la generación…" }
  ]

  start() {
    this.submitTarget.disabled = true
    this.submitTarget.classList.add("cursor-not-allowed", "opacity-70")
    if (this.hasLabelTarget) this.labelTarget.textContent = "Generating…"
    if (this.hasSpinnerTarget) this.spinnerTarget.classList.remove("hidden")

    if (this.hasStatusPanelTarget) {
      this.statusPanelTarget.classList.remove("hidden")
      this.startedAt = Date.now()
      this.tick()
      this.interval = setInterval(() => this.tick(), 500)
    }
  }

  tick() {
    const elapsed = Math.floor((Date.now() - this.startedAt) / 1000)

    if (this.hasTimerTarget) {
      const mm = String(Math.floor(elapsed / 60)).padStart(2, "0")
      const ss = String(elapsed % 60).padStart(2, "0")
      this.timerTarget.textContent = `${mm}:${ss}`
    }

    if (this.hasPhaseTextTarget) {
      let currentPhase = this.PHASES[0]
      for (const phase of this.PHASES) {
        if (elapsed >= phase.at) currentPhase = phase
      }
      if (this.phaseTextTarget.textContent !== currentPhase.text) {
        this.phaseTextTarget.textContent = currentPhase.text
      }
    }
  }

  disconnect() {
    if (this.interval) {
      clearInterval(this.interval)
      this.interval = null
    }
  }
}

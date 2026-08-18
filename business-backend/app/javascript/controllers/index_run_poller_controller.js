import { Controller } from "@hotwired/stimulus"

// Connects to data-controller="index-run-poller"
// Polls the corpus-expansion job's `status` endpoint (JSON) every ~1.5s while it
// runs, updating a live counter and progress bar. When the job finishes it
// reloads the page so the show view re-renders with the grown corpus stats.
export default class extends Controller {
  static targets = ["status", "processed", "bar"]
  static values = { url: String, submitted: Number, finished: Boolean }

  connect() {
    if (this.finishedValue) return
    this.poll()
    this.interval = setInterval(() => this.poll(), 1500)
  }

  disconnect() {
    if (this.interval) clearInterval(this.interval)
  }

  async poll() {
    try {
      const response = await fetch(this.urlValue, { headers: { Accept: "application/json" } })
      if (!response.ok) return
      const data = await response.json()
      this.render(data)
      if (data.finished) {
        clearInterval(this.interval)
        // Re-render the page so the before/after corpus stats show the delta.
        window.location.reload()
      }
    } catch (_e) {
      // Transient network error — keep polling.
    }
  }

  render(data) {
    if (this.hasStatusTarget) this.statusTarget.textContent = data.status
    if (this.hasProcessedTarget) {
      this.processedTarget.textContent = `${data.documents_processed} / ${this.submittedValue}`
    }
    if (this.hasBarTarget && this.submittedValue > 0) {
      const pct = Math.min(100, Math.round((data.documents_processed / this.submittedValue) * 100))
      this.barTarget.style.width = `${pct}%`
    }
  }
}

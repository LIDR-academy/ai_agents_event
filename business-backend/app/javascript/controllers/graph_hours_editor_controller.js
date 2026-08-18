import { Controller } from "@hotwired/stimulus"

// Connects to data-controller="graph-hours-editor"
// Gate 2 (final review): the structure is fixed, the human only edits per-task hours.
// Live-sums the hours inputs into a running total (hours + engineer-days) as feedback.
// The authoritative totals are recomputed server-side when the run resumes.
const HOURS_PER_DAY = 8

export default class extends Controller {
  static targets = ["hours", "totalHours", "totalDays", "missing"]

  connect() {
    this.recompute()
  }

  recompute() {
    let total = 0
    let missing = 0
    this.hoursTargets.forEach((input) => {
      const v = parseFloat(input.value)
      if (Number.isFinite(v)) total += v
      else missing += 1
      // Toggle the per-row "sin horas" hint (a sibling with data-missing-hint).
      const hint = input.closest("[data-task-row]")?.querySelector("[data-missing-hint]")
      if (hint) hint.classList.toggle("hidden", Number.isFinite(v))
    })
    if (this.hasTotalHoursTarget) this.totalHoursTarget.textContent = Math.round(total * 10) / 10
    if (this.hasTotalDaysTarget) this.totalDaysTarget.textContent = Math.round(total / HOURS_PER_DAY)
    if (this.hasMissingTarget) {
      this.missingTarget.textContent = missing
      this.missingTarget.closest("[data-missing-banner]")?.classList.toggle("hidden", missing === 0)
    }
  }
}

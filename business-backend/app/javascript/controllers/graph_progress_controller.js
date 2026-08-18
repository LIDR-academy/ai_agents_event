import { Controller } from "@hotwired/stimulus"

// Connects to data-controller="graph-progress"
// Polls the graph run's `progress` endpoint (JSON) every ~1.5s while a leg runs,
// filling a live per-agent panel: each node row lights up with the didactic line
// its agent emitted, the next node pulses, and the hours fan-out shows a counter.
// When the leg ends (pauses at a gate / completes) it reloads so the show view
// re-renders the gate or completed screen.
export default class extends Controller {
  static values = { url: String, finished: Boolean }

  connect() {
    if (this.finishedValue) return
    this.rows = Array.from(this.element.querySelectorAll("[data-node]"))
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
      this.render(data.activity || [])
      if (data.finished) {
        clearInterval(this.interval)
        window.location.reload()
      }
    } catch (_e) {
      // Transient network error — keep polling.
    }
  }

  render(activity) {
    // Which nodes have reported, and their latest line (hours is aggregated).
    const messageByNode = {}
    let hoursWith = 0
    let hoursWithout = 0
    for (const entry of activity) {
      if (entry.node === "hours") {
        if (/SIN ANÁLOGO/.test(entry.message)) hoursWithout++
        else hoursWith++
      } else {
        messageByNode[entry.node] = entry.message
      }
    }
    if (hoursWith || hoursWithout) {
      messageByNode["hours"] = `${hoursWith} con horas · ${hoursWithout} sin análogo`
    }

    // Paint each row: done (has a message) / running (first pending after a done) / idle.
    let lastDoneIndex = -1
    this.rows.forEach((row, i) => {
      if (messageByNode[row.dataset.node] !== undefined) lastDoneIndex = i
    })
    this.rows.forEach((row, i) => {
      const node = row.dataset.node
      const msg = messageByNode[node]
      const msgEl = row.querySelector("[data-role='msg']")
      const dot = row.querySelector("[data-role='dot']")
      if (msg !== undefined) {
        this.setState(row, dot, "done")
        if (msgEl) msgEl.textContent = msg
      } else if (i === lastDoneIndex + 1) {
        this.setState(row, dot, "running")
        if (msgEl) msgEl.textContent = "…"
      } else {
        this.setState(row, dot, "idle")
      }
    })
  }

  setState(row, dot, state) {
    row.dataset.state = state
    if (!dot) return
    dot.classList.remove("bg-success", "bg-brand", "bg-white/20", "animate-pulse")
    if (state === "done") dot.classList.add("bg-success")
    else if (state === "running") dot.classList.add("bg-brand", "animate-pulse")
    else dot.classList.add("bg-white/20")
  }
}

import { Controller } from "@hotwired/stimulus"

// Editable, two-level cost breakdown for the human-verification step (S09):
// functional MODULES, each with a table of TASKS. No nested-form gem, no build.
//
// Serialization uses explicit integer indices so Rails coalesces the nested
// structure unambiguously:
//   modules[<m>][name] / [description]
//   modules[<m>][tasks][<t>][name] / [description] / [estimated_hours] / [rate_eur_per_hour] / [sources]
// Indices only need to be unique (gaps are fine — the controller sorts by index
// server-side). New modules/tasks get a monotonic index; deletes leave holes.
//
// Templates carry the placeholders __MIDX__ / __TIDX__, replaced on clone.
export default class extends Controller {
  static targets = ["modulesContainer", "moduleTemplate", "taskTemplate", "total", "totalField", "empty"]

  connect() {
    // Next free module index = max rendered index + 1.
    const indices = this.moduleElements.map((el) => parseInt(el.dataset.moduleIndex, 10))
    this.nextModuleIndex = indices.length ? Math.max(...indices) + 1 : 0
    this.moduleElements.forEach((el) => this.initModuleTaskCounter(el))
    this.recompute()
  }

  get moduleElements() {
    return Array.from(this.element.querySelectorAll("[data-module]"))
  }

  initModuleTaskCounter(moduleEl) {
    const taskIndices = Array.from(moduleEl.querySelectorAll("[data-task]"))
      .map((el) => parseInt(el.dataset.taskIndex, 10))
    moduleEl.dataset.nextTaskIndex = taskIndices.length ? Math.max(...taskIndices) + 1 : 0
  }

  addModule() {
    const m = this.nextModuleIndex++
    const html = this.moduleTemplateTarget.innerHTML.replaceAll("__MIDX__", m)
    const tmp = document.createElement("div")
    tmp.innerHTML = html.trim()
    const moduleEl = tmp.firstElementChild
    moduleEl.dataset.nextTaskIndex = "0"
    this.modulesContainerTarget.appendChild(moduleEl)
    this.appendTask(moduleEl) // a fresh module starts with one empty task
    this.toggleEmpty()
    this.recompute()
  }

  addTask(event) {
    const moduleEl = event.target.closest("[data-module]")
    if (moduleEl) {
      this.appendTask(moduleEl)
      this.recompute()
    }
  }

  appendTask(moduleEl) {
    const m = moduleEl.dataset.moduleIndex
    const t = parseInt(moduleEl.dataset.nextTaskIndex, 10)
    moduleEl.dataset.nextTaskIndex = t + 1
    const html = this.taskTemplateTarget.innerHTML.replaceAll("__MIDX__", m).replaceAll("__TIDX__", t)
    const tmp = document.createElement("tbody")
    tmp.innerHTML = html.trim()
    moduleEl.querySelector("[data-tasks-container]").appendChild(tmp.firstElementChild)
  }

  deleteTask(event) {
    event.target.closest("[data-task]")?.remove()
    this.recompute()
  }

  deleteModule(event) {
    event.target.closest("[data-module]")?.remove()
    this.toggleEmpty()
    this.recompute()
  }

  // Cost = hours × rate, summed per module and across modules. In structure mode
  // (review #1) there are no hours/rate inputs, so every subtotal is 0 and the
  // total targets are simply absent — recompute is a harmless no-op there.
  recompute() {
    let grand = 0
    this.moduleElements.forEach((moduleEl) => {
      let subtotal = 0
      moduleEl.querySelectorAll("[data-task]").forEach((row) => {
        const hours = parseInt(row.querySelector("[data-estimate-hours]")?.value, 10)
        const rate = parseInt(row.querySelector("[data-estimate-rate]")?.value, 10)
        const cost = (Number.isFinite(hours) ? hours : 0) * (Number.isFinite(rate) ? rate : 0)
        const cell = row.querySelector("[data-task-cost]")
        if (cell) cell.textContent = cost.toLocaleString("es-ES")
        subtotal += cost
      })
      const subtotalEl = moduleEl.querySelector("[data-module-subtotal]")
      if (subtotalEl) subtotalEl.textContent = subtotal.toLocaleString("es-ES")
      grand += subtotal
    })
    if (this.hasTotalTarget) this.totalTarget.textContent = grand.toLocaleString("es-ES")
    if (this.hasTotalFieldTarget) this.totalFieldTarget.value = grand
  }

  toggleEmpty() {
    if (!this.hasEmptyTarget) return
    this.emptyTarget.classList.toggle("hidden", this.moduleElements.length > 0)
  }
}

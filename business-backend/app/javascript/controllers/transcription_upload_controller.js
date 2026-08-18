import { Controller } from "@hotwired/stimulus"

// Connects to data-controller="transcription-upload"
// Reads a .txt file in the browser via FileReader and dumps its contents into
// the textarea so the user can still review/edit before submitting. Keeps the
// server stateless (no multipart upload, no temp files).
export default class extends Controller {
  static targets = ["fileInput", "textarea", "status"]
  static values = { maxBytes: { type: Number, default: 200000 } }

  load(event) {
    const file = event.target.files[0]
    if (!file) return

    if (file.size > this.maxBytesValue) {
      const limitKb = Math.round(this.maxBytesValue / 1024)
      const actualKb = (file.size / 1024).toFixed(0)
      this.setStatus(`File too large (${actualKb} KB). Max ${limitKb} KB.`, "error")
      event.target.value = ""
      return
    }

    const reader = new FileReader()
    reader.onload = (e) => {
      const text = e.target.result
      this.textareaTarget.value = text
      this.setStatus(`Loaded ${text.length} chars from ${file.name}`, "ok")
      this.textareaTarget.dispatchEvent(new Event("input", { bubbles: true }))
    }
    reader.onerror = () => this.setStatus("Could not read file", "error")
    reader.readAsText(file)
  }

  setStatus(message, kind) {
    if (!this.hasStatusTarget) return
    this.statusTarget.textContent = message
    this.statusTarget.dataset.kind = kind
  }
}

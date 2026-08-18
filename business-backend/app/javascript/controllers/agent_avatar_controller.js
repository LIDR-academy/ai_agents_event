import { Controller } from "@hotwired/stimulus"

// Session 12 — swaps the avatar preview next to the agent-profile <select> to
// match the SELECTED profile. A <select> cannot render images, so we keep a
// separate <img> and update its src on change from an id→url map. When the
// selected profile has no avatar (or "service default" is picked), we show the
// placeholder instead.
export default class extends Controller {
  static targets = ["select", "image", "placeholder"]
  static values = { avatars: Object }

  connect() {
    this.update()
  }

  update() {
    const id = this.hasSelectTarget ? this.selectTarget.value : ""
    const url = id && this.avatarsValue[id]
    if (url) {
      this.imageTarget.src = url
      this.imageTarget.classList.remove("hidden")
      if (this.hasPlaceholderTarget) this.placeholderTarget.classList.add("hidden")
    } else {
      this.imageTarget.classList.add("hidden")
      if (this.hasPlaceholderTarget) this.placeholderTarget.classList.remove("hidden")
    }
  }
}

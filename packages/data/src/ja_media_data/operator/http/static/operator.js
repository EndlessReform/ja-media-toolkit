document.addEventListener("click", (event) => {
  const expandProposal = event.target.closest("[data-proposal-expand]");
  if (expandProposal) {
    openProposalDialog(expandProposal.closest("#proposal"));
    return;
  }
  const closeProposal = event.target.closest("[data-proposal-close]");
  if (closeProposal) {
    closeProposal.closest("dialog").close();
    return;
  }
  const seriesLink = event.target.closest("[data-resolution-series]");
  if (seriesLink) {
    document.querySelectorAll(".resolution-series li.selected").forEach((item) => {
      item.classList.remove("selected");
    });
    seriesLink.closest("li").classList.add("selected");
  }
  const candidate = event.target.closest(".candidate-toggle");
  if (candidate && candidate.dataset.loaded === "true") {
    const rows = document.getElementById(candidate.dataset.candidateTarget);
    const opening = candidate.dataset.open !== "true";
    if (rows) rows.hidden = !opening;
    candidate.dataset.open = String(opening);
    candidate.querySelector("span[aria-hidden]").textContent = opening ? "▼" : "▶";
    event.preventDefault();
    event.stopImmediatePropagation();
    return;
  }
  const control = event.target.closest("[data-scroll-track]");
  if (!control) return;
  const track = document.getElementById(control.dataset.scrollTrack);
  if (!track) return;
  const direction = Number(control.dataset.scrollDirection) || 1;
  track.scrollBy({ left: direction * Math.max(260, track.clientWidth * 0.8), behavior: "smooth" });
});

document.addEventListener("htmx:after:swap", (event) => {
  const target = event.detail.ctx.target;
  restoreLocalSettings(target);
  if (target.id === "proposal") document.body.classList.remove("proposal-modal-open");
  if (!target.classList.contains("candidate-rows-loaded")) return;
  const button = document.querySelector(
    `[data-candidate-target="${target.id}"]`
  );
  if (!button) return;
  button.dataset.loaded = "true";
  button.dataset.open = "true";
  button.querySelector("span[aria-hidden]").textContent = "▼";
});

function restoreLocalSettings(root = document) {
  root.querySelectorAll("[data-local-setting]").forEach((input) => {
    const key = input.dataset.localSetting;
    const saved = localStorage.getItem(key);
    if (saved !== null) input.value = saved;
    if (input.dataset.localSettingBound === "true") return;
    input.dataset.localSettingBound = "true";
    input.addEventListener("change", () => localStorage.setItem(key, input.value));
  });
}

document.addEventListener("DOMContentLoaded", () => restoreLocalSettings());

function openProposalDialog(root) {
  if (!root) return;
  const dialog = root.querySelector(".proposal-dialog");
  const card = root.querySelector(".proposal-card");
  const home = root.querySelector(".proposal-inline-home");
  const slot = root.querySelector(".proposal-dialog-slot");
  if (!dialog || !card || !home || !slot) return;
  slot.append(card);
  document.body.classList.add("proposal-modal-open");
  dialog.addEventListener("close", () => {
    home.after(card);
    document.body.classList.remove("proposal-modal-open");
  }, { once: true });
  dialog.showModal();
}

document.addEventListener("click", (event) => {
  if (event.target.matches("dialog.proposal-dialog")) event.target.close();
});

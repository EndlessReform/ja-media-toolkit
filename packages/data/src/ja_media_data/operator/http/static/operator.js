document.addEventListener("click", (event) => {
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

document.addEventListener("htmx:afterSwap", (event) => {
  if (!event.detail.target.classList.contains("candidate-rows-loaded")) return;
  const button = document.querySelector(
    `[data-candidate-target="${event.detail.target.id}"]`
  );
  if (!button) return;
  button.dataset.loaded = "true";
  button.dataset.open = "true";
  button.querySelector("span[aria-hidden]").textContent = "▼";
});

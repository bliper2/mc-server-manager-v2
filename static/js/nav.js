// Sidebar navigation: a sliding indicator behind the active item, arrow-key movement and aria-current.
// Tab switching itself stays in core.js (switchTab); this file only reads which item is active.

(function () {
  const nav = document.querySelector(".sidebar-nav");
  if (!nav) return;
  const indicator = document.createElement("span");
  indicator.className = "nav-indicator instant";
  indicator.setAttribute("aria-hidden", "true");
  nav.prepend(indicator);

  nav.querySelectorAll(".nav-btn").forEach(button => {
    const label = button.querySelector("span")?.textContent.trim();
    if (label) { button.title = label; button.setAttribute("aria-label", label); button.dataset.navLabel = label; }
  });

  const visibleItems = () => [...nav.querySelectorAll(".nav-btn")].filter(button => button.offsetParent !== null);

  function place(instant) {
    const active = nav.querySelector(".nav-btn.active");
    nav.querySelectorAll(".nav-btn").forEach(button => {
      if (button === active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (!active || active.offsetParent === null) {
      if (indicator.classList.contains("ready")) indicator.classList.remove("ready");
      return;
    }
    if (indicator.classList.contains("instant") !== !!instant) indicator.classList.toggle("instant", !!instant);
    indicator.style.height = `${active.offsetHeight}px`;
    indicator.style.transform = `translateY(${active.offsetTop}px)`;
    if (!indicator.classList.contains("ready")) indicator.classList.add("ready");
    if (instant) requestAnimationFrame(() => indicator.classList.remove("instant"));
  }

  nav.addEventListener("keydown", event => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const items = visibleItems();
    const index = items.indexOf(document.activeElement);
    if (index < 0) return;
    event.preventDefault();
    let next = index;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else next = (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  });

  // Only react to a nav item changing. Writing classes on the indicator itself must not re-trigger this observer.
  new MutationObserver(records => {
    if (records.some(record => record.target !== indicator)) place(false);
  }).observe(nav, { attributes: true, attributeFilter: ["class"], subtree: true });
  window.addEventListener("resize", () => place(true));
  if (document.fonts?.ready) document.fonts.ready.then(() => place(true));
  place(true);
  requestAnimationFrame(() => document.body.classList.add("entered"));
})();

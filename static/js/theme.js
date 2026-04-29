(function () {
  const root = document.documentElement;
  const savedTheme = localStorage.getItem("skillswap-theme");

  if (savedTheme === "dark" || savedTheme === "light") {
    root.setAttribute("data-theme", savedTheme);
  } else {
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    root.setAttribute("data-theme", prefersDark ? "dark" : "light");
  }

  function updateIcon() {
    const icon = document.querySelector("[data-theme-icon]");
    const text = document.querySelector("[data-theme-text]");
    const current = root.getAttribute("data-theme");

    if (icon) icon.className = current === "dark" ? "bi bi-sun-fill" : "bi bi-moon-stars-fill";
    if (text) text.textContent = current === "dark" ? "Light" : "Dark";
  }

  window.toggleSkillSwapTheme = function () {
    const current = root.getAttribute("data-theme");
    const next = current === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    localStorage.setItem("skillswap-theme", next);
    updateIcon();
  };

  document.addEventListener("DOMContentLoaded", updateIcon);
})();

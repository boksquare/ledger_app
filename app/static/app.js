/* Pie chart wiring + dark-mode toggle. Chart re-runs after every HTMX swap. */
(function () {
  let pie = null;

  // Fixed qualitative set, muted: sage, slate, ochre, dusty rose, clay, moss, plum, sand.
  const PALETTE = [
    "#8a9b6e", "#3d5a80", "#c9a227", "#c08497",
    "#b26e4b", "#5f7470", "#85678f", "#d3b88c",
  ];

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function initPie() {
    const dataEl = document.getElementById("pie-data");
    const canvas = document.getElementById("category-pie");
    if (pie) { pie.destroy(); pie = null; }
    if (!dataEl || !canvas) return;

    const { month, filter_category_id, slices } = JSON.parse(dataEl.textContent);

    pie = new Chart(canvas, {
      type: "pie",
      data: {
        labels: slices.map(s => s.name),
        datasets: [{
          data: slices.map(s => s.total),
          backgroundColor: slices.map((s, i) => PALETTE[i % PALETTE.length]),
          borderColor: cssVar("--panel"),
          borderWidth: 2,
          // Emphasize the slice the list is currently filtered to.
          offset: slices.map(s => (s.category_id === filter_category_id ? 18 : 0)),
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 12, color: cssVar("--ink") } },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                const pct = total ? ((ctx.parsed / total) * 100).toFixed(1) : 0;
                const usd = ctx.parsed.toLocaleString("en-US", {
                  style: "currency", currency: "USD",
                });
                return ` ${ctx.label}: ${usd} (${pct}%)`;
              },
            },
          },
        },
        onClick: (evt, elements) => {
          if (!elements.length) return;
          const slice = slices[elements[0].index];
          const clearing = slice.category_id === filter_category_id;
          const url = clearing
            ? `/partials/expenses?month=${month}`
            : `/partials/expenses?month=${month}&category_id=${slice.category_id}`;
          htmx.ajax("GET", url, { target: "#dashboard-content" });
        },
        onHover: (evt, elements) => {
          evt.native.target.style.cursor = elements.length ? "pointer" : "default";
        },
      },
    });
  }

  // ---- Dark mode ----
  function syncToggleIcon() {
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = document.documentElement.dataset.theme === "dark" ? "☀️" : "🌙";
  }

  function wireThemeToggle() {
    const btn = document.getElementById("theme-toggle");
    if (!btn || btn.dataset.wired) return;
    btn.dataset.wired = "1";
    btn.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("theme", next);
      syncToggleIcon();
      initPie(); // re-render with the new theme's colors
    });
    syncToggleIcon();
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireThemeToggle();
    initPie();
  });
  document.body.addEventListener("htmx:afterSwap", () => initPie());
})();

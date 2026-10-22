const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
const figure = document.querySelector(".figure");

if (figure != null && "IntersectionObserver" in globalThis) {
  new IntersectionObserver(([entry]) => {
    figure.classList.toggle("paused", !entry.isIntersecting);
  }).observe(figure);
}

const counters = document.querySelectorAll("[data-count]");

if (!still && counters.length > 0 && "IntersectionObserver" in globalThis) {
  const format = new Intl.NumberFormat("en-US");
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) {
          continue;
        }
        observer.unobserve(entry.target);
        const node = entry.target;
        const target = Number(node.dataset.count);
        const suffix = node.dataset.suffix ?? "";
        node.style.minInlineSize = `${node.getBoundingClientRect().width}px`;
        const start = performance.now();
        const step = (now) => {
          const t = Math.min((now - start) / 1400, 1);
          const eased = 1 - (1 - t) ** 4;
          node.textContent = `${format.format(Math.round(target * eased))}${suffix}`;
          if (t < 1) {
            requestAnimationFrame(step);
          }
        };
        requestAnimationFrame(step);
      }
    },
    { threshold: 0.6 },
  );
  for (const node of counters) {
    observer.observe(node);
  }
}

// Turns each link to a terminal recording, written in a page as [Title](casts/name.cast){ .cli-cast },
// into an asciinema player that plays once it's scrolled into view. Without JavaScript the link stays.
(() => {
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const fontFamily = getComputedStyle(document.body).getPropertyValue("--md-code-font-family").trim();

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          observer.unobserve(entry.target);
          entry.target.cliPlayer.play();
        }
      }
    },
    { threshold: 0.5 },
  );

  function setup() {
    for (const link of document.querySelectorAll("a.cli-cast")) {
      const recording = document.createElement("div");
      recording.className = "cli-cast";
      const title = document.createElement("p");
      title.className = "cli-cast__title";
      title.textContent = link.textContent;
      const screen = document.createElement("div");
      recording.append(title, screen);
      // The link is the only thing in its paragraph, which the recording takes the place of, with the paragraphs
      // describing it, if any follow, moved between its title and the player.
      const replaced = link.parentElement.childElementCount === 1 ? link.parentElement : link;
      let next = replaced.nextElementSibling;
      while (next?.classList.contains("cli-cast-description")) {
        const description = next;
        next = next.nextElementSibling;
        screen.before(description);
      }
      replaced.replaceWith(recording);

      screen.cliPlayer = AsciinemaPlayer.create(link.href, screen, {
        fit: "width",
        theme: "cli",
        // The keys a recording presses, from its input events. Off unless asked for.
        keystrokeOverlay: true,
        // The longest pause shown comes from the recording, as one comparing how long commands take has none.
        terminalFontFamily: fontFamily ? `${fontFamily}, monospace` : undefined,
        // With reduced motion it waits to be played, showing how the recording ends.
        poster: reducedMotion ? "npt:9999" : undefined,
      });
      if (!reducedMotion) {
        observer.observe(screen);
      }
      showClicks(screen);
    }
  }

  // The player shows the keys a recording presses, with keystrokeOverlay, but not a mouse click. A click is
  // shown as a ring where it lands, with a label beside it. A click's input event is a press and release as xterm
  // reports them in SGR mode, ESC [ < button ; column ; row M, counting from 1.
  const click = /^\x1b\[<\d+;(\d+);(\d+)M/;

  function showClicks(screen) {
    screen.cliPlayer.addEventListener("input", ({ data }) => {
      const found = click.exec(data);
      const term = found && screen.querySelector('[style*="--term-cols"]');
      if (!term) {
        return;
      }
      const columns = Number(term.style.getPropertyValue("--term-cols"));
      const rows = Number(term.style.getPropertyValue("--term-rows"));
      const style = getComputedStyle(term);
      const box = term.getBoundingClientRect();
      const base = screen.getBoundingClientRect();
      const left = box.left - base.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft);
      const top = box.top - base.top + parseFloat(style.borderTopWidth) + parseFloat(style.paddingTop);
      const width = term.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const height = term.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
      const shown = document.createElement("div");
      shown.className = "cli-cast__click";
      shown.setAttribute("aria-hidden", "true");
      shown.style.left = `${left + ((Number(found[1]) - 0.5) * width) / columns}px`;
      shown.style.top = `${top + ((Number(found[2]) - 0.5) * height) / rows}px`;
      const label = document.createElement("kbd");
      label.textContent = "Click";
      shown.append(label);
      shown.addEventListener("animationend", () => shown.remove());
      screen.append(shown);
    });
  }

  // Zensical swaps pages in place with instant navigation, announcing each through document$.
  if (typeof document$ !== "undefined") {
    document$.subscribe(setup);
  } else {
    setup();
  }
})();

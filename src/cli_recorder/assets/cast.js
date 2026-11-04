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
        // The longest pause shown comes from the recording, as one comparing how long commands take has none.
        terminalFontFamily: fontFamily ? `${fontFamily}, monospace` : undefined,
        // With reduced motion it waits to be played, showing how the recording ends.
        poster: reducedMotion ? "npt:9999" : undefined,
      });
      if (!reducedMotion) {
        observer.observe(screen);
      }
    }
  }

  // Zensical swaps pages in place with instant navigation, announcing each through document$.
  if (typeof document$ !== "undefined") {
    document$.subscribe(setup);
  } else {
    setup();
  }
})();

// Orivexs Team — orivexs.team
// Progressive enhancement only: every page is complete without this file.

(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------- OS install tabs ----------

  function detectOS() {
    const hint =
      (navigator.userAgentData && navigator.userAgentData.platform) ||
      navigator.platform ||
      navigator.userAgent ||
      "";
    if (/mac|iphone|ipad/i.test(hint)) return "macos";
    if (/win/i.test(hint)) return "windows";
    return "linux";
  }

  document.querySelectorAll("[data-os-tabs]").forEach((list) => {
    const tabs = Array.from(list.querySelectorAll('[role="tab"]'));
    const panels = tabs.map((tab) => document.getElementById(tab.getAttribute("aria-controls")));

    function select(index, focus) {
      tabs.forEach((tab, i) => {
        const on = i === index;
        tab.setAttribute("aria-selected", String(on));
        tab.tabIndex = on ? 0 : -1;
        panels[i].hidden = !on;
      });
      if (focus) tabs[index].focus();
    }

    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => select(i, false));
      tab.addEventListener("keydown", (event) => {
        let next = null;
        if (event.key === "ArrowRight") next = (i + 1) % tabs.length;
        if (event.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = tabs.length - 1;
        if (next !== null) {
          event.preventDefault();
          select(next, true);
        }
      });
    });

    const preferred = tabs.findIndex((tab) => tab.dataset.os === detectOS());
    select(preferred === -1 ? 0 : preferred, false);
  });

  // ---------- copy buttons ----------

  function selectText(node) {
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    const source = document.getElementById(button.dataset.copy);
    const label = button.querySelector("[data-copy-label]");
    let timer = 0;

    button.addEventListener("click", async () => {
      const text = Array.from(source.querySelectorAll(".cmd"))
        .map((node) => node.textContent)
        .join("\n");
      let copied = false;
      try {
        await navigator.clipboard.writeText(text);
        copied = true;
      } catch (error) {
        selectText(source);
      }
      label.textContent = copied ? "Copied" : "Selected: press Ctrl+C";
      button.classList.toggle("is-done", copied);
      clearTimeout(timer);
      timer = setTimeout(() => {
        label.textContent = "Copy";
        button.classList.remove("is-done");
      }, 2000);
    });
  });

  // ---------- project board filter ----------

  document.querySelectorAll("[data-board-filter]").forEach((group) => {
    const board = document.getElementById(group.dataset.boardFilter);
    const buttons = Array.from(group.querySelectorAll("button[data-filter]"));
    const cards = Array.from(board.querySelectorAll("[data-platform]"));
    const columns = Array.from(board.querySelectorAll(".board-col"));

    function apply(filter) {
      buttons.forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
      });
      cards.forEach((card) => {
        card.hidden = filter !== "all" && !card.dataset.platform.split(" ").includes(filter);
      });
      columns.forEach((column) => {
        const shown = column.querySelectorAll("[data-platform]:not([hidden])").length;
        column.querySelector("[data-col-count]").textContent = String(shown);
        column.querySelector(".board-empty").hidden = shown > 0;
      });
    }

    buttons.forEach((button) => button.addEventListener("click", () => apply(button.dataset.filter)));
    apply("all");
  });

  // ---------- launch list sign-up ----------
  // Posts to the form's action (a Formspree endpoint). Until a real form ID is
  // pasted in, the request is handed to the visitor's email app instead.

  const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  function listApps(apps) {
    return apps.length > 1 ? apps.slice(0, -1).join(", ") + " and " + apps[apps.length - 1] : apps[0];
  }

  document.querySelectorAll("[data-notify-form]").forEach((form) => {
    const emailInput = form.querySelector('input[name="email"]');
    const boxes = Array.from(form.querySelectorAll('input[name="apps"]'));
    const status = form.querySelector(".notify-status");
    const button = form.querySelector('button[type="submit"]');
    const done = form.parentElement.querySelector(".notify-done");
    const doneText = done.querySelector(".notify-done-text");

    function fail(message, field) {
      status.textContent = message;
      if (field) {
        field.setAttribute("aria-invalid", "true");
        field.focus();
      }
    }

    emailInput.addEventListener("input", () => {
      emailInput.removeAttribute("aria-invalid");
      status.textContent = "";
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const email = emailInput.value.trim();
      const apps = boxes.filter((box) => box.checked).map((box) => box.value);
      if (!EMAIL_PATTERN.test(email)) return fail("Please enter a valid email address.", emailInput);
      if (!apps.length) return fail("Pick at least one app.", boxes[0]);
      if (form.elements._gotcha.value) return; // filled in by a bot

      const endpoint = form.getAttribute("action") || "";
      if (!endpoint || endpoint.includes("YOUR_FORM_ID")) {
        const subject = "Notify me: " + listApps(apps) + " release";
        const body = "Please email me at " + email + " when " + listApps(apps) + (apps.length > 1 ? " are" : " is") + " released.";
        window.location.href =
          "mailto:oliver@orivexs.team?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(body);
        status.textContent = "Your email app should open with the request ready to send.";
        return;
      }

      const data = new FormData();
      data.append("email", email);
      data.append("apps", apps.join(", "));
      data.append("_subject", form.elements._subject.value);
      button.disabled = true;
      status.textContent = "Sending...";
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          body: data,
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error("HTTP " + response.status);
        status.textContent = "";
        doneText.textContent =
          "We will email " + email + " when " + listApps(apps) + (apps.length > 1 ? " are" : " is") + " released.";
        form.hidden = true;
        done.hidden = false;
        done.focus();
      } catch (error) {
        status.textContent = "That did not go through. Please try again, or email oliver@orivexs.team.";
      } finally {
        button.disabled = false;
      }
    });

    done.querySelector("[data-notify-again]").addEventListener("click", () => {
      emailInput.value = "";
      done.hidden = true;
      form.hidden = false;
      emailInput.focus();
    });

    document.querySelectorAll("[data-notify-app]").forEach((link) => {
      link.addEventListener("click", () => {
        boxes.forEach((box) => {
          box.checked = box.dataset.app === link.dataset.notifyApp;
        });
        done.hidden = true;
        form.hidden = false;
        setTimeout(() => emailInput.focus({ preventScroll: true }), 500);
      });
    });
  });

  // ---------- scroll reveal ----------

  const revealed = document.querySelectorAll(".reveal");
  if (reduceMotion || !("IntersectionObserver" in window)) {
    revealed.forEach((node) => node.classList.add("is-in"));
  } else {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-in");
            observer.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -10% 0px", threshold: 0.12 }
    );
    revealed.forEach((node) => observer.observe(node));
  }

  // ---------- task session playback ----------

  const screen = document.querySelector("[data-session]");
  if (screen && !reduceMotion) {
    const lines = Array.from(screen.querySelectorAll(".t-line"));
    const counters = Array.from(document.querySelectorAll("[data-count]"));
    const STEP_MS = 340;
    const HOLD_MS = 5000;
    let shown = 0;
    let timer = 0;
    let running = false;

    function updateCounters() {
      counters.forEach((counter) => {
        const kind = counter.dataset.count;
        counter.textContent = String(
          lines.slice(0, shown).filter((line) => line.dataset.kind === kind).length
        );
      });
    }

    function reset() {
      shown = 0;
      lines.forEach((line) => line.classList.add("is-off"));
      updateCounters();
    }

    function tick() {
      if (shown < lines.length) {
        const line = lines[shown];
        line.classList.remove("is-off");
        line.classList.add("is-new");
        shown += 1;
        updateCounters();
        timer = setTimeout(tick, STEP_MS);
      } else {
        timer = setTimeout(() => {
          reset();
          tick();
        }, HOLD_MS);
      }
    }

    reset();
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.some((entry) => entry.isIntersecting);
        if (visible && !running) {
          running = true;
          tick();
        } else if (!visible && running) {
          running = false;
          clearTimeout(timer);
        }
      },
      { threshold: 0.25 }
    );
    observer.observe(screen);
  }
})();

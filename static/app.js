(function () {
  "use strict";

  const form = document.getElementById("chat-form");
  const input = document.getElementById("question");
  const sendBtn = document.getElementById("send");
  const messages = document.getElementById("messages");
  const suggestions = document.getElementById("suggestions");

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* The two skill figures, published for whoever drives them. `skillNetBridge`
     carries the chat's hook into the network; `careerArcBridge` is the walk's
     handle on the arc. Each IIFE fills its own in if it starts, both stay null
     otherwise — the network never starts under reduced motion or without a canvas, and the
     arc is absent from a fork with no `career` block — and every call site null
     checks. Here rather than on `window` so the features stay decoupled without
     leaking a global. */
  let skillNetBridge = null;
  let careerArcBridge = null;

  /* Filter state of the skill network, published for the panel cloud behind it.
     Plain state that skillNet writes and the cloud reads on its own frames,
     rather than a callback either way: the cloud arrives late (it is behind a
     dynamic import) and skillNet may never start at all, so a handle held by
     either side would need ordering rules and a null check on every use. Shared
     state has neither problem — whoever is running reads the latest values, and
     the defaults below are what the cloud sees if skillNet never runs.

     `n` and `total` drive a *uniform* thin, and the discipline filter is what
     moves them: one discipline showing thins the cloud, All restores it.
     `changedAt` is the quiet, mono flash the same filter fires through
     `flashCloud`. */
  // `centroids` is one entry per discipline, in -1..1 across the network's
  // canvas, or null where that discipline is filtered out. Empty by default,
  // which is what the cloud sees when skillNet never runs at all.
  const netFilter = { n: 1, total: 1, changedAt: -1e9, strong: false, tint: null, centroids: [] };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  /* ======================================================================
     Theme
     The six spectrum colors live in CSS (:root and :root[data-theme=light]).
     Anything that needs them in JS reads them back out, so the canvas always
     matches the stylesheet and both themes stay in sync from one source.
     ====================================================================== */
  const root = document.documentElement;
  const themeBtn = document.getElementById("theme-toggle");
  const themeListeners = [];

  function palette() {
    const cs = getComputedStyle(root);
    const out = [];
    for (let i = 1; i <= 6; i++) {
      out.push(cs.getPropertyValue("--c" + i).trim() || "#4d8dff");
    }
    return out;
  }

  function cssVar(name, fallback) {
    return getComputedStyle(root).getPropertyValue(name).trim() || fallback;
  }

  function onThemeChange(fn) {
    themeListeners.push(fn);
  }

  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      const next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      root.setAttribute("data-theme", next);
      try {
        localStorage.setItem("theme", next);
      } catch (e) {
        /* private mode: theme just won't persist */
      }
      // Let the new custom-property values land before anything reads them.
      requestAnimationFrame(function () {
        themeListeners.forEach(function (fn) {
          fn();
        });
      });
    });
  }

  /* ======================================================================
     Scroll progress bar
     ====================================================================== */
  const progress = document.getElementById("progress");
  if (progress) {
    let ticking = false;
    function updateProgress() {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const p = max > 0 ? Math.min(1, window.scrollY / max) : 0;
      progress.style.transform = "scaleX(" + p + ")";
      ticking = false;
    }
    window.addEventListener(
      "scroll",
      function () {
        if (!ticking) {
          ticking = true;
          requestAnimationFrame(updateProgress);
        }
      },
      { passive: true }
    );
    updateProgress();
  }

  /* ======================================================================
     Scroll reveal: sections fade and rise as they enter the viewport.
     ====================================================================== */
  const revealables = document.querySelectorAll(".reveal");
  if (revealables.length && "IntersectionObserver" in window && !reduceMotion.matches) {
    const io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("in");
            io.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.08 }
    );
    revealables.forEach(function (node) {
      io.observe(node);
    });
  } else {
    revealables.forEach(function (node) {
      node.classList.add("in");
    });
  }

  /* ======================================================================
     Stat band: count up from zero the first time the band is seen.
     ====================================================================== */
  (function statCounters() {
    // Tiles live in the carousel columns, or in the legacy band; either way
    // the count-up is per tile, so no container is assumed.
    const cells = document.querySelectorAll(".stat-value");
    if (!cells.length) return;

    function format(n, target) {
      // Keep one decimal only when the target actually has one.
      const decimals = String(target).indexOf(".") >= 0 ? 1 : 0;
      return n.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      });
    }

    function run(cell) {
      const target = parseFloat(cell.getAttribute("data-value"));
      if (!isFinite(target)) return;
      const prefix = cell.getAttribute("data-prefix") || "";
      const suffix = cell.getAttribute("data-suffix") || "";
      if (reduceMotion.matches) {
        cell.textContent = prefix + format(target, target) + suffix;
        return;
      }
      const dur = 1400;
      const start = performance.now();
      function step(now) {
        const t = Math.min(1, (now - start) / dur);
        const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
        cell.textContent = prefix + format(target * eased, target) + suffix;
        if (t < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    }

    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              run(entry.target);
              io.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.4 }
      );
      cells.forEach(function (c) {
        io.observe(c);
      });
    } else {
      cells.forEach(run);
    }
  })();

  /* ======================================================================
     Chat
     ====================================================================== */
  function scrollToEnd() {
    messages.scrollTop = messages.scrollHeight;
    const last = messages.lastElementChild;
    if (last) last.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function addUser(text) {
    messages.appendChild(el("div", "msg user", text));
    scrollToEnd();
  }

  // The waiting line, built from the owner's name rather than spelling it out.
  // The name comes from profile.json via data-owner on #messages; without it the
  // sentence still reads, which is what a fork gets before it has a name to use.
  const thinkingLabel = (function () {
    const owner = messages && messages.dataset.owner;
    return owner ? "Checking " + owner + "’s background" : "Checking the background";
  })();

  function addThinking() {
    const node = el("div", "msg bot thinking");
    node.appendChild(document.createTextNode(thinkingLabel));
    const dots = el("span", "dots");
    for (let i = 0; i < 3; i++) dots.appendChild(document.createElement("i"));
    node.appendChild(dots);
    messages.appendChild(node);
    scrollToEnd();
    return node;
  }

  // Turn plain URLs into clickable links without ever using innerHTML on model
  // output (keeps it safe from HTML injection).
  function appendLinkified(node, text) {
    const urlRe = /(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g;
    let last = 0;
    let m;
    while ((m = urlRe.exec(text)) !== null) {
      if (m.index > last) {
        node.appendChild(document.createTextNode(text.slice(last, m.index)));
      }
      const a = document.createElement("a");
      a.href = m[0];
      a.textContent = m[0];
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      node.appendChild(a);
      last = m.index + m[0].length;
    }
    if (last < text.length) {
      node.appendChild(document.createTextNode(text.slice(last)));
    }
  }

  function renderBot(node, answer, sources) {
    node.className = "msg bot";
    node.textContent = "";
    appendLinkified(node, answer);
    if (sources && sources.length) {
      node.appendChild(el("span", "sources", "Sources: " + sources.join(", ")));
    }
    /* The saved-answer path used to append "Saved answer, from the same checked
       pipeline." here, on the reasoning that an answer appearing instantly
       should not pass for one just written. Removed: it is a disclaimer about
       the site's plumbing addressed to a reader who has not asked, on the four
       answers most visitors see first, and the thing it was protecting is not
       actually at risk. The text is the same pipeline's output, judged and
       passed, and the sources line beneath it is the provenance that matters.
       What must stay true is that nothing unjudged reaches the screen, and that
       is enforced in the pipeline, not in a caption. */
    scrollToEnd();
  }

  /* ----------------------------------------------------------------------
     Saved answers to the starter questions.

     Embedded in the page by the server, which has already checked that they
     still match the corpus they were generated from and sent `[]` if they do
     not (load_starter_answers in app/profile.py). So there is nothing to
     validate here: an entry that arrives is one that may be shown.

     Matching is on a normalized question, so a chip and someone typing the same
     sentence with different spacing or a missing question mark both hit. It is
     deliberately not fuzzy. A near miss served from this table would answer a
     question the visitor did not ask, with the confidence of a checked answer,
     and the pipeline is cheap enough that the honest thing is to just run it.
     ---------------------------------------------------------------------- */
  function normalizeQuestion(text) {
    return text.trim().toLowerCase().replace(/\s+/g, " ").replace(/[?.!]+$/, "");
  }

  const savedAnswers = (function () {
    const node = document.getElementById("starter-answers");
    const map = new Map();
    if (!node) return map;
    try {
      const entries = JSON.parse(node.textContent);
      if (Array.isArray(entries)) {
        entries.forEach(function (entry) {
          if (entry && entry.question && entry.answer) {
            map.set(normalizeQuestion(entry.question), entry);
          }
        });
      }
    } catch (e) {
      /* malformed payload: fall through to asking live */
    }
    return map;
  })();

  /* ----------------------------------------------------------------------
     Streaming answer view.

     Tokens land in a plain text node while the answer is being written, so
     there is no per-token DOM churn and no chance of a half-written URL being
     linkified. Once the stream closes, the accumulated text is re-rendered
     through appendLinkified so links become clickable, and the sources line is
     appended. Auto-scroll follows the text only while the reader is already at
     the bottom, so scrolling up to re-read an earlier answer is not fought.
     ---------------------------------------------------------------------- */
  function streamView(node) {
    node.className = "msg bot streaming";
    node.textContent = "";
    const body = document.createTextNode("");
    node.appendChild(body);
    const caret = el("span", "caret");
    node.appendChild(caret);

    let text = "";

    function nearBottom() {
      return messages.scrollHeight - messages.scrollTop - messages.clientHeight < 60;
    }

    return {
      push: function (chunk) {
        const stick = nearBottom();
        text += chunk;
        body.nodeValue = text;
        if (stick) messages.scrollTop = messages.scrollHeight;
      },
      finish: function (sources) {
        renderBot(node, text, sources);
      },
      text: function () {
        return text;
      },
    };
  }

  // Read an SSE body. `onEvent` gets each parsed JSON payload. Frames are
  // separated by a blank line; a chunk from the network can split one anywhere,
  // so the tail is carried over to the next read.
  async function readEventStream(res, onEvent) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });

      let split;
      while ((split = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data:")) continue;
          try {
            onEvent(JSON.parse(line.slice(5).trim()));
          } catch (e) {
            /* a malformed frame is skipped rather than killing the stream */
          }
        }
      }
    }
  }

  function renderError(node, text) {
    node.className = "msg error";
    node.textContent = text;
    scrollToEnd();
  }

  async function errorDetail(res) {
    let detail = "Something went wrong. Please try again.";
    try {
      const err = await res.json();
      if (err && err.detail) detail = err.detail;
    } catch (e) {
      /* keep default */
    }
    return detail;
  }

  // Fallback for browsers without a readable fetch body (and for any stream that
  // fails before the first token). Same pipeline, delivered in one piece.
  async function askBlocking(question, node) {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question }),
    });
    if (!res.ok) {
      renderError(node, await errorDetail(res));
      return;
    }
    const data = await res.json();
    // An empty answer is a pipeline bug, not a reply. Rendering it produces a
    // blank bubble that looks like the page broke; say what happened instead.
    if (!data.answer || !data.answer.trim()) {
      renderError(node, "The assistant did not return an answer. Please try asking again.");
      return;
    }
    renderBot(node, data.answer, data.sources);
    if (skillNetBridge) skillNetBridge.scan(data.answer);
  }

  async function ask(question) {
    addUser(question);
    input.value = "";

    // Saved answer: render it and make no request. Checked before the composer
    // is disabled or a thinking indicator appears, because there is nothing to
    // wait for, and a spinner shown for an answer already in memory would be a
    // loading state staged for effect.
    const saved = savedAnswers.get(normalizeQuestion(question));
    if (saved) {
      const node = el("div", "msg bot");
      messages.appendChild(node);
      renderBot(node, saved.answer, saved.sources);
      if (skillNetBridge) {
        skillNetBridge.reset();
        skillNetBridge.scan(saved.answer);
      }
      input.focus();
      return;
    }


    sendBtn.disabled = true;
    input.disabled = true;
    const thinking = addThinking();
    if (skillNetBridge) skillNetBridge.reset();

    try {
      if (!window.ReadableStream || !window.TextDecoder) {
        await askBlocking(question, thinking);
        return;
      }

      const res = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question }),
      });

      if (!res.ok || !res.body) {
        // A guardrail rejection (429/413) is a real answer, not a transport
        // problem, so show it rather than silently retrying the other endpoint.
        if (res.status >= 400 && res.status < 500) {
          renderError(thinking, await errorDetail(res));
        } else {
          await askBlocking(question, thinking);
        }
        return;
      }

      let view = null;
      let sources = [];
      let failed = null;
      let completed = false;

      await readEventStream(res, function (ev) {
        if (ev.type === "sources") {
          sources = ev.sources || [];
        } else if (ev.type === "token") {
          if (!view) view = streamView(thinking);
          view.push(ev.text);
          // Light up any skill the answer has just named. Called per token so
          // the network reacts as the sentence is written, not after it lands.
          if (skillNetBridge) skillNetBridge.scan(view.text());
        } else if (ev.type === "error") {
          failed = ev.detail || "The assistant is unavailable right now.";
        } else if (ev.type === "done") {
          completed = true;
        }
      });

      if (failed && !view) {
        renderError(thinking, failed);
      } else if (view) {
        view.finish(sources);
      } else if (completed) {
        // The stream ran to `done` and the pipeline produced no tokens. Retrying
        // on /api/chat would run the same two passes again to render the same
        // emptiness, and each run is roughly a thirtieth of the day's token
        // budget, so the retry costs a real answer someone else could have had.
        // Only a stream that never reached `done` is worth repeating.
        renderError(thinking, "The assistant did not return an answer. Please try asking again.");
      } else {
        // Ended before `done`: the connection dropped mid-stream. That is a
        // transport failure and the pipeline may never have run, so falling back
        // is worth the second call.
        await askBlocking(question, thinking);
      }
    } catch (e) {
      renderError(thinking, "Network error. Please check your connection and try again.");
    } finally {
      sendBtn.disabled = false;
      input.disabled = false;
      input.focus();
    }
  }

  /* ======================================================================
     Highlights carousel: arrows scroll the track by one card at a time.
     ====================================================================== */
  const track = document.getElementById("car-track");
  const prevArrow = document.querySelector(".car-arrow.prev");
  const nextArrow = document.querySelector(".car-arrow.next");

  function cardStep() {
    if (!track) return 284;
    const card = track.querySelector(".card");
    if (!card) return 284;
    return card.getBoundingClientRect().width + 16; // card width + gap
  }

  if (track && prevArrow && nextArrow) {
    prevArrow.addEventListener("click", function () {
      track.scrollBy({ left: -cardStep(), behavior: "smooth" });
    });
    nextArrow.addEventListener("click", function () {
      track.scrollBy({ left: cardStep(), behavior: "smooth" });
    });
  }

  // Depth: each card's distance from the middle of the track drives --dist,
  // which the stylesheet turns into scale and opacity. Measured in viewport
  // coordinates via getBoundingClientRect, so it does not care about scroll
  // offsets, snap points, or how many cards there are.
  if (track && !reduceMotion.matches) {
    // The column, not the card: the card and its tile read --dist from it.
    const cards = Array.prototype.slice.call(track.querySelectorAll(".car-col"));

    function paintDepth() {
      const box = track.getBoundingClientRect();
      const mid = box.left + box.width / 2;
      // Half the track is the reference distance, so a card at either edge
      // reaches roughly 1 no matter how wide the viewport is.
      const reach = Math.max(1, box.width / 2);
      cards.forEach(function (card) {
        const r = card.getBoundingClientRect();
        const d = Math.abs(r.left + r.width / 2 - mid) / reach;
        card.style.setProperty("--dist", Math.min(1, d).toFixed(3));
      });
    }

    let depthTicking = false;
    function queueDepth() {
      if (depthTicking) return;
      depthTicking = true;
      requestAnimationFrame(function () {
        paintDepth();
        depthTicking = false;
      });
    }

    track.addEventListener("scroll", queueDepth, { passive: true });
    window.addEventListener("resize", queueDepth);
    paintDepth();
  }

  /* ======================================================================
     Masthead: decode the role line on load. The text resolves left to right
     out of random glyphs, which gives the first second of the page a bit of
     motion without moving any layout (the scrambled string is always the same
     length as the real one, so nothing reflows).
     ====================================================================== */
  (function scrambleRole() {
    const node = document.querySelector(".role");
    if (!node || reduceMotion.matches) return;

    const target = node.textContent;
    if (!target.trim()) return;
    const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&$/\\<>*+=";
    const DUR = 1100;
    const start = performance.now();

    // Reserve the final size up front: the element is inline-block, and a
    // scrambled string of the same length can still measure slightly different
    // in a proportional font.
    node.style.minWidth = node.getBoundingClientRect().width + "px";

    function step(now) {
      const t = Math.min(1, (now - start) / DUR);
      // Ease-out so the tail of the word settles rather than snapping.
      const resolved = Math.floor((1 - Math.pow(1 - t, 2)) * target.length);
      let out = "";
      for (let i = 0; i < target.length; i++) {
        const ch = target[i];
        if (i < resolved || ch === " ") {
          out += ch;
        } else {
          out += GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
        }
      }
      node.textContent = out;
      if (t < 1) {
        requestAnimationFrame(step);
      } else {
        node.textContent = target;
        node.style.minWidth = "";
      }
    }
    requestAnimationFrame(step);
  })();

  /* ======================================================================
     Results: below 760px the chart is hidden, because a fixed 980-unit viewBox
     scaled into that column renders its value labels at about 10 CSS px and
     shrinking. The table carries the same numbers at full size, so on a narrow
     viewport it is the figure rather than a fallback, and it opens by default.

     Here rather than in the template because the condition is the viewport,
     which the server does not know. Read once rather than watched: this decides
     an initial state, and a visitor who has opened or closed the panel by hand
     should not have that undone by a rotation. The no-JS resting state is the
     closed <details>, whose summary is still visible and still works.
     ====================================================================== */
  (function resultsTable() {
    // querySelectorAll, not querySelector: there are two of these now, the
    // results chart's and the eval panel's, and a single-element lookup would
    // silently open the first and leave the second collapsed at the one width
    // where its figure is hidden and the table IS the figure.
    const tables = document.querySelectorAll(".results-table");
    if (!window.matchMedia("(max-width: 760px)").matches) return;
    tables.forEach(function (table) { table.open = true; });
  })();

  /* ======================================================================
     Download PDF. Two shapes, decided by the template: a link to a real file
     when profile.json names one, and a button that opens the résumé panel (so
     its content isn't clipped by the print CSS) and hands off to the browser's
     print-to-PDF when it does not.

     Both sit inside <summary>, so either way the click must not also toggle the
     panel. The element is asked whether it is a link rather than the condition
     being re-tested here, for the same reason the walks ask the element: a
     second copy of the template's `if` goes stale the first time it moves.
     ====================================================================== */
  const printBtn = document.getElementById("resume-print");
  const resumeDetails = document.getElementById("resume-details");
  if (printBtn && resumeDetails) {
    printBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (printBtn.hasAttribute("href")) return;  // a real file: the browser takes it
      e.preventDefault();
      resumeDetails.open = true;
      window.print();
    });
  }

  /* ======================================================================
     scrollWalk — the story driver, with the clock handed to the reader

     The figure sticks, the steps beside it scroll past, and whichever step is
     level with the figure is the one the figure is showing.

     It used to have a sibling, `filterTour`, which walked the same filters on a
     4.6s timer wherever the CSS did not build a walk, driven from the two legends
     under the figures. Both legends are gone (see the skills section in
     index.html), which took the tour's only control and its only narration with
     them: a figure that re-filters itself on a timer, with no chip saying what is
     lit and no step lit beside it, is a page changing on its own for no stated
     reason. Below the walk's breakpoint both figures now simply rest whole, with
     the seven steps under them as a plain list, which is what a reduced-motion
     visitor has always been given here.

     It is deliberately the driver and nothing else. Which element sticks, how
     tall a step is, and whether the steps sit beside the figure or under it are
     all the stylesheet's business — this only asks the DOM for `.walk-figure`
     and `.walk-step` and never for a section name. A second walk is a `.walk`
     block in the template plus one call here.

     It returns true when it built and null when the CSS did not lay out a walk,
     and nothing else: there is no `stop` any more, because the legend click that
     used to cancel a walk permanently no longer exists. A walk now runs for as
     long as its section is on the page.

     Four things are deliberate:

       * Whether this runs at all is read back off the layout
         (`.walk-figure`'s computed `position`) rather than re-tested against the
         media query that built it. The condition is three clauses long — wide,
         reduced-motion-free, and `.js` — and a second copy of it in JS is a copy
         that goes stale the first time any one of them moves. A static figure
         means the CSS did not build a walk, and returning null here is what
         leaves both figures resting whole.

       * The trigger is the *figure's* centre, not a fixed fraction of the
         viewport. The figure is what the reader is looking at, and on a short
         window it is pinned near the top rather than centred, so a fixed line
         would step the figure a screenful before or after the step that
         explains it. It is also why the tier that drops both boxes from 560 to
         460 needs no change here: the figure reports its own centre.

       * It holds the all-on state until the first step actually reaches that
         line, and that beat is bought by `padding-top` on `.walk-steps` rather
         than here. A reader arriving at this section has not seen either figure
         whole yet, and isolating before they get there would mean the complete
         figure — all 58 chords, and the whole network lit — is never on screen
         at all.

       * A step maps to `opts.steps[i]`, whatever that is. This section passes
         `{eng, skills}` records; the driver never looks inside one, which is
         why merging two walks that passed an index and a list of indices cost
         this function no change at all.

     There is no timer, no IntersectionObserver and no visibilitychange here.
     Scroll events do not fire on a section nobody is scrolling past.
     ====================================================================== */
  function scrollWalk(opts) {
    const figure = opts.root.querySelector(".walk-figure");
    const steps = Array.prototype.slice.call(opts.root.querySelectorAll(".walk-step"));
    if (!figure || steps.length !== opts.steps.length || steps.length < 2) return null;
    if (getComputedStyle(figure).position !== "sticky") return null;

    let cur = -2; // neither an index nor the all-on -1, so the first pass applies
    let ticking = false;

    function paint() {
      ticking = false;

      const fr = figure.getBoundingClientRect();
      const line = (fr.top + fr.bottom) / 2;

      // Above the first step: the whole arc, not the first engagement.
      const first = steps[0].getBoundingClientRect();
      let pick = -1;
      if ((first.top + first.bottom) / 2 <= line) {
        let bestD = Infinity;
        steps.forEach(function (s, i) {
          const r = s.getBoundingClientRect();
          const d = Math.abs((r.top + r.bottom) / 2 - line);
          if (d < bestD) {
            bestD = d;
            pick = i;
          }
        });
      }

      if (pick === cur) return;
      cur = pick;
      steps.forEach(function (s, i) {
        s.classList.toggle("is-live", i === pick);
      });
      opts.apply(pick < 0 ? null : opts.steps[pick]);
    }

    /* Both events share one frame gate. `paint` reads a rect off the figure and
       off every step, so calling it per event rather than per frame puts a
       layout flush on each tick of a scroll or each frame of a window drag.
       Resize used to call it directly, which made the drag path the expensive
       one for no reason: it wants the same coalescing scroll already has. */
    function schedule() {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(paint);
    }
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    /* The first pass runs on the next frame, not here. Calling `apply` during
       construction makes the walk a constructor with a side effect, which
       forces every caller to have its filter state fully built before it
       constructs the walk. The network did not, back when this was built from
       inside the legend block: its `active` set was null until the first layout
       pass, and calling in early took the whole panel down with a TypeError that
       looked like a layout bug. The driver is now built after both figures, so
       that particular ordering is no longer live — the deferral stays because it
       is what makes the driver safe to construct from anywhere, which is the
       property that let it move. Deferring costs one frame of the all-on state,
       which is the state the section is in at that point anyway. */
    requestAnimationFrame(paint);

    return true;
  }

  /* ======================================================================
     Masthead particle cloud

     The only WebGL on the page, and the only thing that is code-split, because
     three.js plus the module is ~750 KB against ~60 KB for everything else.
     Every reason not to render it is checked here, before the import, so a
     phone or a reduced-motion visitor never pays for a feature they are not
     shown. The CSS hides the element under the same two conditions; this is the
     half that keeps the bytes off the wire.

     `?v=` is carried through by hand: app.js is versioned by the template, but
     a URL built in JS is not, and a stale module against a fresh app.js is
     exactly the kind of skew the query string exists to prevent.
     ====================================================================== */
  (function mastheadCloud() {
    const mount = document.getElementById("masthead-3d");
    // The gate is on the conditions, not on any one host: both clouds are hidden
    // by the same media queries in CSS, so both have to be skipped by the same
    // check here or the bytes get fetched for something that will not be shown.
    const wide = window.matchMedia("(min-width: 901px)");
    if (reduceMotion.matches || !wide.matches) return;

    // A context probe, not a feature-detect on the constructor: a browser can
    // expose WebGLRenderingContext and still refuse a context on a blocklisted
    // GPU, and finding that out after the import wastes the download.
    let ok = false;
    try {
      const probe = document.createElement("canvas");
      ok = !!(probe.getContext("webgl2") || probe.getContext("webgl"));
    } catch (e) {
      ok = false;
    }
    if (!ok) return;

    let groups = [];
    const dataEl = document.getElementById("skills-data");
    if (dataEl) {
      try {
        const parsed = JSON.parse(dataEl.textContent);
        if (Array.isArray(parsed)) groups = parsed;
      } catch (e) {
        /* the cloud falls back to a single placeholder lobe */
      }
    }

    const v = (document.currentScript && document.currentScript.src.split("?v=")[1]) || "";
    // One import for both mounts. The dynamic import is cached by the browser,
    // so the ~750 KB is fetched and parsed once however many clouds are mounted;
    // what a second mount costs is a second WebGL context and a second render
    // loop, and the loops are already stopped whenever their host is off screen.
    // The header and the skill panel are never both on screen, so in practice
    // one is running at a time.
    const load = import("/static/masthead3d.js" + (v ? "?v=" + v : ""));

    function cloud(el, preset, extra) {
      if (!el) return;
      const cv = el.querySelector("canvas");
      if (!cv) return;
      load
        .then(function (mod) {
          const cfg = {
            canvas: cv,
            preset: preset,
            groups: groups,
            palette: palette,
            isLight: function () {
              return root.getAttribute("data-theme") === "light";
            },
            onThemeChange: onThemeChange,
          };
          if (extra) Object.keys(extra).forEach(function (k) { cfg[k] = extra[k]; });
          if (!mod.mountCloud(cfg)) el.remove();
        })
        .catch(function () {
          // Both hosts read fine without a cloud; a dead canvas stretched across
          // either does not, so failure removes the element rather than leaving
          // it.
          el.remove();
        });
    }

    /* The header's cycle, from profile.json by way of the mount's data-forms.
       Passed as names only: which shapes to show is the owner's choice, how to
       draw each one is masthead3d.js's, and an unknown name is dropped there
       rather than checked here so the two cannot disagree. */
    const wanted = ((mount && mount.getAttribute("data-forms")) || "")
      .split(",")
      .map(function (s) {
        return s.trim();
      })
      .filter(Boolean);
    cloud(mount, "header", wanted.length ? { forms: wanted } : null);
    // The constellation behind the network, painted in the nebula's off-scale
    // hue rather than the six discipline colors. See mono in masthead3d.js.
    cloud(document.getElementById("net-3d"), "panel", {
      mono: function () {
        return cssVar("--neb", "#5a6ae0");
      },
      filter: netFilter,
    });
  })();

  /* ======================================================================
     Skillset neural network

     One connected network, not six islands. Every skill is a neuron colored by
     its discipline. Two kinds of synapse:

       * intra-group  — a sparse ring-plus-chord wiring inside each discipline,
                        stroked in that discipline's single color, so a cluster
                        still reads as one coherent group.
       * cross-group  — a smaller set of long links stitching every discipline
                        to its neighbours, stroked with a gradient that fades
                        from one group's color into the other's. These are what
                        make the whole thing read as a brain rather than as
                        separate constellations.

     The cross links are chosen so the graph is guaranteed connected (a ring
     over the groups) plus a handful of random long-range shortcuts.

     Signals ("pulses") travel along random synapses and light them up. Hovering
     a neuron dims everything except that neuron and its direct connections.
     ====================================================================== */
  (function skillNet() {
    // Its own section, found from the canvas: two sections carry .skills now,
    // and the first one in the document is the arc's.
    const canvas = document.getElementById("skill-net");
    const section = canvas && canvas.closest("section");
    const dataEl = document.getElementById("skills-data");
    if (!section || !canvas || !dataEl) return;

    // [[groupName, [skill, ...]], ...] in profile.json order. Index i here is
    // the same i the legend and the CSS tints use, which is what keeps node
    // colors and the filter buttons in agreement.
    let groups;
    try {
      groups = JSON.parse(dataEl.textContent);
    } catch (e) {
      return;
    }
    if (!Array.isArray(groups) || groups.length === 0) return;
    const groupNames = groups.map(function (g) {
      return g[0];
    });

    // Respect reduced-motion: leave the chip fallback in place, don't animate.
    if (reduceMotion.matches) return;

    section.classList.add("animated");

    const TAU = Math.PI * 2;

    // Physics. Intra-group springs are short and stiff, so a discipline holds
    // together as a tight cluster; cross-group springs are long and slack, so
    // clusters stay distinct while still being pulled into one body.
    const REST_IN = 124;
    const REST_CROSS = 360;
    const K_IN = 0.0019;
    const K_CROSS = 0.0005;
    const REPEL = 104; // universal short-range separation between neurons
    const K_REPEL = 0.075;
    const SEP = 132; // extra separation between neurons of DIFFERENT groups
    const K_SEP = 0.05;
    const K_LABEL = 0.3; // extra push when two labels would overlap
    const LABEL_H = 21; // label band height; below this, labels can collide
    // Pull toward the cluster's anchor wedge. Applied to the cluster's centroid
    // and shared equally by its members, so it slides the cluster across the
    // panel without compressing it. A per-node pull would do the latter.
    const K_ANCHOR = 0.0022;

    // Every distance above is scaled at runtime from the space actually
    // available per visible neuron, so filtering to two disciplines lets the
    // survivors expand to fill the panel instead of huddling in one corner.
    //
    // U = sqrt(panel area / visible neurons) is the natural spacing. Distances
    // are fractions of U rather than fixed pixels, because a constant that
    // looks right for 46 neurons packs them against the walls at 15 and
    // strands them at 8. Intra-cluster distances take a smaller share of U than
    // inter-cluster ones: filtering should mostly push clusters apart, not
    // inflate each cluster until it stops reading as a cluster.
    const U_IN = 0.8; // intra-cluster spacing, as a fraction of U
    const U_SEP = 1.0; // inter-cluster spacing, as a fraction of U
    let spread = 1; // multiplier on cross-group distances
    let spreadIn = 1; // multiplier on intra-group distances
    const MAX_SPEED = 1.4;
    const GLIDE_MS = 1100; // how long a filter click overrides the physics
    const GLIDE_K = 0.14; // share of the remaining distance closed per frame
    let glideAt = -1e9;
    const DAMP = 0.9;

    const LABEL_FONT =
      '600 13px -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

    const ctx = canvas.getContext("2d");
    let dpr = 1;
    let W = 0;
    let H = 0;
    let nodes = [];
    let edges = [];
    let pulses = [];
    let adjacency = []; // node index -> Set of neighbour indices, for hover
    let active = null; // Set of group indices currently shown
    let vNodes = []; // visible node indices
    let vEdges = []; // edges with both endpoints visible
    let anchors = []; // group index -> {x, y} target for that cluster's centroid
    let gCentroid = []; // group index -> {x, y} where that cluster actually is
    let lastSig = null; // visible-group membership, to spot a real filter change
    let vGroups = []; // visible group indices, ascending
    let parallax = 0;
    let pointer = { x: -9999, y: -9999 };
    let hover = -1;
    let halo = "rgba(8,10,18,0.86)";
    let inkRGB = "238,241,248";
    let running = true;
    let raf = 0;

    function hexToRgb(hex) {
      let h = hex.replace("#", "").trim();
      if (h.length === 3) {
        h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
      }
      const n = parseInt(h, 16);
      if (!isFinite(n)) return [77, 141, 255];
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }

    function rgba(rgb, a) {
      return "rgba(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + "," + a + ")";
    }

    function mix(a, b, t) {
      return [
        Math.round(a[0] + (b[0] - a[0]) * t),
        Math.round(a[1] + (b[1] - a[1]) * t),
        Math.round(a[2] + (b[2] - a[2]) * t),
      ];
    }

    function randInt(n) {
      return Math.floor(Math.random() * n);
    }

    // Pull the spectrum + chrome colors out of CSS. Called on build and again
    // whenever the theme flips.
    function applyColors() {
      const pal = palette().map(hexToRgb);
      nodes.forEach(function (n) {
        n.rgb = pal[n.g % pal.length];
        n.css = rgba(n.rgb, 1);
      });
      halo = cssVar("--halo", "rgba(8,10,18,0.86)");
      inkRGB = root.getAttribute("data-theme") === "light" ? "18,20,28" : "238,241,248";
    }

    // Build nodes, then wire the synapses.
    function build() {
      ctx.font = LABEL_FONT;
      nodes = [];
      edges = [];

      const byGroup = [];
      groups.forEach(function (entry, gi) {
        const name = entry[0];
        const members = entry[1] || [];
        const idxs = [];
        members.forEach(function (label) {
          idxs.push(nodes.length);
          nodes.push({
            label: label,
            lc: label.toLowerCase(), // pre-lowered for the chat answer scan
            group: name,
            hw: ctx.measureText(label).width / 2, // label half-width, for wall padding
            g: gi,
            flash: 0, // 1 when the chatbot just named this skill, decays to 0
            rgb: [77, 141, 255],
            css: "#4d8dff",
            x: 0,
            y: 0,
            vx: 0,
            vy: 0,
            depth: 0.55 + Math.random() * 0.9, // parallax strength
            phase: Math.random() * TAU, // per-node breathing offset
            seeded: false,
          });
        });
        byGroup.push(idxs);
      });

      const seen = Object.create(null);
      // `rest` is per-edge, not per-kind. A chord that skips a neighbour has to
      // want a proportionally longer length, otherwise it acts as a drawstring
      // and collapses the ring it is drawn across.
      function link(i, j, rest, cross) {
        if (i === j) return;
        const key = i < j ? i + ":" + j : j + ":" + i;
        if (seen[key]) return;
        seen[key] = 1;
        // `rest` scales with the layout; `lab` is the hard floor set by how
        // much room the two labels need and never shrinks.
        edges.push({
          a: i,
          b: j,
          rest: rest,
          lab: nodes[i].hw + nodes[j].hw + 26,
          cross: !!cross,
        });
      }

      // Intra-group wiring: a ring through the members plus every-other-node
      // chords. Sparse enough to look like structure rather than a solid blob,
      // dense enough that a group can't fall apart.
      byGroup.forEach(function (idxs) {
        const n = idxs.length;
        if (n < 2) return;
        // Chord rest lengths follow the chord geometry of the ring the members
        // sit on, so ring and chords agree on one cluster size.
        const radius = REST_IN / (2 * Math.sin(Math.PI / Math.max(3, n)));
        function chordRest(skip) {
          return 2 * radius * Math.sin((Math.PI * skip) / n);
        }
        for (let k = 0; k < n; k++) {
          link(idxs[k], idxs[(k + 1) % n], REST_IN);
          if (n > 3) link(idxs[k], idxs[(k + 2) % n], chordRest(2));
        }
        // A couple of random chords per group for organic asymmetry.
        for (let k = 0; k < Math.max(1, Math.floor(n / 3)); k++) {
          const i = randInt(n);
          const j = randInt(n);
          const skip = Math.min(Math.abs(i - j), n - Math.abs(i - j));
          if (skip === 0) continue;
          link(idxs[i], idxs[j], chordRest(skip));
        }
      });

      // Cross-group wiring. First a ring over the groups (guarantees the whole
      // graph is connected), then random long-range shortcuts.
      const G = byGroup.length;
      if (G > 1) {
        for (let g = 0; g < G; g++) {
          const from = byGroup[g];
          const to = byGroup[(g + 1) % G];
          if (!from.length || !to.length) continue;
          const bridges = G === 2 ? 4 : 3;
          for (let k = 0; k < bridges; k++) {
            link(from[randInt(from.length)], to[randInt(to.length)], REST_CROSS, true);
          }
        }
        const shortcuts = Math.max(3, G);
        for (let k = 0; k < shortcuts; k++) {
          const ga = randInt(G);
          let gb = randInt(G);
          if (ga === gb) gb = (gb + 1) % G;
          const from = byGroup[ga];
          const to = byGroup[gb];
          if (!from.length || !to.length) continue;
          link(from[randInt(from.length)], to[randInt(to.length)], REST_CROSS, true);
        }
      }

      active = new Set(groupNames.map(function (_, i) {
        return i;
      }));
      applyColors();
      refreshVisible();
    }

    /* The quiet, mono flash on the cloud behind the panel, which is the only
       strength this page fires any more, and only from a discipline filter
       change in `refreshVisible`. The loud, chip-tinted one belonged to the old
       discipline legend and has had no caller since it went; `strong` and
       `tint` stay on the shared state because the cloud still reads them. */
    function flashCloud() {
      netFilter.changedAt = performance.now();
      netFilter.strong = false;
      netFilter.tint = null;
    }

    function vGroupCount() {
      return groupNames.filter(function (_, g) {
        return active.has(g);
      }).length;
    }

    // Recompute what is on screen after a filter change (or on first build):
    // the visible node/edge sets, the adjacency used by the hover highlight,
    // and the spread factor that sizes the layout to the survivors.
    function refreshVisible() {
      vNodes = [];
      for (let i = 0; i < nodes.length; i++) {
        if (active.has(nodes[i].g)) vNodes.push(i);
      }
      // `step` only writes entries for visible groups, so a hidden group's
      // centroid would otherwise stay frozen at wherever its cluster stood
      // when it was switched off. Clearing it is what lets the nebula tell
      // "hidden" apart from "not moving".
      for (let g = 0; g < gCentroid.length; g++) {
        if (!active.has(g)) gCentroid[g] = null;
      }
      vEdges = edges.filter(function (e) {
        return active.has(nodes[e.a].g) && active.has(nodes[e.b].g);
      });

      adjacency = nodes.map(function () {
        return new Set();
      });
      vEdges.forEach(function (e) {
        adjacency[e.a].add(e.b);
        adjacency[e.b].add(e.a);
      });

      // Fewer neurons on screen means each one can claim more room.
      const area = Math.max(1, W * H);
      const U = Math.sqrt(area / Math.max(1, vNodes.length));
      // One or two disciplines may open out much further than the full set:
      // at 1.45 a single cluster stayed a knot in the middle of an empty panel.
      const capIn = vGroupCount() <= 2 ? 2.6 : 1.45;
      spreadIn = Math.max(0.55, Math.min(capIn, (U_IN * U) / REST_IN));
      spread = Math.max(0.55, Math.min(1.6, (U_SEP * U) / SEP));

      // Re-anchor the survivors. Wedges are assigned by rank among the VISIBLE
      // groups, not by the group's index in profile.json, so filtering six
      // disciplines down to two moves those two onto opposite sides of the
      // panel instead of leaving them stranded in the corners they were seeded
      // in. One group left means the middle.
      vGroups = [];
      for (let g = 0; g < groupNames.length; g++) {
        if (active.has(g)) vGroups.push(g);
      }
      anchors = groupNames.map(function () {
        return null;
      });
      const cx = W / 2;
      const cy = H / 2;
      const rx = W * 0.3;
      const ry = H * 0.28;
      vGroups.forEach(function (g, rank) {
        if (vGroups.length === 1) {
          anchors[g] = { x: cx, y: cy };
          return;
        }
        // -PI/2 puts the first visible group at the top; two groups then land
        // top and bottom, which reads worse on a wide panel than left/right.
        const a = (rank / vGroups.length) * TAU + (vGroups.length === 2 ? 0 : -Math.PI / 2);
        anchors[g] = { x: cx + Math.cos(a) * rx, y: cy + Math.sin(a) * ry };
      });

      /* Publish to the cloud behind the panel. The flash is keyed off the
         membership actually changing, not off this function being called:
         `resize` calls it too, and a window drag should not read as somebody
         working a control. `lastSig` is what makes the resize path silent. */
      const sig = vGroups.join(",");
      if (sig !== lastSig) {
        if (lastSig !== null) flashCloud();
        lastSig = sig;
      }
      netFilter.n = vGroups.length;
      netFilter.total = groupNames.length;

      // Drop signals travelling on now-hidden synapses.
      pulses = pulses.filter(function (pl) {
        return active.has(nodes[pl.a].g) && active.has(nodes[pl.b].g);
      });
      hover = -1;
    }

    /* ------------------------------------------------------------------
       Nebula, bound to the clusters

       The soft layer behind the network used to be three blobs on fixed
       percentages drifting on CSS keyframes, which meant the background was the
       one part of this figure that did not react to the legend at all: filter
       five disciplines away and the lighting stayed exactly where it was.
       Now there is one blob per discipline and it rides that cluster's
       centroid, so the panel lights up under wherever the skills currently are
       and a deselected discipline takes its glow with it.

       Deliberately soft: blur is 52px and the lerp is slow, so this reads as
       the light catching up rather than as a fourth thing moving. It is
       background, and a background that tracks too crisply becomes a second
       figure competing with the first.

       No reduced-motion branch is needed here, and that is not an oversight:
       skillNet returns before `section.classList.add("animated")` under
       reduced motion, and the CSS hides `.net-nebula` until that class exists.
       No network, no nebula.
       ------------------------------------------------------------------ */
    const nebula = (function () {
      const host = section.querySelector(".net-nebula");
      if (!host) return { sync: function () {}, measure: function () {} };

      // One blob per discipline, built here rather than in the template: the
      // count has to equal groupNames.length or the binding is off by one, and
      // that is a fact this file owns.
      host.innerHTML = "";
      host.classList.add("is-bound");
      const blobs = groupNames.map(function (_, i) {
        const el = document.createElement("span");
        host.appendChild(el);
        return { el: el, x: 0, y: 0, a: 0, s: 1, seeded: false };
      });

      // Canvas-space -> nebula-space, in pixels. The nebula is inset -28%, so
      // its box is bigger than the panel and its origin is outside it; without
      // this offset every blob would sit up and to the left of its cluster.
      let offX = 0;
      let offY = 0;
      function measure() {
        const c = canvas.getBoundingClientRect();
        const n = host.getBoundingClientRect();
        offX = c.left - n.left;
        offY = c.top - n.top;
      }

      function sync(time) {
        const t = time / 1000;
        // Fewer disciplines on screen means fewer blobs, so each one grows and
        // brightens to keep the panel from going flat when the legend is down
        // to one chip. Total light stays roughly constant.
        // The constants are a floor plus a per-blob share. The floor is what
        // keeps the all-six case from washing out: six blobs each carrying a
        // sixth of the light left the panel visibly flatter than the three
        // fixed blobs this replaced, because those three were each at full
        // strength inside the container's --nebula-a.
        const n = Math.max(1, vGroups.length);
        const scale = 0.85 + 1.0 / n;
        const alpha = Math.min(1, 0.55 + 1.1 / n);

        for (let i = 0; i < blobs.length; i++) {
          const b = blobs[i];
          const c = gCentroid[i];
          const on = active.has(i) && !!c;
          if (on) {
            // Glide, except while invisible: on first placement, and whenever
            // a discipline is switched back on, the blob is at zero alpha and
            // somewhere stale, so it jumps to the right place for free. Without
            // this every blob slides in from the panel's top-left corner on
            // load, and a re-selected one visibly travels across the panel.
            const k = b.seeded && b.a > 0.02 ? 0.045 : 1;
            b.x += (offX + c.x - b.x) * k;
            b.y += (offY + c.y - b.y) * k;
            b.seeded = true;
          }
          b.a += ((on ? alpha : 0) - b.a) * 0.06;
          b.s += ((on ? scale : scale * 0.7) - b.s) * 0.05;
          if (b.a < 0.004) {
            b.el.style.opacity = "0";
            continue;
          }
          const ph = i * 1.7;
          const dx = Math.sin(t * 0.21 + ph) * 26;
          const dy = Math.cos(t * 0.17 + ph * 1.3) * 20;
          b.el.style.opacity = b.a.toFixed(3);
          b.el.style.transform =
            "translate3d(" +
            (b.x + dx).toFixed(1) +
            "px," +
            (b.y + dy).toFixed(1) +
            "px,0) translate(-50%,-50%) scale(" +
            b.s.toFixed(3) +
            ")";
        }
      }

      return { sync: sync, measure: measure };
    })();

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      W = rect.width;
      H = rect.height;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.font = LABEL_FONT;

      // Seed each group in its own wedge around the canvas centre, so the
      // network settles into a readable ring of clusters instead of untangling
      // from a random knot.
      // Ellipse rather than a circle: the panel is much wider than it is tall,
      // so seeding on a circle wastes the sides and crowds the middle.
      const cx = W / 2;
      const cy = H / 2;
      const rx = W * 0.34;
      const ry = H * 0.34;
      nodes.forEach(function (n) {
        const padX = n.hw + 8;
        if (!n.seeded) {
          const a = (n.g / groupNames.length) * TAU + (Math.random() - 0.5) * 0.6;
          const jitter = 0.82 + Math.random() * 0.36;
          n.x = cx + Math.cos(a) * rx * jitter;
          n.y = cy + Math.sin(a) * ry * jitter;
          n.seeded = true;
        }
        n.x = Math.max(padX, Math.min(W - padX, n.x));
        n.y = Math.max(TOP, Math.min(H - BOT, n.y));
      });
      nebula.measure(); // the offset is in pixels, so it is resize-dependent
      if (active) refreshVisible(); // spread depends on the measured panel size
    }

    function updateParallax() {
      const rect = canvas.getBoundingClientRect();
      const vh = window.innerHeight || 1;
      // 0 as the section reaches the viewport bottom, 1 as it leaves the top.
      const p = (vh - rect.top) / (vh + rect.height);
      const target = (Math.max(0, Math.min(1, p)) - 0.5) * 64; // ~±32px
      parallax += (target - parallax) * 0.08;
    }

    const TOP = 26; // room for the label drawn above the dot
    const BOT = 14; // room for the dot itself

    // Fire a signal down a random synapse. Cross-group synapses fire more often
    // than their share, because those are the interesting ones to watch.
    function spawnPulse() {
      if (!vEdges.length || pulses.length > 30) return;
      let e = vEdges[randInt(vEdges.length)];
      if (!e.cross && Math.random() < 0.45) {
        const crossEdges = vEdges.filter(function (x) {
          return x.cross;
        });
        if (crossEdges.length) e = crossEdges[randInt(crossEdges.length)];
      }
      const forward = Math.random() < 0.5;
      pulses.push({
        a: forward ? e.a : e.b,
        b: forward ? e.b : e.a,
        t: 0,
        speed: 0.006 + Math.random() * 0.009,
      });
    }

    function step() {
      // Slide each visible cluster toward its anchor wedge. The force is
      // computed once from the cluster's centroid and applied uniformly to its
      // members, so the cluster translates as a body and its internal shape is
      // left to the springs.
      for (let k = 0; k < vGroups.length; k++) {
        const g = vGroups[k];
        const anchor = anchors[g];
        if (!anchor) continue;
        let sx = 0;
        let sy = 0;
        let count = 0;
        for (let vi = 0; vi < vNodes.length; vi++) {
          const n = nodes[vNodes[vi]];
          if (n.g !== g) continue;
          sx += n.x;
          sy += n.y;
          count++;
        }
        if (!count) continue;
        // The centroid is already being computed here for the anchor force, so
        // the nebula rides along on it rather than walking the node list a
        // second time. It follows where the cluster *is*, not where it is being
        // pulled: an anchor is a fixed wedge, so binding to it would leave the
        // glow parked while the cluster was still travelling toward it.
        gCentroid[g] = { x: sx / count, y: sy / count };
        const fx = (anchor.x - sx / count) * K_ANCHOR;
        const fy = (anchor.y - sy / count) * K_ANCHOR;
        for (let vi = 0; vi < vNodes.length; vi++) {
          const n = nodes[vNodes[vi]];
          if (n.g !== g) continue;
          n.vx += fx;
          n.vy += fy;
        }
      }

      // Pairwise: universal short-range repulsion.
      const repel = REPEL * spreadIn;
      const sep = SEP * spread;
      for (let vi = 0; vi < vNodes.length; vi++) {
        const i = vNodes[vi];
        const a = nodes[i];
        for (let vj = vi + 1; vj < vNodes.length; vj++) {
          const j = vNodes[vj];
          const b = nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;

          // Label-aware separation. Labels are wide and sit on one horizontal
          // band each, so two neurons at a similar height need their label
          // half-widths of clearance, not just node clearance. Without this the
          // long names ("Feature Engineering", "Image Classification") pile up.
          if (Math.abs(dy) < LABEL_H) {
            const need = a.hw + b.hw + 14;
            const ax = Math.abs(dx);
            if (ax < need) {
              const push = ((need - ax) / need) * K_LABEL;
              const dir = dx === 0 ? (i % 2 ? 1 : -1) : dx > 0 ? 1 : -1;
              a.vx += dir * push;
              b.vx -= dir * push;
            }
          }

          // Neurons from different disciplines claim a wider berth than
          // neurons within one, which is what keeps clusters legible as
          // clusters. This replaces the old job of the cross-group springs.
          const range = a.g === b.g ? repel : sep;
          const k = a.g === b.g ? K_REPEL : K_SEP;
          const d2 = dx * dx + dy * dy;
          if (d2 > range * range || d2 < 0.0001) continue;
          const d = Math.sqrt(d2);
          const f = ((range - d) / range) * k;
          const ux = dx / d;
          const uy = dy / d;
          a.vx += ux * f;
          a.vy += uy * f;
          b.vx -= ux * f;
          b.vy -= uy * f;
        }
      }

      // Springs along every synapse.
      for (let k = 0; k < vEdges.length; k++) {
        const e = vEdges[k];
        const a = nodes[e.a];
        const b = nodes[e.b];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        // Label clearance is a floor the layout scale can never undercut.
        const rest = Math.max(e.rest * (e.cross ? spread : spreadIn), e.lab);
        // A cross-group link is a leash, not a spring: it reels the network
        // back in when two disciplines drift apart, but never pulls when
        // they're already close. As a two-way spring it quietly crushed every
        // cluster to about two thirds of its intended size.
        if (e.cross && d <= rest) continue;
        const f = (d - rest) * (e.cross ? K_CROSS : K_IN);
        const ux = dx / d;
        const uy = dy / d;
        a.vx -= ux * f;
        a.vy -= uy * f;
        b.vx += ux * f;
        b.vy += uy * f;
      }

      // Integrate, damp, cap, and keep everything inside the panel.
      for (let vi = 0; vi < vNodes.length; vi++) {
        const n = nodes[vNodes[vi]];
        const padX = n.hw + 8;
        if (n.flash > 0.001) n.flash *= 0.988; // ~4s for a mention to fade out
        else n.flash = 0;
        n.vx += (Math.random() - 0.5) * 0.045; // faint thermal jitter
        n.vy += (Math.random() - 0.5) * 0.045;
        n.vx *= DAMP;
        n.vy *= DAMP;
        // A filter click glides each survivor to its target (see glideTo),
        // bleeding off velocity so the springs do not fight the glide.
        if (n.tx != null && performance.now() - glideAt < GLIDE_MS) {
          n.x += (n.tx - n.x) * GLIDE_K;
          n.y += (n.ty - n.y) * GLIDE_K;
          n.vx *= 0.5;
          n.vy *= 0.5;
        }
        const sp = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
        if (sp > MAX_SPEED) {
          n.vx = (n.vx / sp) * MAX_SPEED;
          n.vy = (n.vy / sp) * MAX_SPEED;
        }
        n.x += n.vx;
        n.y += n.vy;
        if (n.x < padX && n.vx < 0) n.vx *= -0.6;
        if (n.x > W - padX && n.vx > 0) n.vx *= -0.6;
        if (n.y < TOP && n.vy < 0) n.vy *= -0.6;
        if (n.y > H - BOT && n.vy > 0) n.vy *= -0.6;
        n.x = Math.max(padX, Math.min(W - padX, n.x));
        n.y = Math.max(TOP, Math.min(H - BOT, n.y));

        // Render position: physics position + scroll parallax + pointer push.
        let rx = n.x;
        let ry = n.y + parallax * n.depth;
        const dx = rx - pointer.x;
        const dy = ry - pointer.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 11000 && d2 > 0.01) {
          const f = (11000 - d2) / 11000;
          const d = Math.sqrt(d2);
          rx += (dx / d) * f * 12;
          ry += (dy / d) * f * 12;
        }
        n.rx = Math.max(n.hw + 6, Math.min(W - n.hw - 6, rx));
        n.ry = Math.max(TOP, Math.min(H - BOT, ry));
      }

      // Nearest neuron to the pointer becomes the hover focus.
      hover = -1;
      let best = 30 * 30;
      for (let vi = 0; vi < vNodes.length; vi++) {
        const i = vNodes[vi];
        const n = nodes[i];
        const dx = n.rx - pointer.x;
        const dy = n.ry - pointer.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < best) {
          best = d2;
          hover = i;
        }
      }
    }

    function draw(time) {
      ctx.clearRect(0, 0, W, H);
      const focused = hover >= 0;
      const neighbours = focused ? adjacency[hover] : null;

      // --- synapses -------------------------------------------------------
      ctx.lineCap = "round";
      for (let k = 0; k < vEdges.length; k++) {
        const e = vEdges[k];
        const a = nodes[e.a];
        const b = nodes[e.b];
        const lit = focused && (e.a === hover || e.b === hover);
        const dim = focused && !lit;

        let alpha = e.cross ? 0.46 : 0.42;
        let width = e.cross ? 1.5 : 1.15;
        if (lit) {
          alpha = 0.95;
          width = 2.4;
        } else if (dim) {
          alpha *= 0.18;
        }

        if (e.cross || lit) {
          // Two-color gradient: the link visibly belongs to both disciplines.
          const grad = ctx.createLinearGradient(a.rx, a.ry, b.rx, b.ry);
          grad.addColorStop(0, rgba(a.rgb, alpha));
          grad.addColorStop(1, rgba(b.rgb, alpha));
          ctx.strokeStyle = grad;
        } else {
          ctx.strokeStyle = rgba(a.rgb, alpha);
        }
        ctx.lineWidth = width;
        ctx.beginPath();
        ctx.moveTo(a.rx, a.ry);
        ctx.lineTo(b.rx, b.ry);
        ctx.stroke();
      }

      // --- travelling signals ---------------------------------------------
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      for (let i = pulses.length - 1; i >= 0; i--) {
        const p = pulses[i];
        p.t += p.speed;
        if (p.t >= 1) {
          pulses.splice(i, 1);
          continue;
        }
        const a = nodes[p.a];
        const b = nodes[p.b];
        const c = mix(a.rgb, b.rgb, p.t);
        // Fade in and out at the ends so signals don't pop.
        const life = Math.sin(p.t * Math.PI);
        const x = a.rx + (b.rx - a.rx) * p.t;
        const y = a.ry + (b.ry - a.ry) * p.t;

        // Short comet tail back along the synapse.
        const tt = Math.max(0, p.t - 0.14);
        const tx = a.rx + (b.rx - a.rx) * tt;
        const ty = a.ry + (b.ry - a.ry) * tt;
        const grad = ctx.createLinearGradient(tx, ty, x, y);
        grad.addColorStop(0, rgba(c, 0));
        grad.addColorStop(1, rgba(c, 0.7 * life));
        ctx.strokeStyle = grad;
        ctx.lineWidth = 2.4;
        ctx.beginPath();
        ctx.moveTo(tx, ty);
        ctx.lineTo(x, y);
        ctx.stroke();

        ctx.beginPath();
        ctx.arc(x, y, 2.6, 0, TAU);
        ctx.fillStyle = rgba(c, 0.95 * life);
        ctx.fill();
      }
      ctx.restore();

      // --- neurons ---------------------------------------------------------
      for (let vi = 0; vi < vNodes.length; vi++) {
        const i = vNodes[vi];
        const n = nodes[i];
        // A skill the chatbot just named stays lit even while another neuron is
        // hovered, otherwise the effect vanishes the moment you move the mouse.
        const named = n.flash > 0.02;
        const lit = named || (focused && (i === hover || neighbours.has(i)));
        const dim = focused && !lit;
        // Slow breathing so idle nodes never look frozen.
        const breathe = 1 + Math.sin(time * 0.0016 + n.phase) * 0.16;
        const r = (i === hover ? 6.4 : 4) * breathe + n.flash * 3.6;

        ctx.save();
        ctx.globalAlpha = dim ? 0.22 : 1;
        ctx.shadowColor = rgba(n.rgb, 0.9);
        ctx.shadowBlur = (i === hover ? 22 : 10) + n.flash * 26;
        ctx.beginPath();
        ctx.arc(n.rx, n.ry, r, 0, TAU);
        ctx.fillStyle = n.css;
        ctx.fill();
        ctx.restore();

        // Halo ring around the focused neuron.
        if (i === hover) {
          ctx.beginPath();
          ctx.arc(n.rx, n.ry, r + 7, 0, TAU);
          ctx.strokeStyle = rgba(n.rgb, 0.45);
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }

        // Expanding ring on a freshly named skill: it reads as the mention
        // arriving, and it expands as the flash decays so the pulse travels
        // outward rather than shrinking in.
        if (named) {
          ctx.beginPath();
          ctx.arc(n.rx, n.ry, r + 6 + (1 - n.flash) * 26, 0, TAU);
          ctx.strokeStyle = rgba(n.rgb, 0.5 * n.flash);
          ctx.lineWidth = 2;
          ctx.stroke();
        }
      }

      // --- labels ----------------------------------------------------------
      // The physics keeps labels mostly clear, but a drifting network will
      // always produce the occasional collision. Place greedily: try above the
      // neuron, fall back to below it, and if both are taken, fade the label
      // rather than stack unreadable text. Hovered neurons are placed first so
      // the label you are actually reading always wins its slot.
      ctx.font = LABEL_FONT;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineJoin = "round";

      const placed = [];
      function free(x, y, hw) {
        for (let k = 0; k < placed.length; k++) {
          const r = placed[k];
          if (Math.abs(y - r.y) < 15 && Math.abs(x - r.x) < hw + r.hw + 6) return false;
        }
        return true;
      }

      // Priority: the hovered neuron, then skills the chatbot just named, then
      // the hovered neuron's neighbours, then everything else. A named skill has
      // to win its slot or the whole chat-to-network effect can be silently
      // eaten by a label collision.
      function rank(i) {
        if (i === hover) return 0;
        if (nodes[i].flash > 0.02) return 1;
        if (neighbours && neighbours.has(i)) return 2;
        return 3;
      }
      const order = vNodes.slice();
      order.sort(function (p, q) {
        return rank(p) - rank(q);
      });

      for (let k = 0; k < order.length; k++) {
        const i = order[k];
        const n = nodes[i];
        const named = n.flash > 0.02;
        const lit = named || (focused && (i === hover || neighbours.has(i)));
        const dim = focused && !lit;

        /* Labels are drawn at a fixed 13px at any width, so a smaller box does
           not shrink them, it collides them: a phone gets all 46 at once in a
           353px box. Rendered at 390px it is a knot of half-faded names over
           each other, and a phone is what most of this site's readers are on.

           So in a small box nothing is named by default. What survives is what
           was asked for: the hovered neuron, which a touchscreen has no way to
           ask for anyway, and a skill the chatbot just said, which on a phone
           becomes the only label on the panel and reads as the network answering.
           Nothing is lost by it. Each step of the walk above names every skill
           its engagement used, in body text, which is the same reason the arc was
           allowed to shrink to 560 and put its own labels under the legibility
           note.

           420 rather than the 1280 the walk uses, because this is about how much
           room 46 names need and not about which layout is in play. The knob it
           actually belongs to is LABEL_H, the band height at which labels start
           to collide; that is per-label and this is per-panel. */
        if (W < 420 && i !== hover && !named) continue;

        let ly = n.ry - 15;
        let alpha = dim ? 0.25 : 1;
        if (!free(n.rx, ly, n.hw)) {
          ly = n.ry + 17;
          if (!free(n.rx, ly, n.hw)) {
            ly = n.ry - 15;
            alpha *= 0.5; // crowded: keep it faint instead of illegible
          }
        }
        placed.push({ x: n.rx, y: ly, hw: n.hw });

        ctx.globalAlpha = alpha;
        ctx.lineWidth = 4;
        ctx.strokeStyle = halo;
        ctx.strokeText(n.label, n.rx, ly);
        // The focused neuron and its neighbours take their own group color;
        // everything else stays neutral ink so the highlight reads instantly.
        ctx.fillStyle = lit ? n.css : "rgba(" + inkRGB + ",0.86)";
        ctx.fillText(n.label, n.rx, ly);
        ctx.globalAlpha = 1;
      }

      // Group name under the focused neuron, so hovering also tells you which
      // discipline the skill belongs to.
      if (focused) {
        const n = nodes[hover];
        ctx.font = '500 11px ' + "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif";
        ctx.lineWidth = 4;
        ctx.strokeStyle = halo;
        ctx.strokeText(n.group, n.rx, n.ry + 18);
        ctx.fillStyle = rgba(n.rgb, 0.8);
        ctx.fillText(n.group, n.rx, n.ry + 18);
      }
    }

    /* Hand the cloud behind this panel each visible cluster's centroid, in
       -1..1 across the canvas. Same values the nebula rides, published rather
       than recomputed: `step` already derives them for the anchor force.

       Normalized rather than in pixels because the cloud is a perspective 3D
       scene with its own units and its own canvas size, so pixels would mean
       nothing to it. A hidden discipline publishes null instead of a stale
       point: the cloud has to be able to tell "not on screen" from "at the
       origin", and the previous value would leave a lobe parked wherever that
       cluster stood when it was switched off. */
    function publishCentroids() {
      if (!W || !H) return;
      const out = netFilter.centroids;
      for (let g = 0; g < groupNames.length; g++) {
        const c = active.has(g) ? gCentroid[g] : null;
        out[g] = c ? { x: (c.x / W) * 2 - 1, y: (c.y / H) * 2 - 1 } : null;
      }
    }

    let lastSpawn = 0;
    function frame(time) {
      if (running) {
        updateParallax();
        step();
        if (time - lastSpawn > 150) {
          spawnPulse();
          lastSpawn = time;
        }
        draw(time);
        nebula.sync(time);
        publishCentroids();
      }
      raf = requestAnimationFrame(frame);
    }

    canvas.addEventListener("pointermove", function (e) {
      const rect = canvas.getBoundingClientRect();
      pointer.x = e.clientX - rect.left;
      pointer.y = e.clientY - rect.top;
    });
    canvas.addEventListener("pointerleave", function () {
      pointer.x = -9999;
      pointer.y = -9999;
    });

    let resizeTimer = 0;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(resize, 150);
    });

    // Don't burn frames while the network is off screen or the tab is hidden.
    // Both conditions are tracked separately so either one clearing resumes it.
    let onScreen = true;
    function syncRunning() {
      running = onScreen && !document.hidden;
    }
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(
        function (entries) {
          onScreen = entries[0].isIntersecting;
          syncRunning();
        },
        { threshold: 0 }
      ).observe(canvas);
    }
    document.addEventListener("visibilitychange", syncRunning);

    // ---- chat bridge -------------------------------------------------------
    // When the assistant names a skill, that neuron lights up and fires signals
    // down its synapses. `scan` is called on every streamed token with the whole
    // answer so far, so the network reacts word by word as the answer is
    // written; `flashed` makes each skill fire once per answer rather than once
    // per token.
    let flashed = new Set();

    // Word-boundary match done by hand rather than with a RegExp: skill labels
    // contain regex metacharacters ("C++"), and \b does not do what you want
    // next to a '+' anyway.
    function mentions(hay, needle) {
      let from = 0;
      for (;;) {
        const at = hay.indexOf(needle, from);
        if (at < 0) return false;
        const before = at > 0 ? hay[at - 1] : " ";
        const afterAt = at + needle.length;
        const after = afterAt < hay.length ? hay[afterAt] : " ";
        if (!/[a-z0-9]/.test(before) && !/[a-z0-9]/.test(after)) return true;
        from = at + 1;
      }
    }

    function flashNode(i) {
      nodes[i].flash = 1;
      // Fire a couple of signals outward so the mention propagates through the
      // network instead of just blinking one dot.
      const outs = [];
      for (let k = 0; k < vEdges.length; k++) {
        const e = vEdges[k];
        if (e.a === i) outs.push(e.b);
        else if (e.b === i) outs.push(e.a);
      }
      for (let k = 0; k < Math.min(3, outs.length); k++) {
        pulses.push({
          a: i,
          b: outs[randInt(outs.length)],
          t: 0,
          speed: 0.012 + Math.random() * 0.008,
        });
      }
    }

    /* ---- discipline filter --------------------------------------------
       One discipline at a time, or all of them. Back since the network left the
       walk: a filter under a sticky figure was a control that ended the walk,
       and this panel no longer sticks.

       The anchor force alone took several seconds to drag a cluster from
       wherever it sat in the full layout to the centre, and it arrived at its
       full-layout size, a small knot of overlapping labels in a large panel.
       glideTo sets a target per node instead: the cluster's shape, moved onto
       its anchor and scaled to fill its share of the panel. The step loop
       closes on it for GLIDE_MS and then hands back to the physics, which holds
       the shape because the spread caps below allow it. */
    function glideTo() {
      vGroups.forEach(function (g) {
        const a = anchors[g];
        const mem = vNodes.filter(function (i) {
          return nodes[i].g === g;
        });
        if (!a || !mem.length) return;
        let cx = 0;
        let cy = 0;
        mem.forEach(function (i) {
          cx += nodes[i].x;
          cy += nodes[i].y;
        });
        cx /= mem.length;
        cy /= mem.length;
        let rx = 1;
        let ry = 1;
        mem.forEach(function (i) {
          rx = Math.max(rx, Math.abs(nodes[i].x - cx) + nodes[i].hw);
          ry = Math.max(ry, Math.abs(nodes[i].y - cy));
        });
        const share = 0.42 / Math.sqrt(vGroups.length);
        const k = Math.max(1, Math.min((W * share) / rx, (H * share) / ry, 3));
        mem.forEach(function (i) {
          const n = nodes[i];
          n.tx = a.x + (n.x - cx) * k;
          n.ty = a.y + (n.y - cy) * k;
        });
      });
      glideAt = performance.now();
    }

    const legend = document.getElementById("skill-legend");
    if (legend) {
      const buttons = Array.prototype.slice.call(legend.querySelectorAll(".legend-btn"));
      const sync = function () {
        buttons.forEach(function (btn) {
          const key = btn.getAttribute("data-group");
          const on = key === "all" ? active.size === groupNames.length : active.has(Number(key));
          btn.setAttribute("aria-pressed", on ? "true" : "false");
        });
      };
      legend.addEventListener("click", function (e) {
        const btn = e.target.closest(".legend-btn");
        // `active` is null until the first layout pass.
        if (!btn || !active) return;
        const key = btn.getAttribute("data-group");
        active.clear();
        if (key === "all") {
          groupNames.forEach(function (_, i) {
            active.add(i);
          });
        } else {
          active.add(Number(key));
        }
        refreshVisible();
        glideTo();
        sync();
      });
    }

    skillNetBridge = {
      reset: function () {
        flashed = new Set();
      },
      scan: function (text) {
        if (!text) return;
        const hay = text.toLowerCase();
        for (let i = 0; i < nodes.length; i++) {
          if (flashed.has(i)) continue;
          if (mentions(hay, nodes[i].lc)) {
            flashed.add(i);
            flashNode(i);
          }
        }
      },
    };

    onThemeChange(applyColors);

    build();
    resize();
    raf = requestAnimationFrame(frame);
  })();

  /* ======================================================================
     Career arc

     The figure the Skills in Context walk is told through. `walk(cur, seen,
     intro)` is the whole public surface, and the walk driver below is its only
     caller. A step is one engagement, and the state it sets is built to be read
     at a glance rather than studied:

       * the live engagement (`cur`) keeps its band, its whole fan of chords and
         its name, and its skills are labelled at full strength;
       * engagements already walked past (`seen`) keep their band, dimmed, with
         no chords, and their skills keep a faint label. Scrolling down builds
         the career up on the ring rather than replacing one fan with another;
       * everything not yet reached is a dot with no label. The ring at full
         density, 46 rotated labels at once, was what no reader could get past;
       * `intro` adds the three callouts drawn into the SVG, on the first
         engagement, for the step that teaches the encoding.

     cur null is the all-on state, where the figure rests either side of the
     walk and everywhere the walk is not built at all.

     The join is by index. tools/build_career_arc.py writes data-eng onto every
     segment, chord and name block, and the space separated list of engagements
     that reached it onto every skill dot and label; the walk's steps carry the
     same profile.json index. Nothing here parses the SVG geometry, so this
     survives any change to the layout.
     ====================================================================== */
  (function careerArc() {
    const svg = document.querySelector(".career-arc");
    if (!svg) return;

    const marks = Array.prototype.slice.call(svg.querySelectorAll(".arc[data-eng], .seg[data-eng]"));
    const skills = Array.prototype.slice.call(
      svg.querySelectorAll(".dot[data-eng], .skill-label[data-eng]")
    );
    const names = Array.prototype.slice.call(svg.querySelectorAll(".seg-label[data-eng]"));
    if (!marks.length) return;

    // Parse the owner lists once. A skill with an empty data-eng is owned by
    // nobody, so the walk never lights it.
    const owners = skills.map(function (el) {
      const raw = (el.getAttribute("data-eng") || "").trim();
      return raw ? raw.split(/\s+/).map(Number) : [];
    });

    function walk(cur, seen, intro) {
      const on = cur != null;
      svg.classList.toggle("is-walk", on);
      svg.classList.toggle("is-intro", on && !!intro);

      marks.forEach(function (el) {
        const i = Number(el.getAttribute("data-eng"));
        // A walked-past engagement keeps its band but not its chords: two fans
        // from one rim cross each other, and that crossing is what the walk
        // exists to take away.
        const past = on && i !== cur && el.classList.contains("seg") && seen.has(i);
        el.classList.toggle("is-off", on && i !== cur && !past);
        el.classList.toggle("is-past", past);
      });

      skills.forEach(function (el, k) {
        const o = owners[k];
        const isCur = on && o.indexOf(cur) >= 0;
        const isPast =
          on &&
          !isCur &&
          o.some(function (e) {
            return seen.has(e);
          });
        el.classList.toggle("is-cur", isCur);
        el.classList.toggle("is-past", isPast);
        el.classList.toggle("is-dim", on && !isCur && !isPast);
      });

      names.forEach(function (el) {
        el.classList.toggle("is-cur", on && Number(el.getAttribute("data-eng")) === cur);
      });
    }

    careerArcBridge = { walk: walk };
    walk(null, new Set(), false);
  })();

  /* ======================================================================
     Skills in Context — the one walk on this page

     A step is one engagement, in profile.json order, which is start date
     order: the arc isolates it and keeps everything before it as context. The
     first step is the same engagement with the reading guide switched on, and
     the last is the whole figure. The network used to be driven by this walk
     too; it has its own section and its own filter now.
     ====================================================================== */
  (function skillsWalk() {
    const walkEl = document.querySelector(".skills-walk");
    const section = walkEl && walkEl.closest("section");
    if (!section) return;
    const stepEls = Array.prototype.slice.call(section.querySelectorAll(".walk-step"));
    if (!stepEls.length) return;

    // An empty data-eng is the all-on step.
    const steps = stepEls.map(function (el) {
      const raw = el.getAttribute("data-eng");
      return {
        eng: raw === "" || raw == null ? null : Number(raw),
        intro: el.hasAttribute("data-intro"),
      };
    });

    function apply(step) {
      if (!careerArcBridge) return;
      if (!step || step.eng == null) {
        careerArcBridge.walk(null, new Set(), false);
        return;
      }
      // Everything walked past, in walk order. The intro is not a step of the
      // career, so it adds nothing to what has been seen.
      const seen = new Set();
      for (let k = 0; k < steps.length && steps[k] !== step; k++) {
        if (!steps[k].intro && steps[k].eng != null) seen.add(steps[k].eng);
      }
      careerArcBridge.walk(step.eng, seen, step.intro);
    }

    // No fallback driver. Below the walk's breakpoint, under reduced motion and
    // with scripting off, the arc rests whole and the steps are a plain list
    // under it.
    scrollWalk({ root: section, steps: steps, apply: apply });
  })();

  /* ======================================================================
     `?ask=<slug>` deep link. The server has already resolved the slug and
     rendered the question into the composer (see index() in app/main.py), so
     the question is on screen with scripting off too. All that is left is to
     put it in front of the visitor and leave the cursor where they can edit it.

     Not sent automatically, and that is the whole design. Firing a request on
     load would let a crawler, a preview fetch, or one impatient reload spend
     from a budget of roughly 33 questions a day, and it would answer a question
     the visitor never chose to ask.

     Reduced motion gets the same jump without the travel: the destination
     matters, the scrolling does not.
     ====================================================================== */
  if (input && input.dataset.prefilled) {
    const chatSection = form.closest("section") || form;
    // Deferred a frame: the scroll reveal starts every section at opacity 0 and
    // only adds .in once its observer has run, so scrolling during this pass
    // aims at a section that has not been laid out yet.
    requestAnimationFrame(function () {
      chatSection.scrollIntoView({
        behavior: reduceMotion.matches ? "auto" : "smooth",
        block: "center",
      });
      // Caret to the end rather than selecting the text: the visitor is most
      // likely to append to this question, and a select-all means their first
      // keystroke silently deletes the thing the link was for.
      input.focus({ preventScroll: true });
      input.setSelectionRange(input.value.length, input.value.length);
    });
  }

  /* ====================================================================== */
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    const q = input.value.trim();
    if (q) ask(q);
  });

  if (suggestions) {
    suggestions.addEventListener("click", function (e) {
      const btn = e.target.closest(".chip");
      if (!btn) return;
      ask(btn.textContent.trim());
    });
  }

  /* ======================================================================
     Contact form — posts to /api/contact, which relays it by email.

     Nothing here touches the chat: a message never passes through the
     assistant. Only the "email or phone" rule is checked in the browser, since
     it is the one the fields' own attributes cannot say; everything else is the
     server's, whose refusals are fixed sentences and are shown as they come.
     ====================================================================== */
  const contactForm = document.getElementById("contact-form");
  if (contactForm) {
    const contactStatus = document.getElementById("contact-status");
    const contactSend = document.getElementById("contact-send");

    function contactSay(text, isError) {
      contactStatus.textContent = text;
      contactStatus.classList.toggle("is-error", !!isError);
    }

    contactForm.addEventListener("submit", async function (e) {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(contactForm));
      if (!data.email.trim() && !data.phone.trim()) {
        contactSay("Please leave an email address or a phone number to reply to.", true);
        return;
      }

      contactSend.disabled = true;
      contactSay("Sending…", false);
      try {
        const res = await fetch("/api/contact", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
        if (res.ok) {
          contactForm.reset();
          contactSay("Thanks, your message was sent.", false);
          return;
        }
        const body = await res.json().catch(function () { return {}; });
        const detail = typeof body.detail === "string" ? body.detail : "";
        contactSay(detail || "Your message could not be sent right now. Please try again later.", true);
      } catch (err) {
        contactSay("Your message could not be sent right now. Please try again later.", true);
      } finally {
        contactSend.disabled = false;
      }
    });
  }
})();

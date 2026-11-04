# Plan 4: JASMINE (Pitch Deck and Presentation in HTML, ~5%)

**Mission:** Build a cinematic, **offline**, animated HTML pitch deck that tells the HydroPulse story in about 3 minutes and hands off cleanly to the live demo. Also write the spoken script and a Q&A defense sheet, and record a backup demo video.

## Owned files
`pitch_and_roi/deck/index.html` · `pitch_and_roi/deck/styles.css` · `pitch_and_roi/deck/deck.js` · `pitch_and_roi/deck/assets/` · `pitch_and_roi/pitch_script.md` · `pitch_and_roi/qa_defense.md` · `pitch_and_roi/architecture.mmd` · `pitch_and_roi/sources.md`

## Phases

| # | Window | Deliverable | Done when |
|---|---|---|---|
| 1 | H0–H1 | Story outline + `sources.md` with verified stats | Every number has a source or a `[VERIFY]` tag |
| 2 | H1–H4 | Deck engine: navigation, scaling, animation system, notes | Arrow keys/clicker work. 16:9 fits any screen |
| 3 | H4–H9 | Slides 1–8 with animations | Story flows up to the demo handoff |
| 4 | H9–H13 | ROI calculator, architecture, roadmap, team, backup slides | All slides done except the results slide |
| — | **H13** | **CP2: get `backend/benchmark_report.md` from Hardhik, screenshots from Badri** | Results slide filled with **real** numbers |
| 5 | H13–H17 | `pitch_script.md` (2-min + 3-min), `qa_defense.md`, PDF export | Script timed out loud |
| 6 | H16–H18 | Record a 60–90 s backup demo video (screen capture) | Saved and linked from the demo slide |
| 7 | H18–H24 | Rehearse 3× with a timer and the team | Pitch lands under the time limit |

## Handoffs
- **Needs from Hardhik (H13):** benchmark numbers (top-1/top-3 accuracy, mean error in metres, latency, network size).
- **Needs from Badri (H14):** 3 screenshots from `frontend/screenshots/`.
- **Needs from the team:** final names and roles, and the official pitch time limit from the organizers.

> [!CAUTION]
> Don't put the dossier's "94.2% confidence" figure on a slide as "accuracy". Only quote the benchmark report. Judges at engineering colleges will ask how you measured it.

---

## 🎯 Goal Prompt (paste into your agent)

````text
ROLE
You are a world-class pitch designer and front-end motion designer. You're building the hackathon pitch for
"HydroPulse" at Lakshya 2026 (SJB Institute of Technology, Kengeri, Bengaluru), Track 02: Smart and Sustainable
Future. Team: Hardhik (lead backend & hydraulics engine), Gangu (backend pipeline & data), Badri (frontend & design),
Jasmine (pitch & product story).

WHAT HYDROPULSE IS (use this to write the content)
Indian city water boards like BWSSB lose a large share of treated water as Non-Revenue Water (NRW), much of it
through underground pipe leaks buried 1.5–2.5 m under roads. These leaks go unnoticed for days or weeks until roads
cave in or water surfaces. Finding them today means digging up roads, sending acoustic crews, or putting flowmeters
everywhere, all of which is slow and expensive. HydroPulse is a SOFTWARE-ONLY digital twin: (1) it turns real
OpenStreetMap streets of Kengeri into a pipe network, (2) simulates water physics with the US EPA's WNTR engine using
pressure-driven demand and Torricelli's leak equation, (3) uses graph betweenness centrality to choose just 5
"sentinel" junctions to watch, (4) when pressures drop, compares the 5-sensor "drop fingerprint" against a
pre-computed sensitivity matrix using cosine similarity, then verifies the top candidates by re-simulating them,
(5) pinpoints the leaking junction in under a second, estimates the loss in litres/minute and "BWSSB 6,000-litre
water tankers lost per day", and (6) recommends which valves to close to isolate the leak while keeping the rest
of the ward supplied. No hardware is needed for the demo. In the real world it would plug into a utility's
existing pressure loggers or SCADA.

GOAL
Create a stunning, self-contained, OFFLINE HTML presentation in `hydropulse/pitch_and_roi/deck/`
(index.html, styles.css, deck.js, assets/) plus the supporting docs listed below. You're done only when every
ACCEPTANCE CHECK passes in Chrome.

HARD CONSTRAINTS
- Zero external dependencies: no CDNs, no frameworks, no Google Fonts. Use vanilla HTML/CSS/JS, inline SVG, and a
  system font stack (or local .woff2 files in assets/). The venue Wi-Fi may fail.
- Opens by double-clicking index.html (file://). No build step, no server needed.
- 16:9 stage designed at 1920×1080, scaled with CSS transform to fit any screen, letterboxed on black.
- Respect prefers-reduced-motion (swap motion for simple fades).

DECK ENGINE (deck.js)
- Navigation: →/Space/PageDown/click = next, ←/PageUp = previous (presentation clickers send PageUp/PageDown),
  Home/End, number keys + Enter to jump. URL hash #/n so a reload keeps your place.
- Fragments: elements with data-step reveal one at a time before moving to the next slide.
- Progress bar along the top, plus a slide counter. F = fullscreen, N = speaker-notes overlay (notes come from
  <aside class="notes"> in each slide), O = grid overview of all slides, T = an on-screen pitch timer.
- Per-slide entry animations replay every time you arrive: data-anim="fade-up|fade-in|scale-in|slide-left|draw"
  with data-delay for staggered reveals. Number count-ups via data-countup="12345" (Indian number formatting).
- Print stylesheet: Chrome "Print → Save as PDF" gives one slide per page (a backup PDF).

VISUAL DESIGN
- Theme "Deep Water Control Room": background #050912 with a subtle animated noise/gradient, accent cyan #22D3EE,
  emerald #10B981 (sensors), crimson #EF4444 (leaks), amber #F59E0B (valves), text #E2E8F0, muted #64748B.
- Big, confident type (titles 72–96px, at most ~25 words per slide). Numbers in a monospace font. Generous whitespace.
- Signature motif: an animated SVG pipe network with water flowing through it (stroke-dasharray + stroke-dashoffset
  animation). It reappears across slides and evolves: calm flow → leak burst → 5 sensors light up →
  triangulation lines converge → valves close.
- Match the product UI colors so the hand-off to the live demo feels seamless.

SLIDES (main flow ≈ 3 min. Each slide has speaker notes with exact spoken lines and a time budget)
 1. Title: "HydroPulse": "Finding underground water leaks with 5 sensors and a digital twin." Track 02 badge,
    Lakshya 2026 · SJBIT, team names. Water flows across the background network.
 2. Hook: an animated counter of litres lost in Bengaluru per day [VERIFY figure]. One line:
    "Not a water shortage. A pipe problem."
 3. Problem: three animated stat cards: NRW share [VERIFY ~35–40%], days a leak goes unnoticed
    [VERIFY 10–20 days], consequences (sinkholes, contamination, wasted pumping energy). A cross-section SVG of a
    road with a pipe 2 m below it, leaking.
 4. Why current methods fail: a comparison table that animates in row by row: dig-and-check / acoustic crews /
    flowmeters on every street vs HydroPulse (cost, speed, coverage, hardware needed).
 5. The insight: "A water network is a graph. A leak leaves a fingerprint." The network SVG with 5 emerald sensors
    lighting up one after another.
 6. How it works: a 5-step animated pipeline: Map (OSM streets) → Twin (EPA WNTR physics) → Sense (5 sentinels via
    betweenness centrality) → Triangulate (cosine fingerprint match + verification) → Isolate (valve plan).
 7. The science, made simple: 3 visual cards with tiny formulas and plain-English captions:
    Torricelli Q = Cd·A·√(2gh) · Betweenness centrality · Cosine similarity. Explain why it works whatever the
    leak size.
 8. LIVE DEMO: a full-bleed "Switching to live control room →" slide showing localhost:5173 and the Kengeri
    map screenshot. A small "▶ backup video" link to assets/demo_backup.mp4 if the live demo fails.
 9. Results (REAL numbers only): placeholders {{TOP1_ACC}}, {{TOP3_ACC}}, {{MEAN_ERR_M}}, {{P95_MS}},
    {{N_JUNCTIONS}}, {{N_SENSORS}}=5, filled from backend/benchmark_report.md. Show an accuracy-vs-noise mini bar
    chart in SVG. Add a footnote: "Simulated on a Kengeri OSM-derived network with EPA WNTR. Field validation pending."
10. Impact & ROI: an INTERACTIVE calculator (sliders + live outputs):
    inputs: ward/city supply (MLD, default 100), NRW % (default 35 [VERIFY]), share of NRW that is physical leakage
    (default 50%), share of that leakage recoverable through faster detection (default 20%), production + pumping
    cost ₹/kL (default 45, labelled "editable assumption").
    outputs: litres saved/day = MLD×1e6 × NRW × physical × recoverable. Tankers/day = litres/6000.
    ₹/year = litres/day/1000 × cost × 365, formatted in lakh/crore (Intl en-IN). Numbers animate as the user drags.
11. Architecture & tech stack: an animated HTML/SVG diagram (no Mermaid runtime): OSMnx → EPANET .inp → WNTR engine
    → Sensitivity matrix → FastAPI → React + Leaflet SCADA UI. Tech logos drawn as text chips: Python, EPA WNTR,
    NetworkX, NumPy, FastAPI, React, Leaflet, Recharts, Tailwind.
12. Roadmap: Hackathon twin → pilot on one BWSSB District Metered Area using existing pressure loggers → SCADA
    integration → city-wide rollout. Also: works with any EPANET model, so any Indian city can use it.
13. Team: 4 cards with name, role, and one line on what they built.
14. Thank you / Q&A: logo, tagline, "Every drop, located."
 Backup slides (after the end, reached by jumping): B1 "Why only 5 sensors?", B2 "Limitations & honesty"
 (synthetic elevations, pipe diameters inferred from road class, steady-state model, node-level resolution),
 B3 "Accuracy vs sensor noise" detail, B4 "How it plugs into real SCADA".

SUPPORTING DOCS
- pitch_and_roi/pitch_script.md: a 3-minute version and a 2-minute version. Speaker-by-speaker lines with
  [mm:ss] cues, the demo choreography (who clicks what: preset 1 → isolate → judge mode → reveal), and
  transition lines into and out of the demo.
- pitch_and_roi/qa_defense.md: 15 likely judge questions with crisp 2–3 sentence answers. Include: How can 5
  sensors cover 100+ junctions? What if two leaks happen at once? How do you get real pipe data? What about
  sensor noise? Steady-state vs transients? Is this just simulation, and how would you validate it? Cost to
  deploy? Why cosine similarity and not ML? Why not acoustic sensors? How accurate is it, and how did you
  measure that? Who pays, and what's the business model? Scalability to all of Bengaluru? Data privacy and
  security? What happens with closed valves? Pipe ageing and roughness?
- pitch_and_roi/sources.md: every statistic with its source URL and access date. Anything you couldn't verify
  stays marked [VERIFY] on the slides and gets reworded as an approximation ("an estimated…").
- pitch_and_roi/architecture.mmd: a Mermaid flowchart of the same architecture (for the README/docs).

CONTENT RULES
- Never invent statistics. Use credible sources (BWSSB, CAG, MoHUA/AMRUT, World Bank, peer-reviewed papers).
  Tag anything you can't confirm with [VERIFY].
- "Confidence %" in the product is NOT accuracy. Accuracy figures come only from the benchmark report.
- Keep slides visual and words minimal. Detail lives in the speaker notes.

ACCEPTANCE CHECKS
[ ] Double-click index.html with Wi-Fi OFF → everything renders and animates, and the console has no errors.
[ ] Clicker (PageUp/PageDown), arrows, fragments, N/F/O/T keys and hash navigation all work.
[ ] Looks correct at 1920×1080, 1366×768 and on a 4:3 projector (letterboxed).
[ ] ROI calculator updates live with Indian number formatting.
[ ] Print → Save as PDF gives a clean one-slide-per-page PDF.
[ ] Results slide uses the real benchmark numbers (or clearly visible {{placeholders}} before H13).
[ ] pitch_script.md timed out loud: 3-min ≤ 3:00, 2-min ≤ 2:00. qa_defense.md has 15 Q&As. sources.md is complete.
Report: files created, how to present (keys), the placeholders still to fill, and the open [VERIFY] items.
````

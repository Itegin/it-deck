---
name: it-deck-visual-design
description: Visual design principles for IT-Deck's frontend (Dashboard, Studio Mode). Use whenever touching frontend/css/, frontend/js/render.js, frontend/studio.html, or frontend/index.html for anything visual.
---

# IT-Deck Visual Design

## What this project actually is

IT-Deck is a touch control panel, not a content site or portfolio. People
open it, glance at a grid of tiles, and tap one -- there is no scroll, no
narrative, no hero section to reveal. Target devices are iPhone X and
newer -- iPhone X itself is capped at iOS/Safari 16.7, which sets the
floor this project must support. Every visual decision is judged against
that floor first, not against what looks good on a MacBook.

## Hard no's, and why

- No WebGL, no per-element canvas, no "shared rendering layer" -- direct
  GPU cost on hardware already struggling with a WebSocket connection.
- No heavy backdrop-filter/blur stacks, no glass material system -- same
  reason, and this project has no build step to tree-shake or optimize
  the CSS that would require.
- No parallax, no staggered section reveals, no scroll-triggered
  animation -- there is no scroll. Motion here means: a tile press
  animation, a state transition (mute toggling color), a value updating
  smoothly. Motion answers what the user just did, not a decorative
  entrance.
- No build step. No bundler, no CSS-in-JS, no framework. Plain CSS files,
  vanilla JS ES modules, exactly as the rest of the project already is.

## What's already fixed -- don't reinvent it

Brand palette already exists across the codebase: purple accent (#8e5ff5,
also #6D28D9 in generated docs), teal for "active/normal" state
(#0d9488), red for "alert" state (#dc2626), dark tile background
(#2a2f38 / #1A1F26). Any new UI work should extend this system via CSS
custom properties, not introduce a new palette. If a change requires a
new color, name it and justify it against this existing system rather
than picking something that merely looks nice in isolation.

## Real bugs this project has already hit -- check for these specifically

- CSS specificity: a type-based selector (e.g. `#item-form`) can silently
  beat an attribute selector (`[hidden]`) and leave dead UI visible. This
  already caused real data corruption once (a form that looked hidden
  wasn't, got submitted with default values). Check specificity whenever
  adding a rule that interacts with existing hide/show logic.
- Container queries need a definite size on the container or every child
  collapses to 0px -- happened for real on this project's #grid. Verify
  computed sizes in DevTools before trusting a container-query layout.
- Test on an actual narrow/short viewport, not just desktop resized --
  real iPhone hardware has different constraints than a resized Chrome
  window.

## What's genuinely worth doing

Restraint: spend visual boldness in one place (e.g. the currently-active
tile's state color), keep everything else quiet. Deliberate typography
and spacing over decoration -- a tile's label and icon are the content,
not a vehicle for a trend. Avoid generic AI-design tells even in a small
utility UI: identical rounded cards with the same soft shadow on
everything regardless of hierarchy, gradient washes as pure decoration,
ALL-CAPS tracked labels where a normal label would do.

Accessibility floor, non-negotiable: prefers-reduced-motion respected,
visible keyboard focus states, real contrast ratios (not just "looks
readable to me"), touch targets sized for a finger not a cursor.

## Copy in the UI

Button/tile labels and error messages are content, not decoration. Active
voice, plain language, matching what the person actually did: a mute
button's label state should say what pressing it does, not describe
internal system state. An error should say what happened and, if
possible, what to do about it -- never vague, never apologetic filler.

## Process for any visual change

1. Look at the actual current state first (screenshot or DevTools), don't
   assume what's there.
2. Make the change.
3. Screenshot the result at a small viewport size before calling it done
   -- desktop-only verification has caused real bugs on this project.
4. Check prefers-reduced-motion and keyboard focus explicitly if the
   change touches either.
---

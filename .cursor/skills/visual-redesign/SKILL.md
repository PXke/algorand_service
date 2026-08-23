---
name: visual-redesign
description: >-
  Owns visual taste for this Algorand news frontend when the user dislikes
  the look, wants it more beautiful or enjoyable, or asks to redo the design.
  The user is not a designer — the agent must propose direction, not ask them
  to invent type, color, or layout. Use before and during any UI restyle.
---

# Visual redesign

The product is an **editorial news site**, not a SaaS dashboard, not a crypto terminal, not a Dribbble glassmorphism shot.

The user has said they do not find the current UI beautiful or enjoyable. Treat that as a brief: **change the feeling**, do not polish the same look.

## Process (do not skip)

1. **Direction first** — one sentence of intent (e.g. “quiet broadsheet you want to linger in”). Pick 3 references by name (newspapers, magazines, reading apps). Do not start in CSS.
2. **One system** — restyle `frontend/src/app.css` tokens as a single vocabulary (surface, ink, accent, type, space, radius). Then shell (`AppShell`), then pages. No one-off hex in components.
3. **Show, don’t quiz** — implement or mock, then screenshot. Ask the user only A vs B, never “what font do you like?”
4. **Loop** — if it still feels cheap, change hierarchy and type first, not more color.

## Taste bar

Beautiful here means: calm paper, strong headlines, generous measure, obvious primary story, chrome that recedes. Enjoyable means: easy scanning, no visual noise in the tab row, satisfying density without looking like a spreadsheet.

**Do**

- One display face for headlines, one reading face for body, system UI for chrome (already the intent in `app.css`).
- Hierarchy: lead story much larger than the rest. Squint test.
- Space in a 4/8px rhythm. Align to `--shell-gutter` / `--max-wide` / `--max-reading`.
- Color as ink and paper; accent only for interaction. Topic `--tone-*` may stay for meaning but must not rainbow the page.
- Light and dark as the same design, restepped.

**Do not**

- Gradients as decoration, neon crypto, glass, emoji icons, rainbow cards, drop shadows on everything, new typefaces stacked on the three already chosen unless replacing the whole set.
- Asking the user to pick hex codes, fonts, or “make it pop.”
- Parallel token files or Tailwind-by-another-name next to `app.css`.
- Restyling admin (`frontend/src/routes/admin`) in the same pass unless asked — keep public reading UI the priority.

## Tools to use

- Svelte skills + Svelte MCP autofixer on every `.svelte` change.
- `GenerateImage` only for mood/reference when the user wants a picture, not for the live UI.
- Canvas only for a direction board (type specimens, token map), not as the product.
- Browser screenshot after visual changes when `cursor-ide-browser` is available.

## Existing constraints worth keeping

- Favicon/`BrandMark` must stay identical to `public/favicon.svg` unless the user asks to change the mark.
- i18n and layout structure (feeds, topics, article) stay; this skill is look-and-feel.
- WCAG text contrast on `--on-surface` / `--body` vs `--surface` / `--panel`.

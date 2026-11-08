# Design system

The dark redesign (September 2026), with selectable themes since November 2026. Token names are
defined in `:root` in `frontend/src/index.css`; `frontend/src/v3.css` (loaded after it) overrides
their values for the V3 look, which is the **Original** theme; `frontend/src/themes.css` holds the
other themes (see [Themes](#themes)). Components reference tokens, never raw values. The values in
the tables below are the `index.css` originals; read `v3.css :root` for what Original shows.

## Tokens

### Surfaces
| Token | Value | Used for |
|---|---|---|
| `--surface-page` | `#12161C` | Page background |
| `--surface-header` | `#171E28` | App header |
| `--surface-panel` | `#1B222D` | Cards and panels |
| `--surface-control` | `#252F3E` | Inputs, buttons, chips |
| `--surface-hover` | `#212A37` | Hover fill |
| `--surface-selected` | `#29364B` | Selected rows and menu items |
| `--surface-inset` | `#161C25` | Recessed wells inside a panel, table headers |

### Text and lines
`--text-primary` `#E5EAF2`, `--text-secondary` `#96A4B6`, `--text-tertiary` `#7F8CA0`.
`--divider` `#303B4B`, with `--divider-soft` and `--divider-strong` on either side of it.

### Action and result
| Token | Value | Meaning |
|---|---|---|
| `--action-primary` | `#748FF5` | Primary buttons |
| `--accent-line` | `#91A8FF` | Selection, focus ring, equity line |
| `--result-pos` | `#66D7AC` | A winning result |
| `--result-neg` | `#F28B94` | A losing result |
| `--caution` | `#E9BA78` | Warnings, stop levels, middling quality |
| `--level-stop` | `var(--caution)` | Stop history line and stop marks (Edge Trader events) |
| `--level-target` | `#8FA9FF` | Target history line and target marks; blue so it never reads as stop amber |
| `--cal-pos` / `--cal-neg` | `#203630` / `#392730` | Calendar day tints |

**Red and green mean result, never direction.** A long can lose and a short can win, so side is
always neutral text. An exact zero is neutral too.

Two deliberate exceptions, both chart conventions: candles keep up and down colours, and Buy and
Sell fill markers keep green and red because they are execution annotations. Counts of rule breaks
(mistake frequency) use `--caution`, not the loss colour, since they are not a P&L result.

### Type
In Original, `--font-body` is Inter, `--font-display` is Bricolage Grotesque and `--font-mono` is
IBM Plex Mono (prices, times and executions only). Themes swap all three. Sizes: page title 30,
section 19, body 15, small 13, caption 12, KPI value 28.

Numbers use tabular figures (`.num`) and explicit signs. `index.html` loads the Original fonts plus
one stylesheet for the theme fonts (Public Sans, Source Serif 4, Source Sans 3, JetBrains Mono,
IBM Plex Sans Condensed).

### Spacing, shape, depth
`--space-1` through `--space-7` are 4, 8, 12, 16, 20, 24 and 30px. Radii are 4, 6 and 8px.
Cards use `--shadow-card`; dropdowns and modals get progressively stronger shadows. The header is
68px (`--header-h`) and sticky content clears it with `--sticky-offset`.

### Legacy aliases
`--bg-primary`, `--bg-card`, `--border`, `--text-muted`, `--green`, `--red`, `--purple`, `--accent`
and the rest still resolve, so older components render in the new palette without being rewritten.
Prefer the semantic names in new code.

## Layout primitives

| Class | Behaviour |
|---|---|
| `.app-header` / `.app-nav` | 68px utility row; wraps to two rows at 1500px and below |
| `.page-header` | Page title, subtitle and right-aligned actions |
| `.card`, `.panel-flush` | Panel surface; `panel-flush` removes padding for tables |
| `.grid-2-1`, `.grid-3`, `.grid-2`, `.stack` | Collapse to one column at 1180px |
| `.kpi-strip` / `.kpi-cell` | One surface with internal separators. Column count follows the cell count, so rows split evenly (8 becomes 4+4, 7 becomes 4+3) |
| `.table-container`, `.scroll-x` | Horizontal scroll with an edge-shadow overflow cue. Also the containing block, so `sr-only` labels inside cannot widen the page |
| `.tabs` / `.tab` | `role="tablist"` with arrow keys; wraps rather than hiding tabs |
| `.seg` / `.seg-btn` | Segmented control using `aria-pressed` |
| `.chip`, `.notice`, `.empty` | Inline status, block status, empty state |

Wide content scrolls; it is never truncated or hidden. The month calendar keeps a readable minimum
width and scrolls sideways on narrow screens rather than squashing its P&L values.

## Interaction rules

These are enforced in review and partly by the local Impeccable detector.

- Colours come from tokens. Raw hex belongs only in `:root`.
- Cards use `var(--shadow-card)`.
- Hover is CSS. Never `onMouseEnter` or `onMouseLeave` for visual state.
- Every interactive element has `:focus-visible`. Items filling a clipped container use an inset ring.
- Animate only `transform` and `opacity`. Never `transition: all`, and never animate `width`,
  `height`, `padding` or `margin`.
- `prefers-reduced-motion` is respected globally.
- Rows that act as links are keyboard operable (`tabIndex`, Enter and Space).
- Modals trap focus, close on Escape and restore focus to the trigger.
- Every input has a real label or `aria-label`.

## Charts

Recharts for the analytics, lightweight-charts for the trade chart. Neither resolves CSS variables
in canvas, so `TradingChart.js` reads the tokens at render time with `getComputedStyle` and falls
back to the token values. Axis ticks use `--text-secondary`, grids use `--divider-soft`.

The dashboard equity curve shades drawdowns (the band between the dashed running high and the
curve). Breakdown bars (Patterns, Year Behind) diverge from a zero line: profit grows right, loss
grows left (`BarRow diverge`). Recharts tooltips that reuse `.card` add `.chart-tip` to get their
surface back, because V3 makes `.card` transparent.

## Themes

Settings > Appearance picks one of four themes. The choice is saved in this browser only
(`localStorage['journal-theme']`); nothing goes to the database. Layout, content and behaviour are
identical in every theme.

| Theme | Mode | Fonts | Signature |
|---|---|---|---|
| Original | dark | Inter, Bricolage Grotesque, IBM Plex Mono | Navy and amber, caps labels. The default |
| Minimal | light | Public Sans only | Black actions, hairlines, colour kept for results, black goal marks |
| Natural | light | Source Serif 4, Source Sans 3 | Warm paper with grain, plum actions |
| Pro terminal | dark | JetBrains Mono, IBM Plex Sans Condensed | True black, square, amber heads, step equity line, slashed zeros |

**How it works.** `<html data-theme="minimal|natural|terminal">`; no attribute is Original.
`public/index.html` applies the saved theme before the bundle runs, so a light theme never flashes
dark. `src/theme.js` lists the themes and sets or clears the attribute. Charts read tokens when
they mount, and the page remounts when you leave Settings, so no chart listens for a theme change.
Other open tabs follow through the `storage` event, but only their CSS changes at once: a chart
already on screen there keeps its old colours until you change page.

Raw colour outside `:root` blocks has two deliberate exceptions: the swatches in `THEMES`
(`src/theme.js`), which must show other themes' colours, and the browser-bar colour map in the
inline script in `public/index.html`. Keep both in step with `themes.css`.

**Theme hooks** (in `v3.css :root`, defaults = Original): `--display-weight`, `--kpi-weight`,
`--display-tracking` (multiplier on the tight display letter-spacing), `--goal`, `--curve-line`,
`--curve-fill-top`, `--curve-w`, `--chart-dd-opacity`, `--peak-line`, `--hero-glow-a/-b`,
`--shadow-tip/-gtip/-pop`, `--chart-crosshair-line/-label`, `--logo-a/-b`, `--cal-tint-scale`.

**Adding a theme.**
1. Add a `:root[data-theme="id"]` block to `themes.css` that sets every token in `index.css :root`
   that is not a legacy alias, plus the hooks above. Raw colour lives only in that block; scoped
   rules below it use tokens.
2. `--surface-panel` must be opaque: it backs modals, menus and tooltips.
3. Add the id to `THEMES` in `src/theme.js` and to the colour map in `public/index.html`.
4. Add its fonts to the theme stylesheet link in `public/index.html`, with the weights you use.
5. Light themes: set `color-scheme: light`, lighter `--shadow-*` and `--edge-shadow`, and a
   `--cal-tint-scale` that keeps P&L text on the strongest calendar tint at 4.5:1 (the tint is
   `--result-*` at up to 0.21 opacity times the scale, over `--surface-page`).
6. Check every page (dashboard, trade chart, calendar, reports tooltips, modals, account menu,
   native date inputs) at desktop and phone width, and contrast-check every text token on every
   surface.

The design mockups for these and four themes that were not built (Apple, Zen, Editorial, Nordic
dark) are in `design_ref/themes/` in the main checkout.

## Accessibility baseline

Visible focus everywhere, labelled controls, tablists with arrow-key movement, keyboard-operable
rows and calendar days, scroll cues instead of hidden columns, reduced motion honoured, and
meaning never carried by colour alone (results also carry a sign, grades carry a letter).

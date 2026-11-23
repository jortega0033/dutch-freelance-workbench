# Design tokens

The visual system is the Pipenzo "Flow look": black page, alpha-layer surfaces, one accent (Pipenzo yellow `#FECE02`), red only for danger. Dark is the primary design; the light block is the matching light theme.

## Source and resync

`workbench/static/tokens.css` is a byte-for-byte copy of `extension/sidepanel/tokens.css` in `jortega0033/clip-to-reel-studio` at commit `a13378ce73e5b27213c9a200fc960ec93afef054` (2026-11-21). Never edit the copy. To resync, copy the file over it; nothing else changes. The header comment in the file holds the contrast and yellow-use rules.

`index.html` loads `tokens.css` before `styles.css`. `styles.css` defines no colours of its own.

## Tokens by role

- Surfaces: `--bg` (page), `--surface` (panels, cards), `--bg-raised` (inputs), `--bg-sunken` (wells, chips).
- Text: `--fg`, `--muted`, `--faint`.
- Lines: `--border`, `--hairline`, `--control-border`, `--input-border`.
- Accent: `--primary`, `--primary-press`, `--primary-edge`, `--on-primary`. Yellow only on the primary action and the current-step marker.
- Status: `--danger`, `--danger-bg` (the only red), `--ok`, `--ok-bg`, `--warn-fg`, `--warn-bg`, `--success`, `--info`.
- Focus: `--focus`.
- Secondary controls and hover layers: `--btn-secondary`, `--btn-secondary-border`, `--state-hover`, `--state-selected`.
- Dark blocks and overlays: `--ink`, `--on-ink`, `--hero-bg`, `--glass*`, `--scrim`.
- Shadow, space, radius, motion and font: `--shadow-*`, `--space-*`, `--radius-*`, `--dur-*`, `--ease-*`, `--font`, `--mono`.

## Rule

New UI uses these tokens. No new colour literals in CSS or JavaScript. Script code that needs a colour reads it with `getComputedStyle(document.documentElement).getPropertyValue('--token')`.

## Light and dark

The light values sit in `:root`; a `prefers-color-scheme: dark` block overrides them, so the operating system setting chooses the theme. There is no manual switch.

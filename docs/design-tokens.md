# Design tokens

The visual system is the Pipenzo "Flow look": black page, alpha-layer surfaces, one accent (Pipenzo yellow `#FECE02`), red only for danger. Dark is the primary design; the light block is the matching light theme.

## Source and resync

`workbench/static/tokens.css` is a byte-for-byte copy of `extension/sidepanel/tokens.css` in `jortega0033/clip-to-reel-studio` at commit `a13378ce73e5b27213c9a200fc960ec93afef054` (2026-11-21). Never edit the copy. To resync, copy the file over it; nothing else changes. The header comment in the file holds the contrast rules and the yellow rule of the source project. The header comment differs from this document: it names three uses from the source app (including the "your turn" chip, the hero kicker dot and the overlay opening dot), it does not name the current selection, and it does not cover the focus ring or the dark theme checks. The copy must stay identical to the source, so it cannot be edited here. This document takes precedence over any comment in tokens.css, including those on `--primary` and `--state-selected`.

`index.html` loads `tokens.css` before `styles.css`. `styles.css` defines no colours of its own.

## Tokens by role

- Surfaces: `--bg` (page), `--surface` (panels, cards), `--bg-raised` (inputs), `--bg-sunken` (wells, chips).
- Text: `--fg`, `--muted`, `--faint`.
- Lines: `--border`, `--hairline`, `--control-border`, `--input-border`.
- Accent: `--primary`, `--primary-press`, `--primary-edge`, `--on-primary`. Yellow only on the primary action, the current-step marker and the current selection (see "Yellow, selection and focus"). Buttons are neutral by default; add the `primary` class (`button.primary`, `.button.primary`) to one primary action per screen.
- Status: `--danger`, `--danger-bg` (the only red), `--ok`, `--ok-bg`, `--warn-fg`, `--warn-bg`, `--success`, `--info`.
- Focus: `--focus`, a focus indicator, exempt from the accent limit (see below).
- Secondary controls and hover layers: `--btn-secondary`, `--btn-secondary-border`, `--state-hover`, `--state-selected`. `--state-selected` does not mark the current selection; that is a solid `--primary` fill (see below).
- Dark blocks and overlays: `--ink`, `--on-ink`, `--hero-bg`, `--glass*`, `--scrim`.
- Shadow, space, radius, motion and font: `--shadow-*`, `--space-*`, `--radius-*`, `--dur-*`, `--ease-*`, `--font`, `--mono`.

## Rule

New UI uses these tokens. No new colour literals in CSS or JavaScript. Script code that needs a colour reads it with `getComputedStyle(document.documentElement).getPropertyValue('--token')`.

## Yellow, selection and focus

Source: decision 14 in `owner-decisions.md`.

- Decided by the owner on 2026-11-23 (UI rule 8): the yellow accent is used for the primary action, the current-step marker and the current selection.
- How a selection looks (recommended by the orchestrator, adopted under the owner's delegation of the same day): a solid `--primary` fill with `--on-primary` text, not only a yellow border, which has about 1.5:1 against white on light. It also carries a second sign that is not colour (weight, icon or text) and the existing aria attribute (for example `aria-current` or `aria-selected`). Never a tint with a same-hue border and never a thick one-sided border (UI rules 1 and 2).
- Focus ring (recommended by the orchestrator, adopted under the owner's delegation of 2026-11-23): `--focus` is a focus indicator and is exempt from the accent limit, including where it is yellow in dark. A ticket that says the focus style stays unchanged means the rule on `main` after the token pull request.

Where a page still marks the current selection in a neutral colour (`--state-selected`, weight or underline only), it has not been changed yet. The page follows in ticket work; this document does not claim it is done.

## Light and dark

Contrast and no-colour-alone checks cover both the light and the dark block of `tokens.css` (recommended by the orchestrator, adopted under the owner's delegation of 2026-11-23). A pair that passes in one block only does not pass.

The light values sit in `:root`; a `prefers-color-scheme: dark` block overrides them, so the operating system setting chooses the theme. There is no manual switch.

## Linking

A ticket that creates a page, or its static allowlist, links `tokens.css` before any other stylesheet and serves it: the allowlist entry and its test are part of that ticket (recommended by the orchestrator, adopted under the owner's delegation of 2026-11-23).

# Operator workbench art direction

**Status:** approved direction, 2026-07-18.

## North star

The operator workbench should feel like an unusually capable 2004–2008 hobbyist
media tool: phpBB intentional grunge, Japanese portal information density, and
the practical confidence of a fansub encoder's private “wizard” running on a
ThinkPad. It is a workbench, not a SaaS dashboard.

The period reference is functional rather than ironic. Dense tables, obvious
links, small type, durable URLs, and controls that look clickable are good. The
surface may be eccentric and handmade, but the underlying status language must
remain exact.

## Visual rules

- Prefer Verdana, Tahoma, Arial, and Japanese system sans-serif stacks at
  11–13 px. Headings rarely exceed 20 px.
- Use a centered, desktop-first shell around 1100–1200 px with narrow gutters.
  Horizontal scrolling is acceptable for comparison tables on small screens.
- Favor tables, fieldsets, striped rows, inset panels, 1 px borders, compact
  tabs, and mild bevels over cards and floating surfaces.
- Use navy, steel blue, dirty cyan, muted violet, amber, and off-white. Small
  gradients and scanline textures are period-appropriate; glassmorphism is not.
- Keep vertical rhythm tight: 2–6 px inside data cells and 6–10 px between
  major regions. Empty space must earn its keep.
- Put context, counts, timestamps, recipes, and status in the first viewport.
- Use underlined text links. Buttons should resemble buttons, not pills.
- Preserve a narrow illustrated banner, compact breadcrumb/navigation strip,
  and explicit footer/build context as the stable shell.

## Interaction rules

- The primary interaction is scanning a table, following a link, expanding a
  row, or submitting a small filter form.
- One campaign lens may combine adjacent decision boundaries when splitting
  them would hide the operator's input. The first desk shows binding proposals,
  automatic admission, and canonical selection together.
- Lead with the campaign's conclusion products. Put an upstream-to-downstream
  stage carousel below them: a horizontally scrollable card track with explicit
  previous/next controls, a selected-stage marker, and one stage-owned inspector
  below. Stage-specific exceptions and future facets live inside that inspector
  rather than on a global failures page.
- Stage cards carry aggregate status only. Their real rows are server-paged with
  a hard display limit and fetched lazily when the operator selects a card.
  High-volume conclusion products use the same bounded-page discipline.
- "Step in" means inspect the inputs behind a particular cell or decision,
  not trace one record through the entire dependency graph by default.
- Dense row disclosure arrows belong in the rightmost parent cell. Expanded
  candidate details belong in one full-width child row directly beneath
  that parent. Whole successful sections should be collapsible so
  failure/quarantine tables are one action away.
- Domain identifiers should link to authoritative external records when a
  stable URL exists; the operator must be able to investigate surprising
  numbering without manually reconstructing a URL. Normal link styling is
  sufficient; do not add a noisy external-link glyph.
- Opaque selected-capture IDs expose their credential-free `s3://bucket/key`
  manifest identity as native hover text.
- Expanded candidate sets have one shared label row followed by one value row
  per candidate; do not repeat field labels inside every record.
- Progressive enhancement must remain real: filters and navigation work as
  ordinary links/forms before HTMX replaces a region.
- Specialized inspection views stay specialized. Do not create a generic JSON/EAV
  renderer merely to reuse markup.
- Empty states must explain which upstream durable product is absent and show
  the exact command or navigation needed; a polished blank dashboard is a bug.

## Anti-goals

- Large hero typography, generous marketing whitespace, floating rounded card
  grids, minimalist icon-only controls, or generic admin-template polish.
- Artificial sample metrics presented as real state.
- Nostalgia that reduces legibility, including fake CRT blur over body text,
  animated marquees, inaccessible contrast, or sound effects.
- “Enterprise retro” branding. The workbench should look personally owned and
  heavily used, not like a theme applied to a product suite.

## Banner asset

`operator-banner-v1.png` is an AI-generated, text-free period collage of a
fansub workstation. It is decorative only; all navigation and labels remain
HTML. The source prompt and generation method belong in the implementation
handoff whenever the asset is replaced.

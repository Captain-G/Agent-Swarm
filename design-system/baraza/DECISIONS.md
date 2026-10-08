# Baraza design decisions

`MASTER.md` was generated with UI UX Pro Max for a government/civic analytics
product ("high-contrast navy + blue", accessible and ethical). Baraza follows it
with these deliberate adjustments, implemented in `static/css/baraza.css`.

| Topic | MASTER.md | Baraza | Why |
|---|---|---|---|
| Typeface | Fira Sans / Fira Code from Google Fonts | Atkinson Hyperlegible (vendored WOFF2), system monospace for numbers | Designed for low-vision readers and distinguishes easily confused letters (I/l/1, O/0); the audience includes non-technical officials and readers on small phones. Vendored so the app works offline and makes no third-party requests. |
| Data colours | Not specified | Support = teal `#0f766e`, oppose = orange `#c2410c`, neutral = slate | A colour-blind-safe pair (distinguishable with deuteranopia and protanopia), always paired with text labels and signs (+/-), never colour alone. Avoids red/green and party colours. |
| Themes | Light | Light and dark, following the system setting, with a toggle | Long analysis sessions; dark mode tokens are tuned for AA contrast. |
| Icons | Lucide/Heroicons SVG | Lucide SVG, inlined server-side | No icon font, no emoji, accessible names on icon-only buttons. |
| Touch targets | 44 px | 44 px minimum for all controls | Mobile-first: many users will be on phones. |
| Motion | Reduce | Transitions of 150-200 ms; none under `prefers-reduced-motion`; live feeds do not animate | Calm interface for a sensitive topic; avoids distraction while data updates. |
| Charts | - | Chart.js with plain-language headings, key numbers stated in text, and tables for segment breakdowns | Charts are not the only way to read the important numbers. |
| Breakpoints | 375 / 768 / 1024 / 1440 | Same; no horizontal scrolling at 375 px | Verified with automated screenshots. |

Page-specific notes:

* **Live simulation**: the feed uses a social-media-like layout (avatar initials, handles, reactions) under a permanent "All accounts and posts are simulated" label, and every post shows a stance badge.
* **Reports** have a print stylesheet so "Print / save as PDF" produces a clean document.

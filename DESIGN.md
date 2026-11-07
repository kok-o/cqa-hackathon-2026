# CQA design system

An operator quality review workspace. The core task is comparing an assessment with its original conversation, so source and evidence remain visible side by side. The design follows the parent brief's restrained green Operate direction; no separate approval flow was introduced.

## Visual system

- Canvas `#f3f6f5`, paper `#ffffff`, ink `#1e3029`, secondary `#53665d`, line `#dce5df`.
- Primary action `#216447`, dark navigation `#173d2d`, selected surface `#e8f3ec`.
- Warning `#875313` on `#fff6df`; error `#a03434` on `#fff0ef`.
- Manrope Variable, locally bundled. Body 14px with 1.55 line height; heading 24px; compact labels 12–13px. Tabular numeric data. Sentence case.
- 4px base spacing, 16/24px panel spacing, 8px controls, 12px sheets; separators explain regions.
- Native buttons, inputs, selects, details and accessible dialogs. 40px minimum control height; clear 3px focus ring.

## Composition and behavior

Top navigation switches between review and checklist workspaces. The review desk has queue, transcript and assessment panes. At tablet widths queue becomes a bounded list; at mobile widths all panes stack. Evidence selection scrolls and focuses the exact source message; no invented audio alignment. No decorative animation. Reduced motion disables smooth scrolling.

## Direction record

Code-led Operate implementation. Concept seed `seed:de5a3779340b95ba` was run; pinned review-desk brief governs the chosen layout, palette and native controls. The seed's structural discipline is retained through stable columns, explicit state changes and preserved history, without adopting ornamental motifs.

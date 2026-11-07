# CQA design system

An operator quality review workspace. The core task is comparing an assessment with its original conversation, so source and evidence remain visible side by side. The design follows issue #5: a restrained green workspace with clear review context and explicit source evidence.

## Visual system

- Canvas `#f3f6f4`, paper `#ffffff`, ink `#1a2823`, secondary `#53665d`, line `#dce6e0`.
- Primary action `#216447`, dark navigation `#173d2d`, selected surface `#e8f3ec`.
- Warning `#875313` on `#fff6df`; error `#a03434` on `#fff0ef`.
- Manrope Variable, locally bundled. Body 14px with 1.55 line height; heading 24px; compact labels 12–13px. Tabular numeric data. Sentence case.
- 4px base spacing, 16/24px panel spacing, 8px controls, 12px sheets; separators explain regions.
- Native buttons, inputs, selects, details and accessible dialogs. 44px minimum mobile control height; clear 3px focus ring.

## Composition and behavior

Top navigation switches between review and checklist workspaces. The review desk has queue, transcript and assessment panes. At tablet widths the queue is opened on demand; mobile uses Queue, Conversation and Assessment tabs with a single visible pane. Evidence selection focuses the exact source message or highlights the complete checked range; a return action restores the criterion context. Audio seeking requires verified time alignment.

The checklist editor has a persistent criterion index and one selected form grouped into instructions, scoring and applicability. At mobile widths the index is a horizontal strip. Drafts survive criterion switches; a sticky save bar distinguishes a new version from a separate checklist. Official discrete scales, N/A and critical-answer mappings remain editable.

Use CSS transitions of 160ms for evidence, 180ms for results and up to 220ms for panels. Reduced motion removes transitions and smooth scrolling while preserving focus and selected states.

## Direction record

Code-led Operate implementation. Concept seed `seed:de5a3779340b95ba` was run; pinned review-desk brief governs the chosen layout, palette and native controls. The seed's structural discipline is retained through stable columns, explicit state changes and preserved history, without adopting ornamental motifs.

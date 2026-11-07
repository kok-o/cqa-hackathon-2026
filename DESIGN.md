# CQA interface

A compact review tool for checking an assessment against its conversation. The interface uses plain navigation, readable source text and direct actions. Avoid promotional copy, repeated headings, decorative numbering and collections of rounded cards.

## Tokens

- Greens sampled from the Freedom shield supplied by the user: leaf `#4FAF3D`, deep green `#145431`. Bank ink `#0E151C`, white surface, canvas `#F5F6F7`, dividers `#E2E5E8`, muted type `#626A72`.
- Dark application header and selected conversation, light queue and working surfaces. Buttons use deep green with white labels; the leaf green is reserved for small brand and selection accents.
- IBM Plex Sans, bundled locally. Body 14px, source messages 14–15px, metadata 12px; tabular numerals for scores and weights.
- Compact controls with 4px corners. Regions are separated by alignment and thin rules, without decorative shadows.
- Mobile touch targets are at least 44px. Focus remains visible for every action.

## Review

Desktop keeps queue, conversation and assessment together. Tablet opens the queue on demand. Mobile uses Queue, Conversation and Assessment tabs. Evidence opens the exact message or checked range, and the return action restores the selected criterion. Audio seeking requires verified timestamps.

The selected conversation header belongs to the conversation and assessment columns. The queue starts beside that header and spans the entire workspace height, avoiding a redundant banner above the queue.

The desktop queue resizes from 240 to 560 px while preserving at least 320 px for the transcript. Drag the divider or use Left/Right (Shift for a larger step), Home/End; double-click resets. Width persists locally. Mobile and tablet retain their existing full-width panel and drawer.

## Rules

The editor aligns with the page title and uses the available width. A metadata row joins a 280 px criterion index and one form; their headings align. On mobile, a full-width selector replaces the horizontal criterion list. Draft values survive selection changes. The save bar distinguishes a new version from a separate checklist; official scales, N/A and critical-answer mappings stay editable.

Keep explanatory text only where it affects a decision, such as discrete scales or an audio limitation. Validation stays explicit. Status changes come from the API; animations use short CSS transitions and disappear under reduced motion.


## Design review

Applied [frontend-design](https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md). The conversation is the main document; criterion scores and evidence act as adjacent review notes. The distinguishing treatment is the source gutter and the inline criterion ledger. Keep the surrounding shell quiet: no hero, decorative queue numbers, repeated section captions, avatars or promotional empty-state copy. Criterion details open only on request so all eight scores can be scanned first.

# CQA interface

A compact review tool for checking an assessment against its conversation. The interface uses plain navigation, readable source text and direct actions. Avoid promotional copy, repeated headings, decorative numbering and collections of rounded cards.

## Tokens

- Background `#f4f5f3`, surface `#ffffff`, ink `#25312a`, divider `#e4e8e2`.
- Primary `#216447`; selected rows use a pale green surface and a clear edge.
- IBM Plex Sans, bundled locally. Body 14px, source messages 14–15px, metadata 12px; tabular numerals for scores and weights.
- Compact controls with 4px corners. Regions are separated by alignment and thin rules, without decorative shadows.
- Mobile touch targets are at least 44px. Focus remains visible for every action.

## Review

Desktop keeps queue, conversation and assessment together. Tablet opens the queue on demand. Mobile uses Queue, Conversation and Assessment tabs. Evidence opens the exact message or checked range, and the return action restores the selected criterion. Audio seeking requires verified timestamps.

## Rules

The editor keeps a criterion list beside one form. Its sections cover instructions, scoring and applicability. The mobile list scrolls horizontally. Draft values survive selection changes. The save bar distinguishes a new version from a separate checklist; official scales, N/A and critical-answer mappings stay editable.

Keep explanatory text only where it affects a decision, such as discrete scales or an audio limitation. Validation stays explicit. Status changes come from the API; animations use short CSS transitions and disappear under reduced motion.

# CQA interface

CQA is SetW’s review workspace for the Freedom Broker case. The transcript occupies the central column. The queue selects the conversation and the assessment shows the findings beside the source.

## Visual system

- Use bank ink `#0E151C` for the application header, forest `#164734` for primary controls, and leaf `#02B140` for the shield and connection indicator. The two greens come from the public Freedom shield. [Freedom SVG](https://fbroker.kz/images/Logo.svg)
- Use white `#FFFFFF` for the transcript and form surfaces, canvas `#F2F5F3` around the workspace, and lines `#DCE5DF` to separate panels and transcript turns. Keep amber for items that need attention and red for failures and critical flags.
- Use the locally bundled IBM Plex Sans. Set the transcript at 15 px and metadata at 11–12 px. Use tabular numerals for turn numbers, weights, and scores.
- Keep navigation and text fields compact with 7 px corners. Give the workspace and overlays 12 px corners. Reserve shadows for overlays.

## Review workspace

At desktop widths, the queue resizes from 240 to 560 px while leaving at least 320 px for the transcript. Arrow keys, Home and End provide a keyboard alternative. On tablets the queue opens over the workspace and returns focus to its trigger on dismissal. On phones the queue, transcript and assessment use three tabs with 44 px touch targets.

The queue shows actual counts for the current search and dataset filters. Its footer states how many conversations the next batch will include. The selected conversation stays open when it falls outside the filter.

Transcript numbers refer to the original message positions. A prelude with bot messages can collapse before the first operator response. Evidence reopens the relevant turns, highlights the exact quote or checked range, and offers a return to the same criterion.

Show the launch checklist version beside the result and the checklist for the next launch in the toolbar. The score, its completion status and the critical flag are distinct. After an appeal, the original and reviewed scores are both visible, with the decision reason available alongside them.

## Rules and submission

The rule editor presents an index and one criterion at a time. Name, weight and instructions are grouped at the top. Drafts stay associated with their source versions when switching sections or versions. Saving creates a new version or a separate checklist.

The submission preview uses the validated JSON 1.0 document returned by the export endpoint. Display the team, dataset, destination path, record count, successful results and processing errors. Download the same document the reviewer inspected.

## Motion

Use 160–200 ms ease-out transitions for evidence, disclosure and asynchronous save feedback. Animate opacity and transform. Keep keyboard navigation immediate and preserve the same focus and selection states under reduced motion.

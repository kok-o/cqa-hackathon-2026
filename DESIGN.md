---
name: SetW CQA
description: A light conversation canvas and a graphite assessment ledger for the Freedom Broker case
colors:
  primary: "#167345"
  primary-hover: "#105d36"
  brand-green: "#02b140"
  canvas: "#f7f6f1"
  surface: "#ffffff"
  subsurface: "#f7f6f1"
  text: "#202522"
  muted: "#656b65"
  line: "#dedfd8"
  control-line: "#a8aea6"
  selected: "#edf5f0"
  danger: "#923330"
  danger-bg: "#fcefed"
  warning: "#7b5018"
  warning-bg: "#fbf3df"
  evidence-mark: "#d2dfab"
  assessment-bg: "#202522"
  assessment-text: "#f5f3ec"
  assessment-muted: "#b6beb8"
  assessment-green: "#8bdd9b"
  assessment-line: "#444c46"
typography:
  headline:
    fontFamily: "Manrope, IBM Plex Sans, sans-serif"
    fontSize: "54px"
    fontWeight: 500
    lineHeight: 1.1
    letterSpacing: "-0.04em"
  title:
    fontFamily: "Oswald, IBM Plex Sans, sans-serif"
    fontSize: "30px"
    fontWeight: 500
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  score:
    fontFamily: "Oswald, IBM Plex Sans, sans-serif"
    fontSize: "76px"
    fontWeight: 500
    lineHeight: 1
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Manrope, IBM Plex Sans, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  transcript:
    fontFamily: "Manrope, IBM Plex Sans, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.7
  control:
    fontFamily: "Manrope, IBM Plex Sans, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.35
rounded:
  control: "3px"
  overlay: "12px"
  editor-control: "2px"
spacing:
  compact: "8px"
  control-inline: "13px"
  section: "24px"
  page-gutter: "40px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "8px 13px"
    typography: "{typography.control}"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.surface}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "8px 13px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "8px 11px"
---

# Design System: SetW CQA

## Overview

The approved visual direction takes its typographic contrast and light/dark composition from the user's MOONDROP reference. CQA interprets that direction as a readable warm-paper conversation beside a graphite assessment ledger. Large Manrope page headings and condensed Oswald titles and scores establish a recognisable hierarchy. This is a functional review application, with no decorative product imagery or borrowed claims.

The user rejected both the original pale-green three-panel scaffold and the subsequent generic neutral sidebar. The replacement uses a light horizontal header, a full-width dialogue index and a dedicated two-area review. The Freedom Broker shield remains the brand asset; neither the reference nor the public shield is presented as an official CQA design system.

## Colors

The header and transcript use warm paper, forms and the index use white, and the assessment uses graphite. The assessment is the deliberate dark focal area, with cream text, sage secondary text and pale green actions. Red and amber remain meaningful failure and warning signals on both grounds.

The global CSS custom properties in `frontend/src/styles.css` and scoped assessment properties in `Assessment.css` are the palette sources. Ordinary controls use green `#167345`; assessment actions use `#8bdd9b` on the dark surface. Exact source quotes use the pale olive highlight from `Transcript.css`. The public Freedom shield is sourced from https://fbroker.kz/images/Logo.svg.

## Typography

Locally bundled Manrope 400/500/600 carries body text, labels and page headings. Kazakh passages use IBM Plex Sans throughout because Manrope lacks several Kazakh glyphs; IBM Plex Sans also remains in the fallback stack. Locally bundled Oswald 500 carries the CQA wordmark, transcript and editor section headings, scores and selected data counts. Both families serve real content rather than decorative slogans.

Page titles are Manrope 54px/1.1 at weight 500 with -0.04em tracking, reducing to 40px below 700px. Transcript headings are Oswald 30px/1.15; the main score is Oswald 76px, reducing to 64px on small screens. Original message text is 16px/1.7 with a 68ch maximum measure on desktop and mobile. Form text remains readable at 14px, with 16px mobile inputs.

Keep sentence case. Message positions, timestamps, weights and counts use tabular numerals. Monospace belongs only to actual paths or code. Do not use condensed display type for long explanations or form entries.

## Layout

A 76px warm-paper horizontal header contains the shield and CQA wordmark, centered section navigation and compact model status. The work area fills the width underneath. Main gutters are 40px, reducing to 28px at 1100px and 20px at 700px. Mobile navigation occupies a second header row.

The dialogue index owns its main viewport. State filters and import precede search and optional field filters, followed by a full-width table. Excerpts lead the rows; metadata, status, score and gross-error result remain independently readable. At 760px and below, table rows become compact responsive list entries while selection and sorting remain available.

Review is a separate route. The warm transcript takes the remaining width beside the graphite assessment, which uses at least 390px at standard desktop size and 34% when wider. It becomes 370px below 1100px, 340px below 850px and 490px above 1600px. A compact identity header carries the quick queue and previous/next controls. Both content areas scroll independently. At 700px and below, Conversation and Assessment tabs expose one area at a time.

An evidence-position strip sits above long transcripts. Its numbered markers correspond to validated source quotes or checked ranges; they are not a decorative timeline. Checklist editing keeps a narrow criterion index beside a single form. Summary uses light flat rows and inline counts. Hash routes retain the section or encoded dialogue key and support browser navigation, but do not encode an evaluation snapshot.

## Elevation & Depth

The light/dark split is structural. Ordinary table, transcript, summary and editor surfaces remain flat. Light rules mark meaningful region boundaries. The native modal uses `0 18px 70px #10251c38` with a dim backdrop; Radix dropdowns escape clipping through their portal. Working regions do not receive floating-card shadows.

## Shapes

General controls have 3px corners, editor controls 2px and modal overlays 12px. Full working areas are not rounded cards. The assessment fills its column edge to edge. Subtle selected-source washes and a 1px quote mark radius retain text legibility.

## Components

- **Navigation** uses underlined application sections in the light horizontal header. The skip link moves focus into the current section without changing its route.
- **Dialogue index** keeps actual scope counts and makes multi-selection explicit. Selection exposes the batch action. Searching, filtering and sorting affect the working list; the detail queue follows that same order.
- **Launch dialog** appears when the user requests an assessment. It states the number of target dialogues, lets the user choose a version, and names the configured model. Confirming starts the existing real queue; canceling sends no assessment request.
- **Source and evidence** keep original message numbers. Long sources expose a compact navigation strip whose positions are derived from valid quote references and checked ranges in the actual assessment; selecting a marker uses the same evidence and focus-return flow as a criterion. A bot prelude may collapse, but a selected citation reopens its source. Exact quotes or checked ranges are highlighted, and the return action restores focus to the initiating criterion. Source snapshots and current dialogue content retain their existing distinction.
- **Assessment** shows provenance, score and gross-error status separately. Criterion details pair explanation with source controls and an appeal. Original and revised outcomes retain their reason and version context. Version-bound drafts survive application navigation.
- **Checklist editor** saves a new version explicitly. The visible draft is associated with its base version; editing does not rewrite a historical assessment.
- **Submission** has two steps. Parameters prepare the validated server document; the receipt shows its team, model, dataset, path, counts and errors. Download uses that same object. Changing parameters discards the receipt and requires preparing it again.
- **Motion and focus** keep source highlighting and source markers instant. Pointer-triggered criterion disclosure, revision feedback and modal entry use 160ms transitions. Fine-pointer hover colors use 150ms transitions. Evidence navigation scrolls smoothly only for nearby pointer-triggered jumps; keyboard and reduced-motion jumps are instant. Focus uses a visible 2px outline with an offset. No decorative page entrance or invented progress is added.

## Do's and Don'ts

- Do begin with the user's current task and real transcript content.
- Do preserve independent source, assessment, gross-error and reviewer-provenance facts.
- Do keep import, launch, review, appeal, rule editing and export discoverable in their respective contexts.
- Do keep native focus and keyboard behavior intact when changing composition.
- Don't restore the permanently visible queue beside the two review areas.
- Don't recreate the rejected pale-green card framework or neutral sidebar through new component names.
- Don't extend the graphite assessment palette to every screen; its contrast with the warm transcript is deliberate.
- Don't turn a library's default appearance into the visual specification.
- Don't invent scores, model confidence, progress percentages or available audio.

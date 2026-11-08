---
name: SetW CQA
description: Conversation quality review for the Freedom Broker case
colors:
  primary: "#167345"
  primary-hover: "#105d36"
  brand-green: "#02b140"
  canvas: "#f6f7f8"
  surface: "#ffffff"
  subsurface: "#fafbfc"
  text: "#20252b"
  muted: "#626c78"
  line: "#e3e6ea"
  control-line: "#b8bec6"
  selected: "#edf5f0"
  danger: "#923330"
  danger-bg: "#fcefed"
  warning: "#7b5018"
  warning-bg: "#fbf3df"
  evidence-mark: "#c9e8d4"
typography:
  headline:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "28px"
    fontWeight: 600
    lineHeight: 1.28
    letterSpacing: "-0.035em"
  title:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.28
  body:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  transcript:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.65
  control:
    fontFamily: "IBM Plex Sans, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.35
rounded:
  control: "6px"
  overlay: "12px"
  editor-control: "5px"
spacing:
  compact: "8px"
  control-inline: "13px"
  section: "24px"
  page-gutter: "32px"
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

CQA is a quiet working environment for finding a conversation, reading its source, and checking an assessment. The design gives the transcript and the explanation room to be read together. Alignment, text weight, and a small number of neutral surfaces establish hierarchy.

The user explicitly rejected the earlier pale-green, permanently three-column workspace. Its dark top bar, nested panel shells, and equally prominent controls are an anti-reference. The approved replacement separates the dialogue index from the detailed review and uses a neutral navigation rail. The public Freedom Broker shield remains a brand asset, not a claim of an official component system.

## Colors

Use white for the working surface, neutral gray for navigation and secondary regions, and graphite for text. Green carries primary actions, selection and evidence. Amber and red retain their warning and failure meanings. A high score and the gross-error flag are separate facts.

The CSS custom properties in `frontend/src/styles.css` are the source of the global palette above. Component-local neutral variations support the table and transcript; they do not establish another theme. The Freedom shield is sourced from https://fbroker.kz/images/Logo.svg.

## Typography

Use locally bundled IBM Plex Sans throughout. Page headings are 28px, general content is 14px, controls are 13px, and transcript messages are 16px with 1.65 line height and a maximum measure of 68ch. Transcript text becomes 15px below 700px. Roles use weight to distinguish operator, client, bot and system without a colored bubble around every message.

Use sentence case. IDs, counts, scores and weights keep tabular numerals where appropriate. Small timestamps and source-position numbers are secondary but remain visible. Monospace is reserved for actual paths or code.

## Layout

The desktop shell has a 184px neutral navigation rail and a fluid white work area. The rail narrows to 156px at 1100px and becomes top navigation at 900px. Page gutters are 32px, 24px at 1100px, and 20px at 700px.

The dialogue index owns the main viewport. It contains state filters, search, optional field filters, and a full-width table. Table columns align the excerpt, identifying metadata, channel, language, status, score and gross-error result. Narrow screens convert its rows into a compact list while retaining selection, sorting and primary facts.

Review is a separate route. At desktop size, source and assessment use two areas, with the transcript taking the remaining width beside an assessment area of at least 340px. At 1600px and above the assessment is 440px. Both areas scroll independently inside the viewport. A short header carries dialogue identity, quick queue and previous/next navigation. Below 700px, Conversation and Assessment tabs expose one area at a time; the queue opens on demand.

Checklist editing uses a criterion index beside a single spacious form. On small screens a selector replaces the index. Summary uses flat rows and inline counts. Hash routes retain the current application section or encoded dialogue key and support browser navigation; they do not encode an evaluation snapshot.

## Elevation & Depth

Working surfaces are flat. Light rules separate meaningful regions and data rows. The native modal dialog uses a soft `0 18px 70px #10251c38` shadow and a dim backdrop; dropdowns escape clipping through their existing Radix portal. Do not add shadows to ordinary content sections.

## Shapes

Controls use 6px corners, editor fields 5px and overlays 12px. The table, transcript and forms are not enclosed in rounded cards. A small evidence wash and exact-text mark express active selection. Do not introduce geometric icon tiles or decorative pills to fill empty space.

## Components

- **Navigation** uses neutral active rows on desktop and underlined application tabs on mobile. The skip link moves focus into the current section without changing its route.
- **Dialogue index** keeps actual scope counts and makes multi-selection explicit. Selection exposes the batch action. Searching, filtering and sorting affect the working list; the detail queue follows that same order.
- **Launch dialog** appears when the user requests an assessment. It states the number of target dialogues, lets the user choose a version, and names the configured model. Confirming starts the existing real queue; canceling sends no assessment request.
- **Source and evidence** keep original message numbers. A bot prelude may collapse, but a selected citation reopens its source. Exact quotes or checked ranges are highlighted, and the return action restores focus to the initiating criterion. Source snapshots and current dialogue content retain their existing distinction.
- **Assessment** shows provenance, score and gross-error status separately. Criterion details pair explanation with source controls and an appeal. Original and revised outcomes retain their reason and version context. Version-bound drafts survive application navigation.
- **Checklist editor** saves a new version explicitly. The visible draft is associated with its base version; editing does not rewrite a historical assessment.
- **Submission** has two steps. Parameters prepare the validated server document; the receipt shows its team, model, dataset, path, counts and errors. Download uses that same object. Changing parameters discards the receipt and requires preparing it again.
- **Motion and focus** use brief 150–180ms state transitions, including evidence selection and criterion disclosure. Modal entry is 160ms. Reduced motion removes these transitions and smooth scrolling while preserving selection and focus. Focus uses a visible 2px outline with an offset. No decorative page entrance or invented progress is added.

## Do's and Don'ts

- Do begin with the user's current task and real transcript content.
- Do preserve independent source, assessment, gross-error and reviewer-provenance facts.
- Do keep import, launch, review, appeal, rule editing and export discoverable in their respective contexts.
- Do keep native focus and keyboard behavior intact when changing composition.
- Don't restore the permanently visible queue beside the two review areas.
- Don't recreate the rejected pale-green card framework through new component names.
- Don't turn a library's default appearance into the visual specification.
- Don't invent scores, model confidence, progress percentages or available audio.

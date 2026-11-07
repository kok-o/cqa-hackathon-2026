# CQA

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

QA reviewers and team leads evaluate operator conversations. The hackathon judges also use the prototype to import unseen transcripts and change criteria.

## Product Purpose

Read a conversation in RU, KZ or EN, apply the selected version of a checklist, inspect cited evidence, and resolve an appeal with a reproducible score recalculation.

## Operating Context

Local React/TypeScript/Vite application served by FastAPI, with SQLite persistence. No sign-in is required. The supplied dataset contains 65 conversations; synthetic fixtures are clearly labeled. The official eight-criterion rubric is unavailable, so the starter checklist is explicitly illustrative.

## Capabilities and Constraints

Dataset JSON import, versioned editable criteria, asynchronous real model assessment, exact evidence links, audio playback when available, appeals, revisions, history and export. Unconfigured AI and unresolved criteria must remain visible. No invented model outputs or official scoring claims. Primary selection criterion is accuracy against organizer Ground Truth on ten unseen transcripts; no quality result has yet been measured.

## Product Principles

- Keep the source conversation next to the assessment.
- Make uncertainty and source provenance explicit.
- Preserve prior versions and original scores.
- Make all essential workflows available without developer tools.

## Context source

Confirmed task, repository documentation and parent implementation brief. Visual implementation choices are delegated; this subagent did not run a separate user interview.

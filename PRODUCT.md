# CQA

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

QA reviewers and team leads evaluate operator conversations. The hackathon judges also use the prototype to import unseen transcripts and change criteria.

## Product Purpose

Read a conversation in RU, KZ or EN, apply the selected version of a checklist, inspect cited evidence, and resolve an appeal with a reproducible score recalculation.

## Operating Context

Local React/TypeScript/Vite application served by FastAPI, with SQLite persistence. No sign-in is required. The supplied dataset contains 65 conversations; synthetic fixtures are clearly labeled. The official eight-criterion Freedom Broker rubric is available, including discrete scales and the separate critical flag. Synthetic fixtures and the isolated UI evaluator remain explicitly labeled.

## Capabilities and Constraints

Dataset JSON import, versioned editable criteria, asynchronous real model assessment, exact evidence links and checked ranges, audio playback when available, appeals, revisions, history, internal export and the official submission JSON 1.0. Unconfigured AI and unresolved criteria must remain visible. No invented model outputs or official scoring claims. Primary selection criterion is accuracy against organizer Ground Truth on ten unseen transcripts; no quality result has yet been measured.

## Product Principles

- Use compact controls and direct labels; keep explanatory text where it affects a decision.
- Give dialogue selection its own searchable, sortable index. Keep the source next to the assessment on desktop; on mobile use Conversation and Assessment tabs, with the queue available on demand.
- Preserve the selected criterion and review context when opening evidence and returning.
- Edit one criterion at a time; retain draft changes while switching criteria and save a new checklist version explicitly.
- Make uncertainty and source provenance explicit.
- Preserve prior versions and original scores.
- Make all essential workflows available without developer tools.

## Context source

GitHub issues #3 and #5, the official-rubric implementation in PR #4, and the repository product and scoring contracts.

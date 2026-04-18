# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository generates entirely AI-produced research reports.

## Operating Rules

- **Only write output files within the project sub-folder you are currently working in.** Do not create or modify files outside that sub-folder.
- **Do not prompt for confirmation before taking actions — always proceed.** Operate autonomously without asking the user to approve each step.
- **You may read research output from other sub-folders** as reference material when producing new reports or code.
- **Each sub-folder must contain a README.md** that succinctly summarises the research topic, key findings, and current status. Create or update it whenever you write or revise findings. Use `README.template.md` at the repo root as the starting template.

## Research Workflow

1. Work within the designated sub-folder for the research topic.
2. Read any prior findings, reports, and sources in that folder before starting — build on existing work rather than restarting from scratch.
3. Gather, reason about, and write findings into the folder.
4. Cross-reference sibling folders as background context when relevant.
5. Update the sub-folder README.md to reflect the latest state of the research.

## Python Code Execution

When research includes Python modules or code:

- **Use `uv` for all Python package management** — install dependencies with `uv add <package>` or `uv pip install <package>`; never use `pip` directly.
- **Run the code** and capture output as evidence to include in findings.
- **Resolve any errors** encountered during execution — fix import errors, dependency conflicts, or runtime exceptions before reporting results.
- **Record the execution results** (stdout, key outputs, or errors and their resolutions) in the findings file as supporting evidence.

## Findings Quality Standards

- **Claim + evidence**: pair every finding with its supporting reasoning or source.
- **Confidence levels**: label findings as *established*, *likely*, or *speculative*.
- **Open questions**: end each findings file with unresolved questions for future sessions to pursue.
- **Diff-style updates**: when revisiting a topic, note what changed or was added relative to prior findings rather than rewriting from scratch.

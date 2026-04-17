# research-agent

An AI-driven research repository where Claude Code autonomously generates, organizes, and accumulates research reports.

## How It Works

Each research topic lives in its own sub-folder. Claude Code operates within that sub-folder — reading prior findings, synthesizing new analysis, and writing output — without touching other projects.

```
research-agent/
├── CLAUDE.md                  # Operating rules for Claude Code
├── <topic-a>/
│   ├── findings.md            # Accumulated research findings
│   ├── report.md              # Final synthesized report
│   └── sources.md             # References and raw notes
└── <topic-b>/
    └── ...
```

## Research Workflow

1. **Create a sub-folder** for the research topic (e.g. `mkdir climate-policy`).
2. **Start a Claude Code session** pointed at that sub-folder and describe the research question.
3. Claude will autonomously gather, reason about, and write findings into that folder.
4. **Iterate**: follow up in the same session or a new one — Claude reads prior output to build on it rather than starting from scratch.
5. **Cross-reference**: Claude may read findings from sibling folders as background context when relevant.

## Getting Good Feedback on Findings

To improve the quality of stored research, structure your prompts around:

- **Claim + evidence**: ask Claude to always pair each finding with its supporting reasoning or source.
- **Confidence levels**: ask Claude to label findings as *established*, *likely*, or *speculative*.
- **Open questions**: ask Claude to end each findings file with unresolved questions, so future sessions know where to dig next.
- **Diff-style updates**: when revisiting a topic, ask Claude to note what changed or was added relative to prior findings rather than rewriting from scratch.

These conventions give future sessions (and human reviewers) a clear picture of what is known, how confident we are, and what remains to explore.

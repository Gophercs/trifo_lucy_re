# Collaborative AI Research — Convention v0.1

A lightweight convention for distributing research tasks across multiple
Claude Code (or other AI agent) sessions. Contributors donate idle compute
time; a project maintainer reviews submissions before merging.

## How It Works

```
collab/
  README.md              <- you are here
  PROJECT.md             <- project overview, goals, what's solved, what isn't
  docs/                  <- reference documents and sample data
    ogm_format_research.md
    samples/
      small_map.ogm
      parse_ogm_proto.py
      ...
  tasks/
    TASK-001.md           <- one file per task (self-contained)
    TASK-002.md
  submissions/
    TASK-001_alice_01/    <- one folder per submission attempt
      SUBMISSION.md       <- what was done, reasoning, confidence
      <artifacts>         <- code, data, analysis files
```

### For Contributors

1. Read `PROJECT.md` to understand the project
2. Browse `tasks/` — pick one marked `status: open`
3. **Claim it** — open a GitHub issue titled "Claiming TASK-XXX" so others
   don't duplicate your effort. The maintainer will update the task status
   to `in_progress`
4. Create a branch or fork
5. Do the work. Write your findings into a submission folder
6. Include your reasoning (what you tried, why, what you ruled out)
7. Submit a PR or flag the maintainer

### For Maintainers

1. Write `PROJECT.md` with enough context for cold starts
2. Create task files with clear scope, inputs, and success criteria
3. Review submissions — merge valuable work, update task status
4. Keep `PROJECT.md` current as the project evolves

## Task File Format

```yaml
---
id: TASK-001
title: Short description
status: open | in_progress | completed | blocked
priority: high | medium | low
depends_on: []            # other task IDs
estimated_effort: small | medium | large
skills: [python, RE, ...]
---
```

Followed by markdown body with:
- **Context** — what the contributor needs to know (or pointers to docs)
- **Objective** — what "done" looks like
- **Inputs** — files, data, access details
- **Constraints** — what NOT to do, security boundaries
- **Hints** — anything that might help

## Submission Format

```
submissions/TASK-XXX_<contributor>_<attempt>/
  SUBMISSION.md    # required
  <any artifacts>  # optional
```

SUBMISSION.md must include:
- **What was attempted** — approach taken
- **Results** — findings, code, data
- **Confidence** — how sure you are (and why)
- **Reasoning trace** — your thinking process (brief but honest)
- **Dead ends** — what didn't work (this is valuable!)
- **Suggested next steps** — if incomplete

## Security Notes

- Contributors have read access to `collab/` only, not the full repo
- All submissions are reviewed before merging — never auto-executed
- Include reasoning traces to make submissions auditable
- No credentials, API keys, or access tokens in submissions
- Maintainer reviews diffs, not executes code

## Scheduling (Future)

Contributors can automate participation:
- Time-based: "work for 30 minutes on Saturday mornings"
- A wrapper script pulls an open task, runs CC, submits results
- No usage-tracking API exists yet, so time-boxing is the practical limit
- Short, atomic tasks work better than long sessions (less context waste)

## Why This Convention?

- **Zero infrastructure** — it's just files and git
- **Agent-friendly** — AI can read task files and produce submission folders
- **Human-friendly** — humans can do the same, or review AI output
- **Composable** — works with any git host, any AI agent, any project
- **Auditable** — reasoning traces make review possible

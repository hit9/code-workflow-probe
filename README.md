# code-workflow-probe

Pure-Python repo workflow profile syncer for AI coding tools.

It deterministically reads repo files such as manifests, lockfiles, task runners, CI files, and tool config, then returns an aligned profile of components, package managers, workflows, evidence files, fingerprints, confidence, and execution safety.

It does not use an LLM and does not guess missing workflows.

## Install

```bash
uv tool install code-workflow-probe
```

## Quick Use

```bash
code-workflow-probe sync --root .
code-workflow-probe status --root .
code-workflow-probe edit --root . --changed path/to/file
code-workflow-probe affected --root . --changed path/to/file
```

Default output is concise text for LLM context. Use JSON for structured consumers:

```bash
code-workflow-probe sync --root . --format json
```

Use `--verbose` when you need full evidence and fingerprints.

## Codex Skill

```bash
code-workflow-probe install-skill
```

The installed skill tells Codex to call `sync` at task start, use `edit` / `affected` after file changes, and run `sync` again after editing project or workflow management files such as manifests, lockfiles, CI, task runner, test, lint, format, or build config.

## Safety

Only auto-run workflows that are local, aligned, high confidence, low risk, and marked `safe_auto=true`.

Treat CI-only, candidate, stale, risky, or low-confidence workflows as requiring review.

## License

MIT

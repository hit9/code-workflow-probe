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

Use `sync --changed ...` for incremental cache reuse after known file edits:

```bash
code-workflow-probe sync --root . --changed src/app.py
```

Use `--progress` to print sync progress to stderr, and `--full` to force a full scan.

## Python API

```python
from code_workflow_probe import affected, edit, install_skill, status, sync
```

APIs return concise text by default. Pass `format="json"` to get dictionaries.

```python
sync(root=".", cache_path=None, changed_files=None, write=True, format="text", verbose=False, incremental=True, progress=None)
```

Build an aligned profile and optionally write the cache. With `changed_files`, it can reuse an aligned cache when the changes do not affect profile evidence.

```python
status(root=".", cache_path=None, format="text", verbose=False)
```

Check whether the cached profile still matches current evidence files.

```python
edit(root=".", changed_files=None, cache_path=None, format="text", verbose=False)
```

Notify changed files; resync if the changes affect workflow evidence.

```python
affected(root=".", changed_files=None, cache_path=None, format="text", verbose=False)
```

Map changed files to affected components and suggested local workflows.

```python
install_skill(tool="codex", skills_dir=None, dry_run=False, overwrite=True, format="text", verbose=False)
```

Install the Codex skill.

### API Examples

```python
import code_workflow_probe as cwp

print(cwp.sync("."))

profile = cwp.sync(".", format="json")
if profile["alignment"]["aligned"]:
    print(profile["project"]["components"])

result = cwp.affected(".", ["src/app.py"], format="json")
for workflow in result["suggested_workflows"]:
    if workflow["safe_auto"]:
        print(workflow["cwd"], workflow["command"])
```

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

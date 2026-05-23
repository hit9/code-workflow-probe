# code-workflow-probe

Pure-Python repo workflow profile syncer for AI coding tools.

It deterministically reads repo files such as manifests, lockfiles, task runners, CI files, and tool config, then returns an aligned profile of components, package managers, workflows, evidence files, fingerprints, confidence, and execution safety.

It does not use an LLM and does not guess missing workflows.

## Install

```bash
uv tool install code-workflow-probe
```

## Evidence Model

Detectors parse structured files such as `pyproject.toml`, `package.json`, lockfiles, task runners, and CI config. For example, Python pytest workflow detection treats `pytest.ini`, `setup.cfg` pytest sections, or `[tool.pytest...]` in `pyproject.toml` as high-confidence test config evidence.

Dependencies such as `pytest` or `ruff` can identify the tech stack, but they do not by themselves create a high-confidence workflow command. If only test files are found without pytest config, the pytest command is marked as a lower-confidence candidate.

Current deterministic detector families include JavaScript/TypeScript, Deno, Python, Go, Rust, Java/JVM, Ruby/Bundler, PHP/Composer, .NET, and SwiftPM.

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

## Text Format

The default text format is optimized as bounded context for AI coding tools:

- It starts with alignment so stale profiles are not silently trusted.
- It names project type, tech stack, package manager, component path, and workflow `cwd`.
- It includes concrete local workflow commands for install, test, lint, format, build, and dev when evidence supports them.
- It marks non-obvious execution safety with `candidate`, `risk=...`, `conf=...`, and `ci-only` notes.
- It keeps evidence as a short file preview by default; use `--verbose` for fingerprints and full evidence.

Use `--verbose` when you need full evidence and fingerprints.

Use `sync --changed ...` for incremental cache reuse after known file edits:

```bash
code-workflow-probe sync --root . --changed src/app.py
```

Use `--paths-only` when you want sync to trust only the explicit changed paths plus the existing cache, without discovering the whole repo:

```bash
code-workflow-probe sync --root . --changed pyproject.toml --paths-only
```

Use `--progress` to print sync progress to stderr, and `--full` to force a full scan.

## Status Output

`status` has three text detail levels:

```bash
code-workflow-probe status --root .                    # compact: tech, package managers, workflow commands
code-workflow-probe status --root . --detail standard  # component structure plus workflow commands
code-workflow-probe status --root . --detail full      # full component/workflow/evidence listing
```

Use `--depth N` and `--limit N` to control the structural preview in `standard`:

```bash
code-workflow-probe status --root . --detail standard --depth 2 --limit 20
```

The compact and standard text outputs are designed for AI coding context:

- They include the aligned state, project type, detected tech stack, package managers, and local workflow commands.
- Workflow preview order is `test`, `lint`, `format`, `build`, `install`, then `dev`.
- Workflow lines include `cwd` and `command`, plus candidate/risk/confidence notes when relevant.
- CI-only workflows are counted but not mixed into local command recommendations.

In `standard`, components are shown as a bounded repo structure preview:

- Components are sorted by path ascending.
- `depth` limits how deep component paths can be.
- `limit` caps displayed workflow, component, stale, and evidence previews.
- Hidden counts are reported separately.

`--verbose` is kept as an alias for full status text.

## Python API

```python
from code_workflow_probe import affected, edit, install_skill, status, sync, sync_async
```

APIs return concise text by default. Pass `format="json"` to get dictionaries.

```python
sync(root=".", cache_path=None, changed_files=None, write=True, format="text", verbose=False, incremental=True, paths_only=False, progress=None)
```

Build an aligned profile and optionally write the cache. With `changed_files`, it can reuse an aligned cache when the changes do not affect profile evidence. With `paths_only=True`, it avoids whole-repo discovery and updates only from explicit changed paths plus the existing cache.

```python
sync_async(root=".", cache_path=None, changed_files=None, write=True, format="text", verbose=False, incremental=True, paths_only=False, progress=None, executor=None)
```

Run `sync` in a background thread and return a `concurrent.futures.Future`. If `executor` is omitted, a one-shot thread pool is created and shut down after completion.

```python
status(root=".", cache_path=None, format="text", verbose=False, detail="compact", limit=8, depth=2)
```

Check whether the cached profile still matches current evidence files. Text `detail` can be `compact`, `standard`, or `full`; JSON returns the structured data.

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

future = cwp.sync_async(".", format="json")
profile = future.result(timeout=30)
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

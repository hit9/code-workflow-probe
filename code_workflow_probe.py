#!/usr/bin/env python3
"""code-workflow-probe: deterministic repo workflow profile syncer.

API:
    sync(root=".", cache_path=None, changed_files=None, write=True, format="text", verbose=False)
    status(root=".", cache_path=None, format="text", verbose=False)
    edit(root=".", changed_files=None, cache_path=None, format="text", verbose=False)
    affected(root=".", changed_files=None, cache_path=None, format="text", verbose=False)
    install_skill(tool="codex", skills_dir=None, dry_run=False, overwrite=True, format="text", verbose=False)

CLI:
    python code_workflow_probe.py sync --root .
    python code_workflow_probe.py status --root .
    python code_workflow_probe.py edit --changed path/to/file
    python code_workflow_probe.py affected --changed path/to/file
    python code_workflow_probe.py install-skill
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback.
    tomllib = None  # type: ignore[assignment]


VERSION = "0.1.0"
SCHEMA_VERSION = 1
DEFAULT_CACHE_NAME = ".code-workflow-probe.json"
SKILL_NAME = "code-workflow-probe"

WORKFLOW_KINDS = ("install", "test", "lint", "format", "build", "dev")

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "target",
    "vendor",
    ".gradle",
    ".next",
    ".turbo",
}

COMPONENT_MANIFESTS = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "deno.json",
    "deno.jsonc",
}

PROFILE_FILE_NAMES = COMPONENT_MANIFESTS | {
    ".gitignore",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "bun.lockb",
    "uv.lock",
    "poetry.lock",
    "pdm.lock",
    "Pipfile.lock",
    "go.sum",
    "Cargo.lock",
    "gradlew",
    "gradlew.bat",
    "Makefile",
    "makefile",
    "justfile",
    "Justfile",
    "Taskfile.yml",
    "Taskfile.yaml",
    "tsconfig.json",
    "tsconfig.build.json",
    "jsconfig.json",
    "angular.json",
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.json",
    ".eslintrc.yml",
    ".eslintrc.yaml",
    "prettier.config.js",
    "prettier.config.mjs",
    "prettier.config.cjs",
    "prettier.config.ts",
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.yml",
    ".prettierrc.yaml",
    ".prettierrc.js",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.ts",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "svelte.config.js",
    "nuxt.config.js",
    "nuxt.config.ts",
    "tox.ini",
    "noxfile.py",
    "pytest.ini",
    "ruff.toml",
    ".ruff.toml",
    ".flake8",
    ".pylintrc",
    "mypy.ini",
    ".pre-commit-config.yaml",
    ".pre-commit-config.yml",
    ".golangci.yml",
    ".golangci.yaml",
    "rustfmt.toml",
    ".rustfmt.toml",
    ".gitlab-ci.yml",
    ".gitlab-ci.yaml",
    "Jenkinsfile",
}

SOURCE_EXTENSIONS = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".fs": "fsharp",
    ".swift": "swift",
    ".scala": "scala",
    ".clj": "clojure",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
}

DANGEROUS_WORDS = {
    "clean",
    "deploy",
    "destroy",
    "drop",
    "migrate",
    "publish",
    "release",
    "reset",
    "rollback",
    "terraform apply",
    "kubectl delete",
    "docker push",
    "npm publish",
    "rm -rf",
}


def sync(
    root: str | os.PathLike[str] = ".",
    cache_path: str | os.PathLike[str] | None = None,
    changed_files: Optional[Sequence[str]] = None,
    write: bool = True,
    format: str = "text",
    verbose: bool = False,
) -> Dict[str, Any] | str:
    """Build an aligned workflow profile and optionally write it to cache."""

    root_path = _resolve_root(root)
    cache = _resolve_cache_path(root_path, cache_path)
    builder = _ProfileBuilder(root_path, cache)
    profile = builder.build(changed_files=changed_files)
    if write:
        _write_json(cache, profile)
    return _format_result(profile, format, verbose=verbose)


def status(
    root: str | os.PathLike[str] = ".",
    cache_path: str | os.PathLike[str] | None = None,
    format: str = "text",
    verbose: bool = False,
) -> Dict[str, Any] | str:
    """Return whether the cached profile is aligned with current repo files."""

    root_path = _resolve_root(root)
    cache = _resolve_cache_path(root_path, cache_path)
    cached = _load_json(cache)
    checked_at = _utc_now()

    if cached is None:
        return _format_result({
            "operation": "status",
            "tool": "code-workflow-probe",
            "schema_version": SCHEMA_VERSION,
            "root": str(root_path),
            "cache_path": str(cache),
            "alignment": {
                "aligned": False,
                "reason": "cache_missing",
                "checked_at": checked_at,
                "stale_files": [],
                "new_profile_files": [],
                "removed_profile_files": [],
            },
            "profile": None,
            "warnings": ["Run sync before using workflow conclusions."],
        }, format, verbose=verbose)

    stale = _compare_watch_state(root_path, cache, cached.get("watch", {}))
    aligned = not stale["stale_files"] and not stale["new_profile_files"] and not stale["removed_profile_files"] and not stale["source_summary_changed"]
    reason = "aligned" if aligned else "cache_stale"
    warnings = [] if aligned else ["Cached profile is not aligned; run sync before using workflow conclusions."]

    cached["alignment"] = {
        "aligned": aligned,
        "reason": reason,
        "checked_at": checked_at,
        "stale_files": stale["stale_files"],
        "new_profile_files": stale["new_profile_files"],
        "removed_profile_files": stale["removed_profile_files"],
        "source_summary_changed": stale["source_summary_changed"],
    }

    return _format_result({
        "operation": "status",
        "tool": "code-workflow-probe",
        "schema_version": SCHEMA_VERSION,
        "root": str(root_path),
        "cache_path": str(cache),
        "alignment": cached["alignment"],
        "profile": cached if aligned else None,
        "warnings": warnings,
    }, format, verbose=verbose)


def edit(
    root: str | os.PathLike[str] = ".",
    changed_files: Optional[Sequence[str]] = None,
    cache_path: str | os.PathLike[str] | None = None,
    format: str = "text",
    verbose: bool = False,
) -> Dict[str, Any] | str:
    """Edit hook: update profile when changed files invalidate it."""

    root_path = _resolve_root(root)
    cache = _resolve_cache_path(root_path, cache_path)
    normalized = _normalize_changed_files(root_path, changed_files or [])
    current_status = status(root_path, cache, format="json")
    profile_updated = False

    if not current_status["alignment"]["aligned"]:
        profile = sync(root_path, cache, changed_files=normalized, write=True, format="json")
        profile_updated = True
    elif any(_changed_file_affects_profile(path, current_status["profile"]) for path in normalized):
        profile = sync(root_path, cache, changed_files=normalized, write=True, format="json")
        profile_updated = True
    else:
        profile = current_status["profile"]

    affected_result = _affected_from_profile(root_path, profile, normalized)
    return _format_result({
        "operation": "edit",
        "tool": "code-workflow-probe",
        "schema_version": SCHEMA_VERSION,
        "root": str(root_path),
        "cache_path": str(cache),
        "changed_files": normalized,
        "profile_updated": profile_updated,
        "alignment": profile["alignment"],
        "affected": affected_result["affected"],
        "suggested_workflows": affected_result["suggested_workflows"],
        "profile": profile,
        "warnings": affected_result["warnings"],
    }, format, verbose=verbose)


def affected(
    root: str | os.PathLike[str] = ".",
    changed_files: Optional[Sequence[str]] = None,
    cache_path: str | os.PathLike[str] | None = None,
    format: str = "text",
    verbose: bool = False,
) -> Dict[str, Any] | str:
    """Map changed files to components and relevant local workflows."""

    root_path = _resolve_root(root)
    cache = _resolve_cache_path(root_path, cache_path)
    normalized = _normalize_changed_files(root_path, changed_files or [])
    current_status = status(root_path, cache, format="json")

    if current_status["alignment"]["aligned"]:
        profile = current_status["profile"]
    else:
        profile = sync(root_path, cache, changed_files=normalized, write=True, format="json")

    result = _affected_from_profile(root_path, profile, normalized)
    return _format_result({
        "operation": "affected",
        "tool": "code-workflow-probe",
        "schema_version": SCHEMA_VERSION,
        "root": str(root_path),
        "cache_path": str(cache),
        "changed_files": normalized,
        "alignment": profile["alignment"],
        "affected": result["affected"],
        "suggested_workflows": result["suggested_workflows"],
        "warnings": result["warnings"],
    }, format, verbose=verbose)


def install_skill(
    tool: str = "codex",
    skills_dir: str | os.PathLike[str] | None = None,
    dry_run: bool = False,
    overwrite: bool = True,
    format: str = "text",
    verbose: bool = False,
) -> Dict[str, Any] | str:
    """Install a Codex skill that teaches agents to use code-workflow-probe."""

    if tool != "codex":
        raise ValueError("install_skill currently supports only tool='codex'")

    base_dir = _resolve_codex_skills_dir(skills_dir)
    skill_dir = base_dir / SKILL_NAME
    skill_path = skill_dir / "SKILL.md"
    content = _codex_skill_markdown()
    exists = skill_path.exists()

    warnings = []
    installed = False
    if exists and not overwrite:
        warnings.append("Skill already exists and overwrite is disabled.")
    elif not dry_run:
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(content, encoding="utf-8")
        installed = True

    result = {
        "operation": "install-skill",
        "tool": "code-workflow-probe",
        "schema_version": SCHEMA_VERSION,
        "target": "codex",
        "skill_name": SKILL_NAME,
        "skills_dir": str(base_dir),
        "skill_path": str(skill_path),
        "installed": installed,
        "dry_run": dry_run,
        "overwritten": installed and exists,
        "content": content if dry_run else None,
        "warnings": warnings,
    }
    return _format_result(result, format, verbose=verbose)


class _EvidenceStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._items: Dict[str, Dict[str, Any]] = {}

    def add(self, rel_path: str, role: str) -> str:
        rel = _clean_rel(rel_path)
        if not rel:
            return "."
        path = self.root / rel
        item = self._items.get(rel)
        if item is None:
            item = _fingerprint(path)
            item["path"] = rel
            item["roles"] = []
            self._items[rel] = item
        if role not in item["roles"]:
            item["roles"].append(role)
            item["roles"].sort()
        return rel

    def add_many(self, rel_paths: Iterable[str], role: str) -> List[str]:
        return [self.add(path, role) for path in rel_paths]

    def as_dict(self) -> Dict[str, Dict[str, Any]]:
        return {path: dict(value) for path, value in sorted(self._items.items())}


class _ProfileBuilder:
    def __init__(self, root: Path, cache_path: Path) -> None:
        self.root = root
        self.cache_path = cache_path
        self.ignore = _GitIgnore(root)
        self.evidence = _EvidenceStore(root)
        self.warnings: List[str] = []

    def build(self, changed_files: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        profile_files = _discover_profile_files(self.root, self.cache_path)
        self.evidence.add_many(profile_files, "profile_watch")
        source_summary = _source_summary(self.root)
        component_roots = self._component_roots(profile_files, source_summary)
        components = [self._build_component(path, component_roots) for path in component_roots]
        repo_workflows = self._repo_workflows(components)
        ci_workflows = self._ci_workflows(profile_files)
        technologies = _merge_facts(component.get("languages", []) + component.get("frameworks", []) for component in components)
        package_managers = _merge_facts(
            [component["package_manager"]] for component in components if component.get("package_manager")
        )
        project_type = _project_type(components)
        watch_files = {
            path: _fingerprint_with_rel(self.root, path)
            for path in sorted(set(profile_files) | set(self.evidence.as_dict().keys()))
            if path != _rel_to_root(self.root, self.cache_path)
        }

        profile = {
            "schema_version": SCHEMA_VERSION,
            "tool": "code-workflow-probe",
            "version": VERSION,
            "root": str(self.root),
            "cache_path": str(self.cache_path),
            "generated_at": _utc_now(),
            "alignment": {
                "aligned": True,
                "reason": "synced",
                "checked_at": _utc_now(),
                "stale_files": [],
                "new_profile_files": [],
                "removed_profile_files": [],
                "source_summary_changed": False,
            },
            "project": {
                "type": project_type,
                "components": components,
                "technologies": technologies,
                "package_managers": package_managers,
                "repo_workflows": repo_workflows,
                "ci_workflows": ci_workflows,
            },
            "evidence_files": self.evidence.as_dict(),
            "watch": {
                "files": watch_files,
                "source_summary": source_summary,
            },
            "changed_files": _normalize_changed_files(self.root, changed_files or []),
            "warnings": self.warnings,
        }
        return profile

    def _component_roots(self, profile_files: Sequence[str], source_summary: Dict[str, Any]) -> List[str]:
        roots: Set[str] = set()
        for rel in profile_files:
            name = Path(rel).name
            if name in COMPONENT_MANIFESTS:
                roots.add(_dirname_rel(rel))

        if not roots and source_summary["languages"]:
            roots.add(".")
            for sample in source_summary.get("samples", []):
                self.evidence.add(sample, "source_language_sample")

        if not roots and any(Path(path).name in {"Makefile", "makefile", "justfile", "Justfile"} for path in profile_files):
            roots.add(".")

        return sorted(roots, key=lambda item: (item.count("/"), item))

    def _build_component(self, path: str, all_roots: Sequence[str]) -> Dict[str, Any]:
        component_dir = self.root if path == "." else self.root / path
        evidence: List[str] = []
        languages: List[Dict[str, Any]] = []
        frameworks: List[Dict[str, Any]] = []
        workflows: List[Dict[str, Any]] = []
        package_manager: Optional[Dict[str, Any]] = None

        manifests = self._existing_names(path, COMPONENT_MANIFESTS)
        for name in manifests:
            evidence.append(self.evidence.add(_join_rel(path, name), "component_manifest"))

        scope = _component_scope(path, all_roots)

        if self._has_file(path, "package.json"):
            js = self._javascript_component(path, scope, all_roots)
            languages.extend(js["languages"])
            frameworks.extend(js["frameworks"])
            package_manager = js["package_manager"]
            workflows.extend(js["workflows"])

        if self._has_any(path, {"pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "setup.cfg", "Pipfile"}):
            py = self._python_component(path, scope)
            languages.extend(py["languages"])
            frameworks.extend(py["frameworks"])
            package_manager = package_manager or py["package_manager"]
            workflows.extend(py["workflows"])

        if self._has_file(path, "go.mod"):
            go = self._go_component(path, scope)
            languages.extend(go["languages"])
            package_manager = package_manager or go["package_manager"]
            workflows.extend(go["workflows"])

        if self._has_file(path, "Cargo.toml"):
            rust = self._rust_component(path, scope)
            languages.extend(rust["languages"])
            package_manager = package_manager or rust["package_manager"]
            workflows.extend(rust["workflows"])

        if self._has_any(path, {"pom.xml", "build.gradle", "build.gradle.kts"}):
            java = self._java_component(path, scope)
            languages.extend(java["languages"])
            package_manager = package_manager or java["package_manager"]
            workflows.extend(java["workflows"])

        if not languages:
            fallback = self._source_fallback_component(path, all_roots)
            languages.extend(fallback["languages"])
            evidence.extend(fallback["evidence"])

        workflows.extend(self._task_runner_workflows(path, scope))
        workflows = _dedupe_workflows(workflows)
        component_type = _component_type(languages)
        component_evidence = set(evidence)
        for fact in languages + frameworks:
            component_evidence.update(fact.get("evidence", []))
        if package_manager:
            component_evidence.update(package_manager.get("evidence", []))
        for workflow in workflows:
            component_evidence.update(workflow.get("evidence", []))

        return {
            "id": "root" if path == "." else path,
            "path": path,
            "type": component_type,
            "languages": _dedupe_facts(languages),
            "frameworks": _dedupe_facts(frameworks),
            "package_manager": package_manager,
            "workflows": workflows,
            "evidence": sorted(component_evidence),
            "warnings": [],
        }

    def _has_file(self, component_path: str, name: str) -> bool:
        rel = _join_rel(component_path, name)
        return _visible_file(self.root, self.ignore, rel)

    def _existing_names(self, component_path: str, names: Iterable[str]) -> List[str]:
        return sorted(name for name in names if self._has_file(component_path, name))

    def _has_any(self, component_path: str, names: Iterable[str]) -> bool:
        return any(self._has_file(component_path, name) for name in names)

    def _javascript_component(self, path: str, scope: str, all_roots: Sequence[str]) -> Dict[str, Any]:
        rel_package = _join_rel(path, "package.json")
        package_path = self.root / rel_package
        package = _load_json(package_path) or {}
        evidence = [self.evidence.add(rel_package, "javascript_manifest")]
        dependencies = _package_dependencies(package)
        ts_evidence = self._typescript_evidence(path, all_roots, dependencies, rel_package)
        language = "typescript" if ts_evidence else "javascript"
        language_evidence = evidence + ts_evidence
        languages = [_fact(language, 0.9, language_evidence, "package.json and TypeScript evidence" if ts_evidence else "package.json")]
        frameworks = [_fact(name, 0.8, evidence, "package dependency") for name in _js_frameworks(dependencies)]
        pm = self._js_package_manager(path, package)
        workflows = [self._workflow("install", _js_install_command(pm), path, scope, evidence + pm["evidence"], pm["confidence"], "local", True)]

        scripts = package.get("scripts", {}) if isinstance(package.get("scripts"), dict) else {}
        for kind in WORKFLOW_KINDS:
            if kind not in scripts:
                continue
            command = _js_script_command(pm["name"], kind)
            script_text = str(scripts.get(kind, ""))
            risk = _risk_for_command(kind, script_text)
            workflows.append(
                self._workflow(
                    kind,
                    command,
                    path,
                    scope,
                    evidence,
                    "high",
                    "local",
                    recommended=True,
                    risk=risk,
                    reason=f"package.json script '{kind}'",
                    command_preview=script_text,
                )
            )

        return {
            "languages": languages,
            "frameworks": frameworks,
            "package_manager": pm,
            "workflows": workflows,
        }

    def _js_package_manager(self, path: str, package: Dict[str, Any]) -> Dict[str, Any]:
        component_dir = self.root / path
        candidates = [
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
            ("bun.lock", "bun"),
            ("bun.lockb", "bun"),
            ("package-lock.json", "npm"),
            ("npm-shrinkwrap.json", "npm"),
        ]
        for filename, name in candidates:
            if self._has_file(path, filename):
                evidence = [self.evidence.add(_join_rel(path, "package.json"), "package_manager")]
                evidence.append(self.evidence.add(_join_rel(path, filename), "package_manager_lockfile"))
                return _package_manager(name, _pm_executable(name), 0.95, evidence)

        package_manager = package.get("packageManager")
        if isinstance(package_manager, str) and "@" in package_manager:
            name = package_manager.split("@", 1)[0]
            if name in {"npm", "pnpm", "yarn", "bun"}:
                evidence = [self.evidence.add(_join_rel(path, "package.json"), "package_manager")]
                return _package_manager(name, _pm_executable(name), 0.85, evidence)

        evidence = [self.evidence.add(_join_rel(path, "package.json"), "package_manager")]
        return _package_manager("npm", "npm", 0.6, evidence, warnings=["No JS lockfile or packageManager field; npm is only a candidate."])

    def _python_component(self, path: str, scope: str) -> Dict[str, Any]:
        component_dir = self.root / path
        pyproject = _load_toml(component_dir / "pyproject.toml")
        evidence = [
            self.evidence.add(_join_rel(path, name), "python_manifest")
            for name in ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "setup.cfg", "Pipfile")
            if self._has_file(path, name)
        ]
        languages = [_fact("python", 0.9, evidence, "python manifest")]
        visible_requirement_files = [name for name in ("requirements.txt", "requirements-dev.txt") if self._has_file(path, name)]
        frameworks = [
            _fact(name, 0.8, evidence, "python dependency")
            for name in _python_frameworks(component_dir, pyproject, visible_requirement_files)
        ]
        pm = self._python_package_manager(path, pyproject)
        workflows: List[Dict[str, Any]] = []

        install = _python_install_command(pm)
        if install:
            workflows.append(self._workflow("install", install, path, scope, pm["evidence"], pm["confidence"], "local", True))

        pytest_evidence = self._pytest_evidence(path, pyproject)
        if pytest_evidence:
            workflows.append(
                self._workflow(
                    "test",
                    "python -m pytest",
                    path,
                    scope,
                    pytest_evidence,
                    "high",
                    "local",
                    recommended=True,
                )
            )
        elif _has_test_sample(self.root, path, self.ignore):
            samples = self.evidence.add_many(_test_samples(self.root, path, self.ignore), "python_test_sample")
            workflows.append(
                self._workflow(
                    "test",
                    "python -m pytest",
                    path,
                    scope,
                    samples,
                    "medium",
                    "local",
                    recommended=False,
                    reason="test files exist but no pytest configuration was found",
                )
            )

        if self._has_file(path, "tox.ini"):
            workflows.append(self._workflow("test", "tox", path, scope, [self.evidence.add(_join_rel(path, "tox.ini"), "test_runner")], "high", "local", True))
        if self._has_file(path, "noxfile.py"):
            workflows.append(self._workflow("test", "nox", path, scope, [self.evidence.add(_join_rel(path, "noxfile.py"), "test_runner")], "high", "local", True))

        ruff = self._ruff_evidence(path, pyproject)
        if ruff:
            workflows.append(self._workflow("lint", "ruff check .", path, scope, ruff, "high", "local", True))
            workflows.append(
                self._workflow(
                    "format",
                    "ruff format .",
                    path,
                    scope,
                    ruff,
                    "medium",
                    "local",
                    recommended=False,
                    reason="ruff is configured; formatter availability may depend on ruff version",
                )
            )

        black = self._black_evidence(path, pyproject)
        if black:
            workflows.append(self._workflow("format", "black .", path, scope, black, "high", "local", True))

        if self._has_file(path, ".flake8") or (self._has_file(path, "setup.cfg") and _setup_cfg_has_section(component_dir / "setup.cfg", "flake8")):
            flake8_evidence = [
                self.evidence.add(_join_rel(path, name), "lint_config")
                for name in (".flake8", "setup.cfg")
                if self._has_file(path, name)
            ]
            workflows.append(self._workflow("lint", "flake8 .", path, scope, flake8_evidence, "high", "local", True))

        if evidence and self._has_file(path, "pyproject.toml"):
            workflows.append(
                self._workflow(
                    "build",
                    "python -m build",
                    path,
                    scope,
                    [self.evidence.add(_join_rel(path, "pyproject.toml"), "build_config")],
                    "medium",
                    "local",
                    recommended=False,
                )
            )

        return {
            "languages": languages,
            "frameworks": frameworks,
            "package_manager": pm,
            "workflows": workflows,
        }

    def _typescript_evidence(
        self,
        path: str,
        all_roots: Sequence[str],
        dependencies: Set[str],
        package_json: str,
    ) -> List[str]:
        component_dir = self.root if path == "." else self.root / path
        evidence = []
        if "typescript" in dependencies:
            evidence.append(self.evidence.add(package_json, "typescript_dependency"))
            return evidence

        for name in ("tsconfig.json", "tsconfig.build.json"):
            if self._has_file(path, name):
                evidence.append(self.evidence.add(_join_rel(path, name), "typescript_config"))
        if evidence:
            return evidence

        ignored_roots = [
            candidate
            for candidate in all_roots
            if candidate != "." and candidate != path and _is_under(candidate, path)
        ]
        for file_path in _walk_files(component_dir, self.root, self.ignore):
            rel = _rel_to_root(self.root, file_path)
            if any(_is_under(rel, ignored) for ignored in ignored_roots):
                continue
            if file_path.suffix in {".ts", ".tsx"}:
                evidence.append(self.evidence.add(rel, "source_language_sample"))
                if len(evidence) >= 3:
                    break
        return evidence

    def _python_package_manager(self, path: str, pyproject: Dict[str, Any]) -> Dict[str, Any]:
        component_dir = self.root / path
        checks = [
            ("uv.lock", "uv", "uv"),
            ("poetry.lock", "poetry", "poetry"),
            ("pdm.lock", "pdm", "pdm"),
            ("Pipfile.lock", "pipenv", "pipenv"),
            ("Pipfile", "pipenv", "pipenv"),
        ]
        for filename, name, command in checks:
            if self._has_file(path, filename):
                evidence = [self.evidence.add(_join_rel(path, filename), "package_manager_lockfile")]
                if self._has_file(path, "pyproject.toml"):
                    evidence.append(self.evidence.add(_join_rel(path, "pyproject.toml"), "package_manager"))
                return _package_manager(name, command, 0.95, evidence)

        tool = pyproject.get("tool", {}) if isinstance(pyproject, dict) else {}
        if isinstance(tool, dict):
            if "poetry" in tool:
                evidence = [self.evidence.add(_join_rel(path, "pyproject.toml"), "package_manager")]
                return _package_manager("poetry", "poetry", 0.9, evidence)
            if "pdm" in tool:
                evidence = [self.evidence.add(_join_rel(path, "pyproject.toml"), "package_manager")]
                return _package_manager("pdm", "pdm", 0.9, evidence)

        if self._has_file(path, "requirements.txt") or self._has_file(path, "requirements-dev.txt"):
            evidence = [
                self.evidence.add(_join_rel(path, name), "package_manager")
                for name in ("requirements.txt", "requirements-dev.txt")
                if self._has_file(path, name)
            ]
            return _package_manager("pip", "python -m pip", 0.85, evidence)

        evidence = [
            self.evidence.add(_join_rel(path, name), "package_manager")
            for name in ("pyproject.toml", "setup.py", "setup.cfg")
            if self._has_file(path, name)
        ]
        return _package_manager("pip", "python -m pip", 0.6, evidence, warnings=["No Python lockfile; pip workflow is a candidate."])

    def _go_component(self, path: str, scope: str) -> Dict[str, Any]:
        evidence = [self.evidence.add(_join_rel(path, "go.mod"), "go_manifest")]
        if self._has_file(path, "go.sum"):
            evidence.append(self.evidence.add(_join_rel(path, "go.sum"), "go_lockfile"))
        workflows = [
            self._workflow("install", "go mod download", path, scope, evidence, "high", "local", True),
            self._workflow("test", "go test ./...", path, scope, evidence, "high", "local", True),
            self._workflow("build", "go build ./...", path, scope, evidence, "medium", "local", False),
        ]
        lint_config = _join_rel(path, ".golangci.yml")
        if _visible_file(self.root, self.ignore, lint_config):
            workflows.append(self._workflow("lint", "golangci-lint run", path, scope, [self.evidence.add(lint_config, "lint_config")], "high", "local", True))
        return {
            "languages": [_fact("go", 0.95, evidence, "go.mod")],
            "package_manager": _package_manager("go modules", "go", 0.95, evidence),
            "workflows": workflows,
        }

    def _rust_component(self, path: str, scope: str) -> Dict[str, Any]:
        evidence = [self.evidence.add(_join_rel(path, "Cargo.toml"), "rust_manifest")]
        if self._has_file(path, "Cargo.lock"):
            evidence.append(self.evidence.add(_join_rel(path, "Cargo.lock"), "rust_lockfile"))
        return {
            "languages": [_fact("rust", 0.95, evidence, "Cargo.toml")],
            "package_manager": _package_manager("cargo", "cargo", 0.95, evidence),
            "workflows": [
                self._workflow("install", "cargo fetch", path, scope, evidence, "high", "local", True),
                self._workflow("test", "cargo test", path, scope, evidence, "high", "local", True),
                self._workflow("build", "cargo build", path, scope, evidence, "medium", "local", False),
                self._workflow("format", "cargo fmt", path, scope, evidence, "medium", "local", False),
            ],
        }

    def _java_component(self, path: str, scope: str) -> Dict[str, Any]:
        component_dir = self.root / path
        if self._has_file(path, "pom.xml"):
            evidence = [self.evidence.add(_join_rel(path, "pom.xml"), "java_manifest")]
            pm = _package_manager("maven", "mvn", 0.95, evidence)
            workflows = [
                self._workflow("test", "mvn test", path, scope, evidence, "high", "local", True),
                self._workflow("build", "mvn package", path, scope, evidence, "medium", "local", False),
            ]
        else:
            build_file = "build.gradle" if self._has_file(path, "build.gradle") else "build.gradle.kts"
            evidence = [self.evidence.add(_join_rel(path, build_file), "java_manifest")]
            gradle = "./gradlew" if self._has_file(path, "gradlew") else "gradle"
            if self._has_file(path, "gradlew"):
                evidence.append(self.evidence.add(_join_rel(path, "gradlew"), "task_runner"))
            pm = _package_manager("gradle", gradle, 0.9, evidence)
            workflows = [
                self._workflow("test", f"{gradle} test", path, scope, evidence, "high", "local", True),
                self._workflow("build", f"{gradle} build", path, scope, evidence, "medium", "local", False),
            ]
        return {
            "languages": [_fact("java", 0.85, evidence, "java build manifest")],
            "package_manager": pm,
            "workflows": workflows,
        }

    def _source_fallback_component(self, path: str, all_roots: Sequence[str]) -> Dict[str, Any]:
        component_dir = self.root if path == "." else self.root / path
        ignored_roots = [root for root in all_roots if root != "." and root != path and _is_under(root, path)]
        samples: Dict[str, List[str]] = defaultdict(list)
        for file_path in _walk_files(component_dir, self.root, self.ignore):
            rel = _rel_to_root(self.root, file_path)
            if any(_is_under(rel, ignored) for ignored in ignored_roots):
                continue
            language = SOURCE_EXTENSIONS.get(file_path.suffix)
            if language and len(samples[language]) < 3:
                samples[language].append(rel)

        evidence: List[str] = []
        facts: List[Dict[str, Any]] = []
        for language, paths in sorted(samples.items()):
            added = self.evidence.add_many(paths, "source_language_sample")
            evidence.extend(added)
            facts.append(_fact(language, 0.55, added, "source file extension sample"))
        return {"languages": facts, "evidence": evidence}

    def _task_runner_workflows(self, path: str, scope: str) -> List[Dict[str, Any]]:
        component_dir = self.root if path == "." else self.root / path
        workflows: List[Dict[str, Any]] = []
        task_files = [
            ("Makefile", "make"),
            ("makefile", "make"),
            ("justfile", "just"),
            ("Justfile", "just"),
        ]
        for filename, runner in task_files:
            task_file = component_dir / filename
            if not self._has_file(path, filename):
                continue
            rel = self.evidence.add(_join_rel(path, filename), "task_runner")
            targets = _parse_task_targets(task_file, runner)
            for kind in WORKFLOW_KINDS:
                if kind not in targets:
                    continue
                command = f"{runner} {kind}"
                recipe = targets[kind]
                workflows.append(
                    self._workflow(
                        kind,
                        command,
                        path,
                        scope,
                        [rel],
                        "high",
                        "local",
                        recommended=True,
                        risk=_risk_for_command(kind, recipe),
                        reason=f"{filename} target '{kind}'",
                        command_preview=recipe,
                    )
                )
        return workflows

    def _repo_workflows(self, components: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(components) <= 1:
            return []
        repo_workflows: List[Dict[str, Any]] = []
        for component in components:
            if component["path"] != ".":
                continue
            for workflow in component.get("workflows", []):
                workflow = dict(workflow)
                workflow["scope"] = "repo"
                workflow["component_id"] = None
                repo_workflows.append(workflow)
        return repo_workflows

    def _ci_workflows(self, profile_files: Sequence[str]) -> List[Dict[str, Any]]:
        workflows: List[Dict[str, Any]] = []
        for rel in sorted(profile_files):
            if not _is_ci_file(rel):
                continue
            evidence = [self.evidence.add(rel, "ci_workflow")]
            commands = _extract_ci_commands(self.root / rel)
            if not commands:
                workflows.append(
                    self._workflow(
                        "ci",
                        None,
                        ".",
                        "repo",
                        evidence,
                        "medium",
                        "ci",
                        recommended=False,
                        risk="high",
                        ci_only=True,
                        reason="CI workflow file found; commands were not statically extracted",
                    )
                )
            for command in commands:
                workflows.append(
                    self._workflow(
                        _classify_workflow_kind(command),
                        command,
                        ".",
                        "repo",
                        evidence,
                        "medium",
                        "ci",
                        recommended=False,
                        risk=_risk_for_command("ci", command),
                        ci_only=True,
                        reason="command extracted from CI workflow; not a local workflow recommendation",
                        command_preview=command,
                    )
                )
        return workflows

    def _pytest_evidence(self, path: str, pyproject: Dict[str, Any]) -> List[str]:
        component_dir = self.root / path
        evidence = []
        if self._has_file(path, "pytest.ini"):
            evidence.append(self.evidence.add(_join_rel(path, "pytest.ini"), "test_config"))
        if self._has_file(path, "setup.cfg") and _setup_cfg_has_section(component_dir / "setup.cfg", "tool:pytest"):
            evidence.append(self.evidence.add(_join_rel(path, "setup.cfg"), "test_config"))
        tool = pyproject.get("tool", {}) if isinstance(pyproject, dict) else {}
        if isinstance(tool, dict) and "pytest" in tool and self._has_file(path, "pyproject.toml"):
            evidence.append(self.evidence.add(_join_rel(path, "pyproject.toml"), "test_config"))
        return evidence

    def _ruff_evidence(self, path: str, pyproject: Dict[str, Any]) -> List[str]:
        component_dir = self.root / path
        evidence = []
        for name in ("ruff.toml", ".ruff.toml"):
            if self._has_file(path, name):
                evidence.append(self.evidence.add(_join_rel(path, name), "lint_config"))
        tool = pyproject.get("tool", {}) if isinstance(pyproject, dict) else {}
        if isinstance(tool, dict) and "ruff" in tool and self._has_file(path, "pyproject.toml"):
            evidence.append(self.evidence.add(_join_rel(path, "pyproject.toml"), "lint_config"))
        return evidence

    def _black_evidence(self, path: str, pyproject: Dict[str, Any]) -> List[str]:
        component_dir = self.root / path
        tool = pyproject.get("tool", {}) if isinstance(pyproject, dict) else {}
        if isinstance(tool, dict) and "black" in tool and self._has_file(path, "pyproject.toml"):
            return [self.evidence.add(_join_rel(path, "pyproject.toml"), "format_config")]
        return []

    def _workflow(
        self,
        kind: str,
        command: Optional[str],
        cwd: str,
        scope: str,
        evidence: Sequence[str],
        confidence: str,
        source: str,
        recommended: bool,
        risk: Optional[str] = None,
        ci_only: bool = False,
        reason: str = "",
        command_preview: Optional[str] = None,
    ) -> Dict[str, Any]:
        risk_level = risk or _risk_for_command(kind, command or "")
        cwd_known = bool(cwd)
        candidate = not recommended
        safe_auto = (
            source == "local"
            and not ci_only
            and cwd_known
            and confidence == "high"
            and risk_level == "low"
            and recommended
            and kind in {"test", "lint"}
        )
        warnings = []
        if ci_only:
            warnings.append("CI-only workflow; do not execute as a local command without review.")
        if not cwd_known:
            warnings.append("cwd is not known; command is not recommended for execution.")
        if risk_level != "low":
            warnings.append("Workflow is not low risk; confirm before execution.")
        if confidence != "high":
            warnings.append("Workflow confidence is not high.")
        if candidate:
            warnings.append("Workflow is a candidate, not a recommendation.")

        return {
            "kind": kind,
            "command": command,
            "cwd": cwd,
            "scope": scope,
            "source": source,
            "evidence": sorted(set(evidence)),
            "confidence": confidence,
            "confidence_score": _confidence_score(confidence),
            "risk": risk_level,
            "safe_auto": safe_auto,
            "candidate": candidate,
            "recommended": recommended,
            "needs_confirmation": not safe_auto,
            "ci_only": ci_only,
            "reason": reason,
            "command_preview": command_preview,
            "warnings": warnings,
        }


def _affected_from_profile(root: Path, profile: Dict[str, Any], changed_files: Sequence[str]) -> Dict[str, Any]:
    components = profile.get("project", {}).get("components", [])
    affected_items = []
    component_ids: Set[str] = set()
    suggested: Dict[str, Dict[str, Any]] = {}

    for changed in changed_files:
        component = _match_component(components, changed)
        profile_affecting = _is_profile_file(changed) or changed in profile.get("watch", {}).get("files", {})
        item = {
            "file": changed,
            "component_id": component.get("id") if component else None,
            "component_path": component.get("path") if component else None,
            "profile_affecting": profile_affecting,
            "reason": "profile evidence/config file" if profile_affecting else "matched by component path",
        }
        affected_items.append(item)
        if component:
            component_ids.add(component["id"])
            for workflow in component.get("workflows", []):
                if workflow.get("source") != "local":
                    continue
                if workflow.get("kind") not in {"test", "lint", "build"}:
                    continue
                key = f"{component['id']}:{workflow['kind']}:{workflow.get('command')}"
                suggested[key] = dict(workflow, component_id=component["id"])

    warnings = []
    if not profile.get("alignment", {}).get("aligned"):
        warnings.append("Profile is not aligned; suggested workflows must not be executed.")

    return {
        "affected": {
            "components": sorted(component_ids),
            "files": affected_items,
        },
        "suggested_workflows": list(suggested.values()),
        "warnings": warnings,
    }


def _compare_watch_state(root: Path, cache_path: Path, watch: Dict[str, Any]) -> Dict[str, Any]:
    cached_files = watch.get("files", {}) if isinstance(watch, dict) else {}
    current_profile_files = _discover_profile_files(root, cache_path)
    current_source_summary = _source_summary(root)
    current_file_set = set(current_profile_files) | set(cached_files.keys())
    ignore = _GitIgnore(root)
    current_files = {
        rel: _fingerprint_with_rel(root, rel)
        for rel in sorted(current_file_set)
        if rel != _rel_to_root(root, cache_path) and _visible_file(root, ignore, rel)
    }
    cached_file_set = set(cached_files.keys())
    current_existing_set = set(current_files.keys())
    stale_files = []
    for rel in sorted(cached_file_set & current_existing_set):
        cached_fp = cached_files.get(rel, {})
        current_fp = current_files.get(rel, {})
        if cached_fp.get("sha256") != current_fp.get("sha256") or cached_fp.get("size") != current_fp.get("size"):
            stale_files.append(rel)
    cached_source_summary = watch.get("source_summary", {})
    source_summary_changed = current_source_summary.get("languages") != cached_source_summary.get("languages")
    return {
        "stale_files": stale_files,
        "new_profile_files": sorted(set(current_profile_files) - cached_file_set),
        "removed_profile_files": sorted(cached_file_set - current_existing_set),
        "source_summary_changed": source_summary_changed,
    }


def _changed_file_affects_profile(path: str, profile: Dict[str, Any]) -> bool:
    return _is_profile_file(path) or path in profile.get("watch", {}).get("files", {})


def _discover_profile_files(root: Path, cache_path: Path) -> List[str]:
    cache_rel = _rel_to_root(root, cache_path)
    files = []
    for file_path in _walk_files(root):
        rel = _rel_to_root(root, file_path)
        if rel == cache_rel:
            continue
        if _is_profile_file(rel):
            files.append(rel)
    return sorted(set(files))


def _source_summary(root: Path) -> Dict[str, Any]:
    counts: Dict[str, int] = defaultdict(int)
    samples: Dict[str, List[str]] = defaultdict(list)
    for file_path in _walk_files(root):
        language = SOURCE_EXTENSIONS.get(file_path.suffix)
        if not language:
            continue
        rel = _rel_to_root(root, file_path)
        counts[language] += 1
        if len(samples[language]) < 3:
            samples[language].append(rel)
    return {
        "languages": sorted(counts.keys()),
        "language_counts": dict(sorted(counts.items())),
        "samples": [sample for _, values in sorted(samples.items()) for sample in values],
    }


class _GitIgnore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.rules = _load_gitignore_rules(root / ".gitignore")
        self.has_negation = any(rule["negated"] for rule in self.rules)

    def ignored(self, rel_path: str, is_dir: bool) -> bool:
        rel = _clean_rel(rel_path)
        if rel == ".gitignore":
            return False
        ignored = False
        for rule in self.rules:
            if _gitignore_rule_matches(rule, rel, is_dir):
                ignored = not rule["negated"]
        return ignored


def _load_gitignore_rules(path: Path) -> List[Dict[str, Any]]:
    text = _read_text(path)
    rules = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        line = line.strip()
        if not line:
            continue
        anchored = line.startswith("/")
        if anchored:
            line = line.lstrip("/")
        directory_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        rules.append(
            {
                "pattern": line,
                "negated": negated,
                "anchored": anchored,
                "directory_only": directory_only,
                "has_slash": "/" in line,
            }
        )
    return rules


def _gitignore_rule_matches(rule: Dict[str, Any], rel_path: str, is_dir: bool) -> bool:
    pattern = rule["pattern"]
    if rule["directory_only"] and not is_dir:
        parent_parts = rel_path.split("/")[:-1]
        parents = ["/".join(parent_parts[:index]) for index in range(1, len(parent_parts) + 1)]
        return any(_gitignore_rule_matches(rule, parent, is_dir=True) for parent in parents)

    if rule["anchored"] or rule["has_slash"]:
        return rel_path == pattern or fnmatch.fnmatchcase(rel_path, pattern)

    parts = rel_path.split("/")
    if is_dir:
        return any(part == pattern or fnmatch.fnmatchcase(part, pattern) for part in parts)
    return fnmatch.fnmatchcase(parts[-1], pattern)


def _walk_files(root: Path, repo_root: Optional[Path] = None, ignore: Optional[_GitIgnore] = None) -> Iterable[Path]:
    if not root.exists():
        return []
    repo = (repo_root or root).resolve()
    matcher = ignore or _GitIgnore(repo)
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        kept_dirs = []
        for name in dirs:
            path = current_path / name
            rel = _rel_to_root(repo, path)
            if name in IGNORED_DIRS or (matcher.ignored(rel, is_dir=True) and not matcher.has_negation):
                continue
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for filename in files:
            path = current_path / filename
            rel = _rel_to_root(repo, path)
            if matcher.ignored(rel, is_dir=False):
                continue
            yield path


def _is_profile_file(rel_path: str) -> bool:
    rel = _clean_rel(rel_path)
    name = Path(rel).name
    if name in PROFILE_FILE_NAMES:
        return True
    if rel.startswith(".github/workflows/") and Path(rel).suffix in {".yml", ".yaml"}:
        return True
    if rel == ".circleci/config.yml":
        return True
    return False


def _is_ci_file(rel_path: str) -> bool:
    rel = _clean_rel(rel_path)
    return (
        rel.startswith(".github/workflows/")
        or rel in {".gitlab-ci.yml", ".gitlab-ci.yaml", ".circleci/config.yml", "Jenkinsfile"}
    )


def _extract_ci_commands(path: Path) -> List[str]:
    text = _read_text(path)
    if not text:
        return []
    commands = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("run:"):
            command = line.split(":", 1)[1].strip().strip("'\"")
            if command and command not in {"|", ">"}:
                commands.append(command)
        elif line.startswith("- run:"):
            command = line.split(":", 1)[1].strip().strip("'\"")
            if command and command not in {"|", ">"}:
                commands.append(command)
        elif re.match(r"^script:\s*.+", line):
            command = line.split(":", 1)[1].strip().strip("'\"")
            if command and command not in {"|", ">"}:
                commands.append(command)
    return commands[:50]


def _parse_task_targets(path: Path, runner: str) -> Dict[str, str]:
    text = _read_text(path)
    if not text:
        return {}

    targets: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        target = _target_from_line(line, runner)
        if target:
            current = target if target in WORKFLOW_KINDS else None
            if current and current not in targets:
                targets[current] = []
            continue
        if current and (raw_line.startswith("\t") or raw_line.startswith("    ")):
            targets[current].append(line.strip())

    return {target: "\n".join(commands) for target, commands in targets.items()}


def _target_from_line(line: str, runner: str) -> Optional[str]:
    if runner == "make":
        if line.startswith("."):
            return None
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*:(?:\s|$)", line)
        return match.group(1) if match else None
    match = re.match(r"^([A-Za-z0-9_.-]+)\s*:", line)
    return match.group(1) if match else None


def _risk_for_command(kind: str, command: str) -> str:
    lowered = (command or "").lower()
    if kind in {"deploy", "release", "publish"}:
        return "high"
    if any(word in lowered for word in DANGEROUS_WORDS):
        return "high"
    if kind in {"install", "format", "build", "dev", "ci"}:
        return "medium"
    return "low"


def _classify_workflow_kind(command: str) -> str:
    lowered = command.lower()
    for kind in ("test", "lint", "format", "build", "install", "dev"):
        if re.search(rf"\b{kind}\b", lowered):
            return kind
    return "ci"


def _fact(name: str, confidence: float, evidence: Sequence[str], reason: str) -> Dict[str, Any]:
    return {
        "name": name,
        "confidence": _confidence_label(confidence),
        "confidence_score": round(confidence, 2),
        "evidence": sorted(set(evidence)),
        "reason": reason,
    }


def _package_manager(
    name: str,
    command: str,
    confidence: float,
    evidence: Sequence[str],
    warnings: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return {
        "name": name,
        "command": command,
        "confidence": _confidence_label(confidence),
        "confidence_score": round(confidence, 2),
        "evidence": sorted(set(evidence)),
        "warnings": list(warnings or []),
    }


def _confidence_label(value: float) -> str:
    if value >= 0.85:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"


def _confidence_score(label: str) -> float:
    return {"high": 0.95, "medium": 0.65, "low": 0.35}.get(label, 0.0)


def _merge_facts(groups: Iterable[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        for fact in group:
            if not fact:
                continue
            name = fact.get("name")
            if not name:
                continue
            current = merged.get(name)
            if current is None or fact.get("confidence_score", 0) > current.get("confidence_score", 0):
                merged[name] = dict(fact)
            else:
                current["evidence"] = sorted(set(current.get("evidence", [])) | set(fact.get("evidence", [])))
    return [merged[name] for name in sorted(merged)]


def _dedupe_facts(facts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _merge_facts([facts])


def _dedupe_workflows(workflows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[Tuple[str, Optional[str], str], Dict[str, Any]] = {}
    for workflow in workflows:
        key = (workflow.get("kind", ""), workflow.get("command"), workflow.get("cwd", ""))
        current = seen.get(key)
        if current is None:
            seen[key] = dict(workflow)
            continue
        current["evidence"] = sorted(set(current.get("evidence", [])) | set(workflow.get("evidence", [])))
        if workflow.get("recommended") and not current.get("recommended"):
            seen[key] = dict(workflow)
    return list(seen.values())


def _project_type(components: Sequence[Dict[str, Any]]) -> str:
    if not components:
        return "unknown"
    if len(components) == 1:
        return "single-component"
    return "multi-component"


def _component_type(languages: Sequence[Dict[str, Any]]) -> str:
    names = {item.get("name") for item in languages if item.get("name")}
    if not names:
        return "unknown"
    if len(names) == 1:
        return next(iter(names))
    return "mixed"


def _component_scope(path: str, all_roots: Sequence[str]) -> str:
    if path == "." and len(all_roots) > 1:
        return "repo"
    return "component"


def _match_component(components: Sequence[Dict[str, Any]], rel_path: str) -> Optional[Dict[str, Any]]:
    matches = []
    for component in components:
        path = component.get("path", ".")
        if path == "." or rel_path == path or rel_path.startswith(path + "/"):
            matches.append(component)
    if not matches:
        return None
    return sorted(matches, key=lambda item: len(item.get("path", "")), reverse=True)[0]


def _package_dependencies(package: Dict[str, Any]) -> Set[str]:
    deps: Set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = package.get(key)
        if isinstance(value, dict):
            deps.update(str(name).lower() for name in value)
    return deps


def _js_frameworks(dependencies: Set[str]) -> List[str]:
    known = {
        "next": "nextjs",
        "react": "react",
        "vue": "vue",
        "svelte": "svelte",
        "@angular/core": "angular",
        "vite": "vite",
        "nuxt": "nuxt",
        "express": "express",
        "nestjs": "nestjs",
    }
    return sorted({label for dep, label in known.items() if dep in dependencies})


def _python_frameworks(component_dir: Path, pyproject: Dict[str, Any], requirement_files: Sequence[str]) -> List[str]:
    deps = set()
    deps.update(_pyproject_dependency_names(pyproject))
    for filename in requirement_files:
        deps.update(_requirements_dependency_names(component_dir / filename))
    known = {
        "django": "django",
        "flask": "flask",
        "fastapi": "fastapi",
        "pytest": "pytest",
        "ruff": "ruff",
        "black": "black",
    }
    return sorted({label for dep, label in known.items() if dep in deps})


def _pyproject_dependency_names(pyproject: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()
    project = pyproject.get("project", {}) if isinstance(pyproject, dict) else {}
    if isinstance(project, dict):
        names.update(_dependency_name(item) for item in project.get("dependencies", []) if isinstance(item, str))
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for values in optional.values():
                if isinstance(values, list):
                    names.update(_dependency_name(item) for item in values if isinstance(item, str))
    tool = pyproject.get("tool", {}) if isinstance(pyproject, dict) else {}
    poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
    if isinstance(poetry, dict):
        for key in ("dependencies", "dev-dependencies"):
            value = poetry.get(key)
            if isinstance(value, dict):
                names.update(str(name).lower() for name in value.keys())
    return {name for name in names if name}


def _requirements_dependency_names(path: Path) -> Set[str]:
    text = _read_text(path)
    names = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        names.add(_dependency_name(stripped))
    return {name for name in names if name}


def _dependency_name(value: str) -> str:
    match = re.match(r"^\s*([A-Za-z0-9_.-]+)", value)
    return match.group(1).lower().replace("_", "-") if match else ""


def _python_install_command(pm: Dict[str, Any]) -> Optional[str]:
    name = pm["name"]
    if name == "uv":
        return "uv sync"
    if name == "poetry":
        return "poetry install"
    if name == "pdm":
        return "pdm install"
    if name == "pipenv":
        return "pipenv install --dev"
    if name == "pip":
        if any(path.endswith("requirements.txt") for path in pm.get("evidence", [])):
            return "python -m pip install -r requirements.txt"
        return "python -m pip install -e ."
    return None


def _js_install_command(pm: Dict[str, Any]) -> str:
    name = pm["name"]
    evidence = set(pm.get("evidence", []))
    if name == "npm":
        return "npm ci" if any(path.endswith(("package-lock.json", "npm-shrinkwrap.json")) for path in evidence) else "npm install"
    if name == "pnpm":
        return "pnpm install --frozen-lockfile" if any(path.endswith("pnpm-lock.yaml") for path in evidence) else "pnpm install"
    if name == "yarn":
        return "yarn install --immutable" if any(path.endswith("yarn.lock") for path in evidence) else "yarn install"
    if name == "bun":
        return "bun install --frozen-lockfile" if any("bun.lock" in path for path in evidence) else "bun install"
    return f"{name} install"


def _js_script_command(pm_name: str, script: str) -> str:
    if pm_name == "yarn":
        return f"yarn {script}"
    if pm_name == "bun":
        return f"bun run {script}"
    if pm_name == "pnpm":
        return f"pnpm run {script}"
    return f"npm run {script}"


def _pm_executable(name: str) -> str:
    return {"npm": "npm", "pnpm": "pnpm", "yarn": "yarn", "bun": "bun"}.get(name, name)


def _has_test_sample(root: Path, component_path: str, ignore: _GitIgnore) -> bool:
    return bool(_test_samples(root, component_path, ignore))


def _test_samples(root: Path, component_path: str, ignore: _GitIgnore) -> List[str]:
    component_dir = root if component_path == "." else root / component_path
    samples = []
    patterns = ("test_*.py", "*_test.py")
    for test_root in (component_dir / "tests", component_dir):
        if not test_root.exists():
            continue
        for pattern in patterns:
            for path in test_root.glob(pattern):
                rel = _rel_to_root(root, path)
                if _visible_file(root, ignore, rel):
                    samples.append(rel)
                    if len(samples) >= 3:
                        return samples
    return samples


def _setup_cfg_has_section(path: Path, section: str) -> bool:
    text = _read_text(path)
    return f"[{section}]" in text


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _load_toml(path: Path) -> Dict[str, Any]:
    if tomllib is None or not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def _read_text(path: Path, max_bytes: int = 1_000_000) -> str:
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _fingerprint(path: Path) -> Dict[str, Any]:
    try:
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "path": _clean_rel(str(path.name)) if not path.is_absolute() else str(path),
            "sha256": digest.hexdigest(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        return {
            "path": str(path),
            "sha256": None,
            "size": None,
            "mtime_ns": None,
            "missing": True,
        }


def _fingerprint_with_rel(root: Path, rel_path: str) -> Dict[str, Any]:
    item = _fingerprint(root / rel_path)
    item["path"] = rel_path
    return item


def _visible_file(root: Path, ignore: _GitIgnore, rel_path: str) -> bool:
    rel = _clean_rel(rel_path)
    return (root / rel).is_file() and not ignore.ignored(rel, is_dir=False)


def _resolve_root(root: str | os.PathLike[str]) -> Path:
    return Path(root).expanduser().resolve()


def _resolve_cache_path(root: Path, cache_path: str | os.PathLike[str] | None) -> Path:
    if cache_path is None:
        return root / DEFAULT_CACHE_NAME
    path = Path(cache_path).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _normalize_changed_files(root: Path, changed_files: Sequence[str]) -> List[str]:
    normalized = []
    for item in changed_files:
        if not item:
            continue
        path = Path(item)
        if path.is_absolute():
            try:
                rel = path.resolve().relative_to(root)
            except ValueError:
                continue
            normalized.append(rel.as_posix())
        else:
            normalized.append(_clean_rel(item))
    return sorted(set(path for path in normalized if path and path != "."))


def _rel_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _clean_rel(path: str) -> str:
    rel = Path(path).as_posix()
    if rel == ".":
        return "."
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.strip("/")


def _join_rel(base: str, name: str) -> str:
    return name if base == "." else f"{base}/{name}"


def _dirname_rel(path: str) -> str:
    parent = Path(path).parent.as_posix()
    return "." if parent == "." else parent


def _is_under(path: str, parent: str) -> bool:
    if parent == ".":
        return True
    return path == parent or path.startswith(parent + "/")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_result(data: Dict[str, Any], output_format: str, verbose: bool = False) -> Dict[str, Any] | str:
    fmt = _normalize_output_format(output_format)
    if fmt == "json":
        return data
    return _render_text(data, verbose=verbose)


def _normalize_output_format(output_format: str) -> str:
    fmt = (output_format or "text").lower()
    if fmt not in {"text", "json"}:
        raise ValueError("format must be 'text' or 'json'")
    return fmt


def _render_text(data: Dict[str, Any], verbose: bool = False) -> str:
    operation = data.get("operation", "sync")
    if operation == "install-skill":
        return _render_install_skill_text(data, verbose=verbose)

    profile = data.get("profile") if isinstance(data.get("profile"), dict) else data if isinstance(data.get("project"), dict) else None
    alignment = data.get("alignment") or (profile or {}).get("alignment", {})
    lines = [
        "code-workflow-probe",
        f"{operation}: aligned={_bool_text(alignment.get('aligned'))} reason={alignment.get('reason', 'unknown')}",
    ]

    stale = alignment.get("stale_files", [])
    new_files = alignment.get("new_profile_files", [])
    removed = alignment.get("removed_profile_files", [])
    if stale:
        lines.append(f"stale_files: {', '.join(stale)}")
    if new_files:
        lines.append(f"new_profile_files: {', '.join(new_files)}")
    if removed:
        lines.append(f"removed_profile_files: {', '.join(removed)}")
    if "profile_updated" in data:
        lines.append(f"profile_updated: {_bool_text(data.get('profile_updated'))}")
    if data.get("changed_files"):
        lines.append(f"changed_files: {', '.join(data['changed_files'])}")

    if data.get("affected"):
        _append_affected_text(lines, data)

    if profile:
        _append_profile_text(lines, profile, verbose=verbose)
    else:
        lines.append("profile: unavailable")

    warnings = list(data.get("warnings", []))
    if profile:
        warnings.extend(profile.get("warnings", []))
    if warnings:
        _append_list(lines, "warnings", warnings)
    return "\n".join(lines)


def _render_install_skill_text(data: Dict[str, Any], verbose: bool = False) -> str:
    lines = [
        "code-workflow-probe",
        f"install-skill: target={data.get('target')} installed={_bool_text(data.get('installed'))} dry_run={_bool_text(data.get('dry_run'))}",
        f"path: {data.get('skill_path')}",
        "note: installed skill tells Codex to sync after editing project/workflow management files.",
    ]
    if verbose and data.get("content"):
        lines.append("content:")
        lines.append(str(data["content"]).rstrip())
    if data.get("warnings"):
        _append_list(lines, "warnings", data.get("warnings", []))
    return "\n".join(lines)


def _append_profile_text(lines: List[str], profile: Dict[str, Any], verbose: bool = False) -> None:
    project = profile.get("project", {})
    components = project.get("components", [])
    lines.append(
        f"project: {project.get('type', 'unknown')}; "
        f"tech={_format_fact_names(project.get('technologies', []), verbose=verbose)}; "
        f"pm={_format_package_managers(project.get('package_managers', []), verbose=verbose)}"
    )
    lines.append("components:")
    if not components:
        lines.append("- none")
    for component in components:
        lines.append(
            "- "
            f"id={component.get('id')} "
            f"path={component.get('path')} "
            f"lang={_format_fact_names(component.get('languages', []), verbose=verbose)} "
            f"pm={_format_package_manager(component.get('package_manager'), verbose=verbose)}"
        )
        _append_workflow_groups(lines, component.get("workflows", []), indent="  ", verbose=verbose)

    ci_workflows = project.get("ci_workflows", [])
    if ci_workflows:
        lines.append(f"ci: {len(ci_workflows)} candidate(s), not local")

    evidence_files = sorted(profile.get("evidence_files", {}))
    if verbose:
        lines.append("evidence_files:")
        if not evidence_files:
            lines.append("- none")
        for path in evidence_files:
            fingerprint = profile["evidence_files"][path]
            sha = fingerprint.get("sha256") or "missing"
            roles = ",".join(fingerprint.get("roles", [])) or "unknown"
            lines.append(f"- {path}: sha256={sha} size={fingerprint.get('size')} roles={roles}")
    else:
        preview = ", ".join(evidence_files[:5]) if evidence_files else "none"
        suffix = "" if len(evidence_files) <= 5 else f", +{len(evidence_files) - 5} more"
        lines.append(f"evidence({len(evidence_files)}): {preview}{suffix}")


def _append_affected_text(lines: List[str], data: Dict[str, Any]) -> None:
    affected_data = data.get("affected", {})
    components = affected_data.get("components", [])
    lines.append(f"affected: components={', '.join(components) if components else 'none'}")
    files = affected_data.get("files", [])
    for item in files[:8]:
        lines.append(
            "- "
            f"{item.get('file')} -> component={item.get('component_id') or 'none'} "
            f"profile_affecting={_bool_text(item.get('profile_affecting'))}"
        )

    workflows = data.get("suggested_workflows", [])
    lines.append("suggested_workflows:")
    if not workflows:
        lines.append("- none")
    for workflow in workflows[:12]:
        component_id = workflow.get("component_id")
        prefix = f"component={component_id} " if component_id else ""
        lines.append(f"- {prefix}{_format_workflow(workflow)}")
    if len(workflows) > 12:
        lines.append(f"- +{len(workflows) - 12} more")


def _append_workflow_groups(lines: List[str], workflows: Sequence[Dict[str, Any]], indent: str = "", verbose: bool = False) -> None:
    if not workflows:
        return
    safe = [workflow for workflow in workflows if workflow.get("safe_auto")]
    review = [workflow for workflow in workflows if not workflow.get("safe_auto")]
    if safe:
        lines.append(f"{indent}safe_auto:")
        for workflow in safe:
            lines.append(f"{indent}- {_format_workflow(workflow, verbose=verbose)}")
    if review:
        lines.append(f"{indent}needs_review:")
        for workflow in review[:8]:
            lines.append(f"{indent}- {_format_workflow(workflow, verbose=verbose)}")
        if len(review) > 8:
            lines.append(f"{indent}- +{len(review) - 8} more")


def _format_workflow(workflow: Dict[str, Any], verbose: bool = False) -> str:
    if not verbose:
        notes = []
        if workflow.get("candidate"):
            notes.append("candidate")
        if workflow.get("risk") and workflow.get("risk") != "low":
            notes.append(f"risk={workflow.get('risk')}")
        if workflow.get("confidence") and workflow.get("confidence") != "high":
            notes.append(f"conf={workflow.get('confidence')}")
        if workflow.get("ci_only"):
            notes.append("ci-only")
        suffix = f" [{' '.join(notes)}]" if notes else ""
        return f"{workflow.get('kind')}: cwd={workflow.get('cwd') or '?'} command={workflow.get('command') or 'none'}{suffix}"
    return (
        f"kind={workflow.get('kind')} "
        f"command={workflow.get('command') or 'none'} "
        f"cwd={workflow.get('cwd') or 'unknown'} "
        f"scope={workflow.get('scope')} "
        f"source={workflow.get('source')} "
        f"confidence={workflow.get('confidence')} "
        f"risk={workflow.get('risk')} "
        f"safe_auto={_bool_text(workflow.get('safe_auto'))} "
        f"candidate={_bool_text(workflow.get('candidate'))} "
        f"ci_only={_bool_text(workflow.get('ci_only'))} "
        f"evidence={_format_names(workflow.get('evidence', []))}"
    )


def _format_fact_names(facts: Sequence[Dict[str, Any]], verbose: bool = False) -> str:
    if not facts:
        return "none"
    if verbose:
        return ", ".join(f"{fact.get('name')}({fact.get('confidence')})" for fact in facts)
    return ",".join(str(fact.get("name")) for fact in facts)


def _format_package_managers(package_managers: Sequence[Dict[str, Any]], verbose: bool = False) -> str:
    if not package_managers:
        return "none"
    return ", ".join(_format_package_manager(item, verbose=verbose) for item in package_managers)


def _format_package_manager(package_manager: Optional[Dict[str, Any]], verbose: bool = False) -> str:
    if not package_manager:
        return "none"
    if not verbose:
        return str(package_manager.get("name"))
    return f"{package_manager.get('name')}({package_manager.get('confidence')}; command={package_manager.get('command')})"


def _format_names(values: Sequence[str]) -> str:
    return ",".join(values) if values else "none"


def _append_list(lines: List[str], label: str, values: Sequence[str]) -> None:
    lines.append(f"{label}:")
    if not values:
        lines.append("- none")
        return
    for value in values:
        lines.append(f"- {value}")


def _bool_text(value: Any) -> str:
    return "true" if value is True else "false" if value is False else "unknown"


def _resolve_codex_skills_dir(skills_dir: str | os.PathLike[str] | None) -> Path:
    if skills_dir is not None:
        return Path(skills_dir).expanduser().resolve()
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return (Path(codex_home).expanduser() / "skills").resolve()
    return (Path.home() / ".codex" / "skills").resolve()


def _codex_skill_markdown() -> str:
    return """---
name: code-workflow-probe
description: Use code-workflow-probe to keep repo workflow facts aligned before exploring, after relevant edits, and before validation.
---

# Code Workflow Probe

Use `code-workflow-probe` when working in a repository and you need current, evidence-backed workflow facts for install, test, lint, format, build, dev, components, package managers, CI, and affected files.

## Workflow

1. At task start, run:
   `code-workflow-probe sync --root <repo>`
2. Prefer the default text output for quick agent context.
3. Use JSON when you need structured data:
   `code-workflow-probe sync --root <repo> --format json`
4. After editing files, notify the probe:
   `code-workflow-probe edit --root <repo> --changed <path> [<path>...]`
5. Before validation, map changes to components and workflows:
   `code-workflow-probe affected --root <repo> --changed <path> [<path>...]`
6. If status says the profile is not aligned, run sync before trusting workflow conclusions:
   `code-workflow-probe status --root <repo>`

## Important Sync Rule

Strongly prefer running `code-workflow-probe sync --root <repo>` after editing project or workflow management files, including manifests, lockfiles, package-manager files, task-runner files, CI files, test/lint/format/build config, and monorepo/component boundary files.

Examples include `package.json`, lockfiles, `pyproject.toml`, `requirements*.txt`, `go.mod`, `Cargo.toml`, `pom.xml`, Gradle files, `Makefile`, `justfile`, `.github/workflows/*`, `.gitlab-ci.yml`, `pytest.ini`, `ruff.toml`, ESLint config, Prettier config, and similar workflow evidence files.

## Safety Rules

- Do not use stale profile data. If `aligned` is false or unknown, sync first.
- Only auto-run workflows that are `safe_auto=true`, local, high confidence, low risk, and have a known cwd.
- Treat CI-only, candidate, inferred, medium/high risk, and low-confidence workflows as requiring review.
- Do not turn CI commands into local commands without checking cwd and local evidence.
- Do not invent missing workflows.
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="code-workflow-probe", description="Deterministic repo workflow profile syncer.")
    parser.add_argument("--root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--cache", default=None, help=f"Cache path. Defaults to {DEFAULT_CACHE_NAME} under root.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format. Defaults to text.")
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON when --format json is used.")
    parser.add_argument("--verbose", action="store_true", help="Expand text output with full evidence details.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--root", default=argparse.SUPPRESS, help="Repository root. Defaults to current directory.")
        subparser.add_argument("--cache", default=argparse.SUPPRESS, help=f"Cache path. Defaults to {DEFAULT_CACHE_NAME} under root.")
        subparser.add_argument("--format", choices=("text", "json"), default=argparse.SUPPRESS, help="Output format. Defaults to text.")
        subparser.add_argument("--compact", action="store_true", default=argparse.SUPPRESS, help="Emit compact JSON when --format json is used.")
        subparser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS, help="Expand text output with full evidence details.")

    sync_parser = subparsers.add_parser("sync", help="Build and cache an aligned profile.")
    add_common(sync_parser)
    sync_parser.add_argument("--changed", nargs="*", default=[], help="Changed files to include in output context.")
    sync_parser.add_argument("--no-write", action="store_true", help="Do not write cache.")

    status_parser = subparsers.add_parser("status", help="Check whether cached profile is aligned.")
    add_common(status_parser)

    edit_parser = subparsers.add_parser("edit", help="Notify changed files and update profile if needed.")
    add_common(edit_parser)
    edit_parser.add_argument("--changed", nargs="+", required=True, help="Changed files.")

    affected_parser = subparsers.add_parser("affected", help="Map changed files to components and workflows.")
    add_common(affected_parser)
    affected_parser.add_argument("--changed", nargs="+", required=True, help="Changed files.")

    skill_parser = subparsers.add_parser("install-skill", help="Install a Codex skill for code-workflow-probe.")
    add_common(skill_parser)
    skill_parser.add_argument("--tool", choices=("codex",), default="codex", help="Target AI coding tool. Only codex is supported.")
    skill_parser.add_argument("--skills-dir", default=None, help="Codex skills directory. Defaults to $CODEX_HOME/skills or ~/.codex/skills.")
    skill_parser.add_argument("--dry-run", action="store_true", help="Preview the target path and skill content without writing files.")
    skill_parser.add_argument("--no-overwrite", action="store_true", help="Do not overwrite an existing skill file.")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "sync":
        output = sync(args.root, args.cache, changed_files=args.changed, write=not args.no_write, format=args.format, verbose=args.verbose)
    elif args.command == "status":
        output = status(args.root, args.cache, format=args.format, verbose=args.verbose)
    elif args.command == "edit":
        output = edit(args.root, args.changed, args.cache, format=args.format, verbose=args.verbose)
    elif args.command == "affected":
        output = affected(args.root, args.changed, args.cache, format=args.format, verbose=args.verbose)
    elif args.command == "install-skill":
        output = install_skill(
            tool=args.tool,
            skills_dir=args.skills_dir,
            dry_run=args.dry_run,
            overwrite=not args.no_overwrite,
            format=args.format,
            verbose=args.verbose,
        )
    else:  # pragma: no cover - argparse prevents this.
        parser.error(f"unknown command: {args.command}")

    if args.format == "json":
        json.dump(output, sys.stdout, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(str(output))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

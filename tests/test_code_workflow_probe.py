import json
import subprocess
import sys
from pathlib import Path

import code_workflow_probe as probe


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_mixed_repo(root: Path) -> Path:
    write(
        root / "app" / "package.json",
        json.dumps(
            {
                "scripts": {
                    "test": "vitest run",
                    "lint": "eslint .",
                    "build": "vite build",
                    "dev": "vite --host 0.0.0.0",
                },
                "dependencies": {"react": "latest", "vite": "latest"},
                "devDependencies": {"typescript": "latest", "vitest": "latest"},
                "packageManager": "pnpm@9.0.0",
            }
        ),
    )
    write(root / "app" / "pnpm-lock.yaml", "lockfileVersion: 9\n")
    write(root / "app" / "src" / "main.ts", "const x: number = 1\n")
    write(
        root / "service" / "pyproject.toml",
        "\n".join(
            [
                "[project]",
                'dependencies=["pytest","ruff"]',
                "[tool.pytest.ini_options]",
                'testpaths=["tests"]',
                "[tool.ruff]",
                "line-length=100",
            ]
        ),
    )
    write(root / "service" / "tests" / "test_sample.py", "def test_ok():\n    assert True\n")
    write(
        root / ".github" / "workflows" / "ci.yml",
        "\n".join(
            [
                "name: ci",
                "on: [push]",
                "jobs:",
                "  test:",
                "    runs-on: ubuntu-latest",
                "    steps:",
                "      - run: pnpm run test",
            ]
        ),
    )
    return root / "cache.json"


def component(profile, component_id):
    return {item["id"]: item for item in profile["project"]["components"]}[component_id]


def workflows(component_data, kind):
    return [item for item in component_data["workflows"] if item["kind"] == kind]


def test_api_default_format_is_text_and_json_format_returns_dict(tmp_path):
    cache = make_mixed_repo(tmp_path)

    text = probe.sync(tmp_path, cache)
    data = probe.sync(tmp_path, cache, format="json")
    status_text = probe.status(tmp_path, cache)
    verbose_text = probe.sync(tmp_path, cache, verbose=True)

    assert isinstance(text, str)
    assert "sync: aligned=true" in text
    assert "components:" in text
    assert "test: cwd=app command=pnpm run test" in text
    assert " @ " not in text
    assert "evidence_files:" not in text
    assert isinstance(data, dict)
    assert data["alignment"]["aligned"] is True
    assert isinstance(status_text, str)
    assert "status: aligned=true" in status_text
    assert "evidence_files:" in verbose_text
    assert "sha256=" in verbose_text


def test_sync_builds_aligned_multi_component_profile(tmp_path):
    cache = make_mixed_repo(tmp_path)

    profile = probe.sync(tmp_path, cache, format="json")

    assert profile["alignment"]["aligned"] is True
    assert profile["project"]["type"] == "multi-component"
    assert {item["id"] for item in profile["project"]["components"]} == {"app", "service"}

    app = component(profile, "app")
    service = component(profile, "service")

    assert app["package_manager"]["name"] == "pnpm"
    assert app["languages"][0]["name"] == "typescript"
    assert app["workflows"][0]["cwd"] == "app"
    assert workflows(app, "test")[0]["safe_auto"] is True
    assert workflows(app, "lint")[0]["safe_auto"] is True
    assert workflows(app, "build")[0]["safe_auto"] is False
    assert workflows(app, "dev")[0]["safe_auto"] is False

    assert service["package_manager"]["name"] == "pip"
    assert workflows(service, "test")[0]["command"] == "python -m pytest"
    assert workflows(service, "test")[0]["safe_auto"] is True
    assert workflows(service, "lint")[0]["command"] == "ruff check ."

    assert profile["project"]["repo_workflows"] == []
    assert profile["project"]["ci_workflows"]
    assert all(item["ci_only"] for item in profile["project"]["ci_workflows"])
    assert all(not item["safe_auto"] for item in profile["project"]["ci_workflows"])


def test_status_and_edit_hook_resync_only_profile_affecting_changes(tmp_path):
    cache = make_mixed_repo(tmp_path)
    probe.sync(tmp_path, cache, format="json")

    assert probe.status(tmp_path, cache, format="json")["alignment"]["aligned"] is True

    write(tmp_path / "app" / "src" / "main.ts", "const x: number = 2\n")
    source_edit = probe.edit(tmp_path, ["app/src/main.ts"], cache, format="json")
    assert source_edit["profile_updated"] is False
    assert source_edit["affected"]["components"] == ["app"]
    assert source_edit["suggested_workflows"]

    write(
        tmp_path / "app" / "package.json",
        json.dumps({"scripts": {"test": "vitest run", "lint": "eslint ."}, "packageManager": "pnpm@9.0.0"}),
    )
    stale = probe.status(tmp_path, cache, format="json")
    assert stale["alignment"]["aligned"] is False
    assert stale["alignment"]["stale_files"] == ["app/package.json"]

    manifest_edit = probe.edit(tmp_path, ["app/package.json"], cache, format="json")
    assert manifest_edit["profile_updated"] is True
    assert manifest_edit["alignment"]["aligned"] is True
    assert [item["kind"] for item in manifest_edit["suggested_workflows"]] == ["test", "lint"]


def test_affected_maps_files_to_components_and_local_workflows(tmp_path):
    cache = make_mixed_repo(tmp_path)
    probe.sync(tmp_path, cache, format="json")

    result = probe.affected(tmp_path, ["app/src/main.ts", "service/new_module.py"], cache, format="json")

    assert result["alignment"]["aligned"] is True
    assert result["affected"]["components"] == ["app", "service"]
    assert {item["component_id"] for item in result["suggested_workflows"]} == {"app", "service"}
    assert {item["kind"] for item in result["suggested_workflows"]} == {"test", "lint", "build"}


def test_cli_supports_global_and_subcommand_options(tmp_path):
    cache = make_mixed_repo(tmp_path)
    script = Path(probe.__file__).resolve()

    sync_result = subprocess.run(
        [sys.executable, str(script), "sync", "--root", str(tmp_path), "--cache", str(cache), "--compact"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "code-workflow-probe" in sync_result.stdout
    assert "sync: aligned=true" in sync_result.stdout

    json_result = subprocess.run(
        [sys.executable, str(script), "sync", "--root", str(tmp_path), "--cache", str(cache), "--format", "json", "--compact"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(json_result.stdout)["alignment"]["aligned"] is True

    status_result = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path), "--cache", str(cache), "--format", "json", "status", "--compact"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(status_result.stdout)["alignment"]["aligned"] is True


def test_install_skill_api_and_cli(tmp_path):
    skills_dir = tmp_path / "skills"

    dry_run = probe.install_skill(skills_dir=skills_dir, dry_run=True, format="json")
    skill_path = skills_dir / "code-workflow-probe" / "SKILL.md"

    assert dry_run["installed"] is False
    assert dry_run["dry_run"] is True
    assert dry_run["skill_path"] == str(skill_path)
    assert not skill_path.exists()
    assert "after editing project or workflow management files" in dry_run["content"]
    assert "code-workflow-probe sync --root <repo>" in dry_run["content"]

    installed = probe.install_skill(skills_dir=skills_dir, format="json")
    assert installed["installed"] is True
    assert installed["overwritten"] is False
    assert skill_path.exists()
    assert "name: code-workflow-probe" in skill_path.read_text(encoding="utf-8")

    script = Path(probe.__file__).resolve()
    cli = subprocess.run(
        [
            sys.executable,
            str(script),
            "install-skill",
            "--skills-dir",
            str(skills_dir),
            "--dry-run",
            "--format",
            "json",
            "--compact",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(cli.stdout)
    assert data["target"] == "codex"
    assert data["dry_run"] is True


def test_unknown_repo_is_conservative(tmp_path):
    write(tmp_path / "tool.py", "print('hello')\n")

    profile = probe.sync(tmp_path, tmp_path / "cache.json", format="json")

    assert profile["project"]["type"] == "single-component"
    root = component(profile, "root")
    assert root["languages"][0]["name"] == "python"
    assert root["languages"][0]["confidence"] == "low"
    assert root["package_manager"] is None
    assert root["workflows"] == []


def test_gitignore_filters_profile_inputs_and_is_watched(tmp_path):
    write(tmp_path / ".gitignore", "ignored/\napp/package-lock.json\n")
    write(tmp_path / "ignored" / "package.json", json.dumps({"scripts": {"test": "echo ignored"}}))
    write(tmp_path / "ignored" / "ghost.py", "print('ignored')\n")
    write(tmp_path / "app" / "package.json", json.dumps({"scripts": {"test": "node test.js"}}))
    write(tmp_path / "app" / "package-lock.json", '{"lockfileVersion":3}\n')

    cache = tmp_path / "cache.json"
    profile = probe.sync(tmp_path, cache, format="json")

    assert {item["id"] for item in profile["project"]["components"]} == {"app"}
    assert ".gitignore" in profile["evidence_files"]
    assert "ignored/package.json" not in profile["watch"]["files"]
    assert "app/package-lock.json" not in profile["watch"]["files"]

    app = component(profile, "app")
    assert app["package_manager"]["name"] == "npm"
    assert app["package_manager"]["confidence"] == "medium"

    write(tmp_path / ".gitignore", "ignored/\napp/package-lock.json\napp/\n")
    status = probe.status(tmp_path, cache, format="json")

    assert status["alignment"]["aligned"] is False
    assert ".gitignore" in status["alignment"]["stale_files"]


def test_risky_local_and_ci_workflows_are_not_safe_auto(tmp_path):
    write(
        tmp_path / "package.json",
        json.dumps({"scripts": {"test": "rm -rf build", "lint": "eslint ."}, "packageManager": "npm@10.0.0"}),
    )
    write(tmp_path / "package-lock.json", '{"lockfileVersion":3}\n')
    write(tmp_path / ".github" / "workflows" / "release.yml", "jobs:\n  release:\n    steps:\n      - run: npm publish\n")

    profile = probe.sync(tmp_path, tmp_path / "cache.json", format="json")
    root = component(profile, "root")

    test_workflow = workflows(root, "test")[0]
    lint_workflow = workflows(root, "lint")[0]
    ci_workflow = profile["project"]["ci_workflows"][0]

    assert test_workflow["risk"] == "high"
    assert test_workflow["safe_auto"] is False
    assert lint_workflow["risk"] == "low"
    assert lint_workflow["safe_auto"] is True
    assert ci_workflow["ci_only"] is True
    assert ci_workflow["safe_auto"] is False

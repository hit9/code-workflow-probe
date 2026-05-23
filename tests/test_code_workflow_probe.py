import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
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


def test_status_standard_component_preview_uses_path_depth_and_limit():
    components = [
        {
            "id": "api",
            "path": "services/api",
            "workflows": [{"kind": "test", "command": "python -m pytest", "cwd": "services/api", "source": "local", "safe_auto": True}],
            "evidence": ["services/api/pyproject.toml"],
            "languages": [{"name": "python"}],
            "package_manager": {"name": "pip"},
        },
        {
            "id": "web",
            "path": "apps/web",
            "workflows": [
                {"kind": "test", "safe_auto": True},
                {"kind": "build", "command": "pnpm build", "cwd": "apps/web", "source": "local", "safe_auto": False},
                {"kind": "test", "command": "pnpm test", "cwd": "apps/web", "source": "local", "safe_auto": True},
            ],
            "evidence": ["apps/web/package.json"],
            "languages": [{"name": "typescript"}],
            "package_manager": {"name": "pnpm"},
        },
        {
            "id": "worker",
            "path": "services/deep/worker",
            "workflows": [{"kind": "test", "command": "python -m pytest", "cwd": "services/deep/worker", "source": "local", "safe_auto": True}],
            "evidence": ["services/deep/worker/pyproject.toml"],
            "languages": [{"name": "python"}],
            "package_manager": {"name": "pip"},
        },
    ]
    lines = []

    probe._append_status_components(lines, components, limit=1, depth=2)
    text = "\n".join(lines)

    assert "components(depth=2, shown=1/3):" in text
    assert "id=web" in text
    assert "id=api" not in text
    assert "id=worker" not in text
    assert "workflows(local, shown=1/2):" in text
    assert "test: cwd=apps/web command=pnpm test" in text
    assert "build: cwd=apps/web command=pnpm build" not in text
    assert "hidden: depth=1 limit=1" in text


def test_api_default_format_is_text_and_json_format_returns_dict(tmp_path):
    cache = make_mixed_repo(tmp_path)

    text = probe.sync(tmp_path, cache)
    data = probe.sync(tmp_path, cache, format="json")
    status_text = probe.status(tmp_path, cache)
    standard_status = probe.status(tmp_path, cache, detail="standard", limit=1, depth=1)
    full_status = probe.status(tmp_path, cache, detail="full")
    verbose_status = probe.status(tmp_path, cache, verbose=True)
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
    assert "summary:" in status_text
    assert "- project: multi-component" in status_text
    assert "- components: 2" in status_text
    assert "- workflows: safe_auto=" in status_text
    assert "workflows(local, shown=" in status_text
    assert "component=app test: cwd=app command=pnpm run test" in status_text
    assert "component=app lint: cwd=app command=pnpm run lint" in status_text
    assert "id=app" not in status_text
    assert "components(depth=1, shown=1/2):" in standard_status
    assert "id=app" in standard_status
    assert "test: cwd=app command=pnpm run test" in standard_status
    assert "id=service" not in standard_status
    assert "evidence_files:" not in standard_status
    assert "id=app" in full_status
    assert "evidence_files:" in full_status
    assert "id=app" in verbose_status
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


def test_sync_detects_additional_stack_families(tmp_path):
    write(
        tmp_path / "ruby" / "Gemfile",
        "\n".join([
            'source "https://rubygems.org"',
            'gem "rails"',
            'gem "rspec"',
            'gem "rubocop"',
        ]),
    )
    write(tmp_path / "ruby" / "Gemfile.lock", "GEM\n")
    write(
        tmp_path / "php" / "composer.json",
        json.dumps(
            {
                "require": {"laravel/framework": "^11.0"},
                "require-dev": {"phpunit/phpunit": "^11.0", "phpstan/phpstan": "^1.0", "laravel/pint": "^1.0"},
                "scripts": {"test": "phpunit", "lint": "phpstan analyse", "format": "pint"},
            }
        ),
    )
    write(tmp_path / "php" / "composer.lock", "{}\n")
    write(tmp_path / "php" / "phpunit.xml", "<phpunit />\n")
    write(tmp_path / "php" / "phpstan.neon", "parameters: {}\n")
    write(
        tmp_path / "deno" / "deno.json",
        json.dumps({"tasks": {"test": "deno test", "lint": "deno lint", "fmt": "deno fmt", "build": "deno compile main.ts"}}),
    )
    write(tmp_path / "deno" / "main.ts", "console.log('ok')\n")
    write(tmp_path / "dotnet" / "App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk\"></Project>\n")
    write(tmp_path / "swift" / "Package.swift", "// swift-tools-version: 5.9\n")
    write(tmp_path / "swift" / "Package.resolved", "{}\n")

    profile = probe.sync(tmp_path, tmp_path / "cache.json", format="json")

    assert {item["id"] for item in profile["project"]["components"]} == {"deno", "dotnet", "php", "ruby", "swift"}

    ruby = component(profile, "ruby")
    assert ruby["package_manager"]["name"] == "bundler"
    assert ruby["languages"][0]["name"] == "ruby"
    assert {workflow["command"] for workflow in ruby["workflows"]} >= {
        "bundle install",
        "bundle exec rspec",
        "bundle exec rubocop",
    }

    php = component(profile, "php")
    assert php["package_manager"]["name"] == "composer"
    assert php["languages"][0]["name"] == "php"
    assert {workflow["command"] for workflow in php["workflows"]} >= {
        "composer install",
        "composer test",
        "composer lint",
        "composer format",
        "vendor/bin/phpunit",
        "vendor/bin/phpstan analyse",
    }

    deno = component(profile, "deno")
    assert deno["package_manager"]["name"] == "deno"
    assert {item["name"] for item in deno["frameworks"]} == {"deno"}
    assert {workflow["command"] for workflow in deno["workflows"]} >= {
        "deno task test",
        "deno task lint",
        "deno task fmt",
        "deno task build",
    }

    dotnet = component(profile, "dotnet")
    assert dotnet["package_manager"]["name"] == "dotnet"
    assert dotnet["languages"][0]["name"] == "csharp"
    assert {workflow["command"] for workflow in dotnet["workflows"]} >= {
        "dotnet restore",
        "dotnet test",
        "dotnet build",
        "dotnet format",
    }

    swift = component(profile, "swift")
    assert swift["package_manager"]["name"] == "swift package manager"
    assert swift["languages"][0]["name"] == "swift"
    assert {workflow["command"] for workflow in swift["workflows"]} >= {
        "swift package resolve",
        "swift test",
        "swift build",
    }


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


def test_sync_incremental_reuses_cache_for_non_profile_changes(tmp_path):
    cache = make_mixed_repo(tmp_path)
    first = probe.sync(tmp_path, cache, format="json")
    events = []

    write(tmp_path / "app" / "src" / "main.ts", "const x: number = 3\n")
    reused = probe.sync(
        tmp_path,
        cache,
        changed_files=["app/src/main.ts"],
        format="json",
        progress=events.append,
    )

    assert first["project"] == reused["project"]
    assert reused["alignment"]["reason"] == "incremental_reuse"
    assert "sync: reused cached profile" in events

    write(tmp_path / "app" / "package.json", json.dumps({"scripts": {"test": "vitest run"}}))
    rebuilt = probe.sync(tmp_path, cache, changed_files=["app/package.json"], format="json")

    assert rebuilt["alignment"]["reason"] == "synced"
    assert rebuilt["project"] != first["project"]


def test_sync_async_returns_future_and_runs_sync(tmp_path):
    cache = make_mixed_repo(tmp_path)
    events = []

    future = probe.sync_async(tmp_path, cache, format="json", progress=events.append)
    profile = future.result(timeout=5)

    assert profile["alignment"]["aligned"] is True
    assert profile["project"]["type"] == "multi-component"
    assert "sync: start" in events
    assert "sync: done" in events


def test_sync_async_accepts_custom_executor(tmp_path):
    cache = make_mixed_repo(tmp_path)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = probe.sync_async(tmp_path, cache, format="json", executor=executor)
        profile = future.result(timeout=5)

    assert profile["alignment"]["aligned"] is True


def test_sync_paths_only_updates_profile_file_without_repo_discovery(tmp_path, monkeypatch):
    cache = make_mixed_repo(tmp_path)
    first = probe.sync(tmp_path, cache, format="json")

    def fail_discovery(*args, **kwargs):
        raise AssertionError("paths-only sync should not discover the whole repo")

    def fail_source_summary(*args, **kwargs):
        raise AssertionError("paths-only sync should not scan source summary")

    monkeypatch.setattr(probe, "_discover_profile_files", fail_discovery)
    monkeypatch.setattr(probe, "_source_summary", fail_source_summary)

    write(tmp_path / "app" / "package.json", json.dumps({"scripts": {"test": "vitest run"}, "packageManager": "pnpm@9.0.0"}))
    updated = probe.sync(
        tmp_path,
        cache,
        changed_files=["app/package.json"],
        paths_only=True,
        format="json",
    )

    app = component(updated, "app")
    assert first["project"] != updated["project"]
    assert updated["alignment"]["reason"] == "paths_only_synced"
    assert [item["kind"] for item in app["workflows"]] == ["install", "test"]


def test_sync_paths_only_adds_ignored_adjacent_lockfile(tmp_path, monkeypatch):
    write(tmp_path / ".gitignore", "uv.lock\n")
    write(tmp_path / "pyproject.toml", "[project]\nname='demo'\n")
    cache = tmp_path / "cache.json"
    first = probe.sync(tmp_path, cache, format="json")

    def fail_discovery(*args, **kwargs):
        raise AssertionError("paths-only sync should not discover the whole repo")

    monkeypatch.setattr(probe, "_discover_profile_files", fail_discovery)

    write(tmp_path / "uv.lock", "version = 1\n")
    updated = probe.sync(
        tmp_path,
        cache,
        changed_files=["uv.lock"],
        paths_only=True,
        format="json",
    )

    root = component(updated, "root")
    assert component(first, "root")["package_manager"]["name"] == "pip"
    assert updated["alignment"]["reason"] == "paths_only_synced"
    assert "uv.lock" in updated["watch"]["files"]
    assert root["package_manager"]["name"] == "uv"


def test_sync_paths_only_requires_existing_cache(tmp_path):
    result = probe.sync(
        tmp_path,
        tmp_path / "cache.json",
        changed_files=["pyproject.toml"],
        paths_only=True,
        format="json",
    )

    assert result["alignment"]["aligned"] is False
    assert result["alignment"]["reason"] == "cache_missing_paths_only"
    assert result["profile"] is None


def test_manifest_sync_and_status_do_not_require_source_summary_scan(tmp_path, monkeypatch):
    cache = make_mixed_repo(tmp_path)

    def fail_source_summary(root):
        raise AssertionError("source summary should not be scanned for manifest repos")

    monkeypatch.setattr(probe, "_source_summary", fail_source_summary)

    profile = probe.sync(tmp_path, cache, format="json", incremental=False)
    status = probe.status(tmp_path, cache, format="json")

    assert profile["alignment"]["aligned"] is True
    assert status["alignment"]["aligned"] is True


def test_edit_and_affected_use_incremental_cache_before_status_scan(tmp_path, monkeypatch):
    cache = make_mixed_repo(tmp_path)
    probe.sync(tmp_path, cache, format="json")

    def fail_status(*args, **kwargs):
        raise AssertionError("status should not run for non-profile changed files")

    monkeypatch.setattr(probe, "status", fail_status)

    edited = probe.edit(tmp_path, ["app/src/main.ts"], cache, format="json")
    affected = probe.affected(tmp_path, ["app/src/main.ts"], cache, format="json")

    assert edited["profile_updated"] is False
    assert edited["alignment"]["reason"] == "incremental_reuse"
    assert affected["alignment"]["reason"] == "incremental_reuse"


def test_cli_progress_uses_stderr(tmp_path):
    cache = make_mixed_repo(tmp_path)
    script = Path(probe.__file__).resolve()
    subprocess.run(
        [sys.executable, str(script), "sync", "--root", str(tmp_path), "--cache", str(cache), "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "sync",
            "--root",
            str(tmp_path),
            "--cache",
            str(cache),
            "--changed",
            "app/src/main.ts",
            "--progress",
            "--format",
            "json",
            "--compact",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout)["alignment"]["reason"] == "incremental_reuse"
    assert "cwp [" in result.stderr
    assert "100% done" in result.stderr
    assert "sync: start" not in result.stderr


def test_affected_maps_files_to_components_and_local_workflows(tmp_path):
    cache = make_mixed_repo(tmp_path)
    probe.sync(tmp_path, cache, format="json")

    result = probe.affected(tmp_path, ["app/src/main.ts", "service/new_module.py"], cache, format="json")
    text = probe.affected(tmp_path, ["app/src/main.ts"], cache)

    assert result["alignment"]["aligned"] is True
    assert result["affected"]["components"] == ["app", "service"]
    assert {item["component_id"] for item in result["suggested_workflows"]} == {"app", "service"}
    assert {item["kind"] for item in result["suggested_workflows"]} == {"test", "lint", "build"}
    assert "suggested_workflows:" in text
    assert "profile: unavailable" not in text


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

    status_detail = subprocess.run(
        [sys.executable, str(script), "status", "--root", str(tmp_path), "--cache", str(cache), "--detail", "standard", "--limit", "1", "--depth", "1"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "components(depth=1, shown=1/2):" in status_detail.stdout
    assert "id=app" in status_detail.stdout
    assert "id=service" not in status_detail.stdout


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
    assert "--paths-only" in dry_run["content"]
    assert "--full" in dry_run["content"]
    assert "--progress" in dry_run["content"]
    assert "changed path list is complete" in dry_run["content"]

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
    assert "app/package-lock.json" in profile["watch"]["files"]

    app = component(profile, "app")
    assert app["package_manager"]["name"] == "npm"
    assert app["package_manager"]["confidence"] == "high"

    write(tmp_path / ".gitignore", "ignored/\napp/package-lock.json\napp/\n")
    status = probe.status(tmp_path, cache, format="json")

    assert status["alignment"]["aligned"] is False
    assert ".gitignore" in status["alignment"]["stale_files"]


def test_ignored_lockfile_still_counts_as_adjacent_profile_evidence(tmp_path):
    write(tmp_path / ".gitignore", "uv.lock\n")
    write(
        tmp_path / "pyproject.toml",
        "\n".join(
            [
                "[project]",
                'dependencies=["pytest"]',
                "[tool.pytest.ini_options]",
                'testpaths=["tests"]',
            ]
        ),
    )
    write(tmp_path / "uv.lock", "version = 1\n")

    profile = probe.sync(tmp_path, tmp_path / "cache.json", format="json")
    root = component(profile, "root")

    assert "uv.lock" in profile["watch"]["files"]
    assert root["package_manager"]["name"] == "uv"
    assert workflows(root, "install")[0]["command"] == "uv sync"


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

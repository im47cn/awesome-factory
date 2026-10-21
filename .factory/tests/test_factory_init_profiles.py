import importlib.util
from pathlib import Path


MODULE = Path(__file__).resolve().parents[2] / "tools" / "factory-init.py"
spec = importlib.util.spec_from_file_location("factory_init_profiles", MODULE)
assert spec and spec.loader
factory_init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factory_init)


def test_detect_project_type_frontend(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    assert factory_init.detect_project_type(tmp_path) == "frontend"


def test_detect_project_type_backend(tmp_path):
    (tmp_path / "pom.xml").write_text("<project />")
    assert factory_init.detect_project_type(tmp_path) == "backend"


def test_detect_project_type_fullstack(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    assert factory_init.detect_project_type(tmp_path) == "fullstack"


def test_unknown_project_fails_closed_to_fullstack(tmp_path):
    assert factory_init.detect_project_type(tmp_path) == "fullstack"


def test_recommended_gate_uses_existing_commands_only(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    assert factory_init.recommended_gate(tmp_path, "frontend") == "pnpm run typecheck"
    assert factory_init.recommended_gate(tmp_path, "backend") == "false"


def test_local_config_marks_remote_side_effects_disabled(tmp_path):
    value = factory_init.gen_factory_local(
        "demo", ["MISSION.md", ".factory/"], "upstream", "repo", "pnpm test", [], None,
        project_type="frontend", execution_mode="local", local_runner="scripts/factory-local-run.py",
    )
    config = __import__("json").loads(value)
    assert config["project_type"] == "frontend"
    assert config["execution_mode"] == "local"
    assert config["remote_side_effects"] is False
    assert config["auto_commit"] is False
    assert config["init_profile"] == "local-minimal"

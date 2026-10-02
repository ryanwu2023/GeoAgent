from scripts.bootstrap_env import ensure_venv, venv_python


def test_project_venv_path_is_inside_repository(tmp_path):
    assert venv_python(tmp_path).parent.parent == tmp_path / ".venv"


def test_existing_project_venv_is_reused(tmp_path):
    target = venv_python(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("stub", encoding="utf-8")
    assert ensure_venv(tmp_path) == target

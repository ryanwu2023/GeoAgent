import subprocess

import pytest

from scripts.crawler_manager import choose_launcher


def test_launcher_prefers_run_new_and_falls_back_to_run(tmp_path):
    project = tmp_path / "collector"
    project.mkdir()
    assert choose_launcher(project) is None
    old = project / "run.bat"
    old.write_text("exit /b 0", encoding="ascii")
    assert choose_launcher(project) == old
    new = project / "run_new.bat"
    new.write_text("exit /b 0", encoding="ascii")
    assert choose_launcher(project) == new


@pytest.mark.skipif(not hasattr(subprocess, "CREATE_NO_WINDOW"), reason="Windows batch test")
def test_windows_cmd_can_execute_selected_launcher(tmp_path):
    launcher = tmp_path / "run_new.bat"
    launcher.write_text("@exit /b 0\n", encoding="ascii")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", launcher.name],
        cwd=tmp_path,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    assert result.returncode == 0

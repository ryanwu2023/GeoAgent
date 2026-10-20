"""Run in the actual candidate image, not just on the developer machine."""
import shutil
import subprocess


def test_curl_transport_is_available_in_collector_runtime():
    binary = shutil.which("curl")
    assert binary, "Finance/TACO curl transport requires curl in the production image"
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True)
    assert "https" in result.stdout.lower()

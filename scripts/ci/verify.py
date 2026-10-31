"""Run the backend suite with per-test PostgreSQL schemas in the built image."""
import sys

import pytest


if __name__ == "__main__":
    sys.exit(pytest.main(["-q", "/app/tests", "-p", "no:cacheprovider",
                          "--junitxml=/reports/junit.xml"]))

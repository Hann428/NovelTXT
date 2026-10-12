from pathlib import Path


def pytest_configure(config):
    config.option.basetemp = Path(__file__).parent / "tmp"

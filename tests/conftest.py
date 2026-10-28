"""Shared fixtures for the WowsGuess plugin test-suite."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_dir():
    """Workspace-local temp directory.

    ``tmp_path`` is unusable in the Windows sandbox used for local development
    because pytest creates its temp dirs with mode ``0o700``, which drops the
    sandbox ACE. Plain ``mkdir`` keeps the inherited ACL.
    """
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

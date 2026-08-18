"""How this suite reacts to a dev environment that is not there.

Kept in its own module rather than in `conftest.py` because the test modules
import it too, and importing from a conftest is a trick nobody should have to
recognise. Nothing here has a side effect at import time - see the note at the
top of `test_lovelace_resources.py` for why that matters.
"""

from __future__ import annotations

import os
from typing import NoReturn

import pytest


def environment_unavailable(reason: str) -> NoReturn:
    """Skip on a workstation, fail under CI.

    Locally, `pytest -m e2e` without a container up should say so and get out of
    the way. In CI the container is always meant to be running, so a skip there
    is a green run that tested nothing - which is exactly how this suite spent
    weeks reporting success while silently skipping every one of its tests.

    Use this for anything that is the environment's fault: a missing seed
    script, a container that will not stop, a store file that is not on disk.
    Not for a deliberate opt-out like `CHOREMANDER_SKIP_DISRUPTIVE`, which is a
    real choice by whoever set it and should skip everywhere.
    """
    if os.environ.get("CI"):
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)

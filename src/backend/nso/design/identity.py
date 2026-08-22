"""Design ID: a deterministic, non-invertible public handle for a design pair."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from typing import List

from ..recipe import DesignRecipe


def design_id_for(recipes: List[DesignRecipe]) -> str:
    """The same patient profile always yields the same ID, and the server can
    look the recipe back up, but the ID itself carries no optical information."""
    blob = "|".join(
        "".join(f"{k}={v}" for k, v in sorted(asdict(r).items())) for r in recipes
    )
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    number = int(digest[:6], 16) % 1000
    letter = chr(ord("A") + int(digest[6:8], 16) % 26)
    return f"NSO-P{number:03d}{letter}"

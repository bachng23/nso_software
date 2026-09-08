"""Design ID: a deterministic, non-invertible public handle for a design pair."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, List

from ..recipe import DesignRecipe


def design_id_for(recipes: List[DesignRecipe], context: Any = None) -> str:
    """The same patient profile always yields the same ID, and the server can
    look the recipe back up, but the ID itself carries no optical information."""
    blob = json.dumps(
        {"recipes": [asdict(recipe) for recipe in recipes], "context": context},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    # 96 bits of digest gives an opaque, unguessable public handle while
    # retaining deterministic idempotency for a repeated fitting request.
    return f"NSO-{digest[:24].upper()}"


def revision_id_for(root_design_id: str, revision: int) -> str:
    """Return an immutable revision handle without changing the root ID."""
    root = root_design_id.split("-R", 1)[0]
    return root if revision == 0 else f"{root}-R{revision}"

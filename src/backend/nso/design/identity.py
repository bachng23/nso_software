"""Design ID: a deterministic, keyed, non-invertible public handle."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import asdict
from typing import Any, List

from ..recipe import DesignRecipe


_DEVELOPMENT_ID_KEY = "nso-local-development-key-not-for-production"


def _identity_key() -> bytes:
    """Return the server-only key used to blind public Design IDs.

    Render generates this value from ``render.yaml``. The fallback keeps local
    tests deterministic, but production deployments must set the environment
    variable so source access cannot enable an offline lookup table.
    """
    return os.environ.get("DESIGN_ID_SECRET", _DEVELOPMENT_ID_KEY).encode("utf-8")


def design_id_for(recipes: List[DesignRecipe], context: Any = None) -> str:
    """The same patient profile always yields the same ID, and the server can
    look the recipe back up, but the ID itself carries no optical information."""
    blob = json.dumps(
        {"recipes": [asdict(recipe) for recipe in recipes], "context": context},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hmac.new(_identity_key(), blob.encode("utf-8"), hashlib.sha256).hexdigest()
    # 96 bits of a keyed digest gives an opaque public handle while retaining
    # deterministic idempotency for a repeated fitting request.
    return f"NSO-{digest[:24].upper()}"


def revision_id_for(root_design_id: str, revision: int) -> str:
    """Return an immutable revision handle without changing the root ID."""
    root = root_design_id.split("-R", 1)[0]
    return root if revision == 0 else f"{root}-R{revision}"

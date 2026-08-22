"""Manufacturing IP layer: geometry projection, segmented release, verification."""

from .geometry import (
    microstructure_element_count,
    microstructure_map,
    microstructure_page_count,
    surface_map,
)
from .registry import REGISTRY, SEGMENT_CODES, VENDOR_SEGMENTS, DesignRegistry
from .store import (
    DesignStore,
    InMemoryDesignStore,
    PostgresDesignStore,
    SQLiteDesignStore,
    default_store,
    store_from_url,
)
from .verification import geometric_verification

__all__ = [
    "microstructure_element_count", "microstructure_map",
    "microstructure_page_count", "surface_map", "REGISTRY", "SEGMENT_CODES",
    "VENDOR_SEGMENTS", "DesignRegistry", "DesignStore", "InMemoryDesignStore",
    "SQLiteDesignStore", "PostgresDesignStore", "default_store", "store_from_url",
    "geometric_verification",
]

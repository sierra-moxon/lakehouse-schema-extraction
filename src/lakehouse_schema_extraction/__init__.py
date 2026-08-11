"""Extract DDL and schema metadata from databases federated behind a Starburst lakehouse.

Trino's own ``information_schema`` is a lowest-common-denominator view across all
connector types, so it exposes no primary keys, foreign keys, or indexes. This package
instead uses each connector's ``system.query`` table function to run native catalog
queries against the underlying database, which does return full constraint metadata.
"""

from lakehouse_schema_extraction.client import LakehouseClient, list_catalogs
from lakehouse_schema_extraction.dialects import get_dialect, registered_dialects

__version__ = "0.1.0"

__all__ = [
    "LakehouseClient",
    "list_catalogs",
    "get_dialect",
    "registered_dialects",
    "__version__",
]

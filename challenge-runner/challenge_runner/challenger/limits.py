"""Caps and frozen vocabularies for the platform challenger.

Failure classes and oracle sources mirror the frozen literals on
``FailureClassLiteral`` and ``OracleSourceLiteral``. The runner stays
stdlib-only, so it cannot import the backend schema modules.
"""

from __future__ import annotations

FAILURE_CLASSES = frozenset(
    {
        "authorization_confidentiality",
        "integrity",
        "injection_execution",
        "denial_of_service",
        "regression",
    }
)
ORACLE_SOURCES = frozenset(
    {
        "property_clause",
        "documented_rule",
        "existing_test",
        "issue_text",
        "customer_rule",
    }
)

# Scenario.test_path in the frozen ClaimIn schema.
TEST_PATH_RE = r"^challenge_tests/[A-Za-z0-9_./-]+\.py$"

MAX_PRECONDITION = 4000
MAX_SETUP = 4000
MAX_ACTION = 4000
MAX_EXPECTED = 4000
MAX_OBSERVED = 4000
MAX_IMPACT = 4000
MAX_REFERENCE = 500
MAX_QUOTE = 2000
MAX_TEST_PATH_CHARS = 200

MAX_CLAIMS_DEFAULT = 5
MAX_COMPLETION_TOKENS = 8192
MAX_MODEL_CHARS = 400_000
MAX_TEST_FILE_BYTES = 48 * 1024
MAX_TEST_TOTAL_BYTES = 192 * 1024
MAX_TEST_FILES_PER_CLAIM = 10
MAX_TEST_FILES_TOTAL = 40

MAX_PROPERTY_CHARS = 8000
MAX_DIFF_CHARS = 32 * 1024
MAX_SOURCE_FILE_BYTES = 8 * 1024
MAX_SOURCE_VIEW_BYTES = 32 * 1024
MAX_FILE_LIST = 400
MAX_WALK_FILES = 5000
MAX_NEAREST_TESTS = 3
MAX_ENV_STRING = 2000
MAX_ENV_ITEMS = 40
MAX_ENV_DEPTH = 4

MAX_RESPONSE_BYTES = 1_000_000

REASON_CODES = frozenset(
    {
        "not_json",
        "oversized_output",
        "not_a_list",
        "not_an_object",
        "unexpected_field",
        "missing_field",
        "invalid_type",
        "invalid_failure_class",
        "invalid_oracle_source",
        "field_too_long",
        "field_empty",
        "unsafe_test_path",
        "test_path_mismatch",
        "missing_test_file",
        "invalid_test_content",
        "test_file_too_large",
        "test_total_too_large",
        "too_many_test_files",
        "duplicate_test_path",
        "over_max_claims",
        "write_failed",
    }
)

ARCHIVE_NAME = "challenger_bundle.tar.gz"
FIXED_TAR_MTIME = 0
FIXED_TAR_MODE = 0o644
FIXED_TAR_UID = 0
FIXED_TAR_GID = 0

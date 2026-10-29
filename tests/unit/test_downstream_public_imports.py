"""Names other repositories import must not be pruned as dead code.

A name that only this repository's tests reference can still be a public
contract: EquityLab imports ``security_identifiers`` from its backtest ledger,
dataset canonicalization and research samplers, and hashes the module source
as part of frozen sample identities.  Deleting it in 0.18.0 made EquityLab
unimportable.  The table below is every ``cnequity`` name EquityLab imports or
reaches through an imported module.  Removing or renaming any of them needs a
deprecation cycle and a changelog entry, not a dead-code sweep.
"""

import importlib
import importlib.util
from pathlib import Path

import pytest

DOWNSTREAM_IMPORTS = {
    "cnequity": ("query",),
    "cnequity.compliance": ("source_policy",),
    "cnequity.compliance.source_policy": (
        "UNKNOWN",
        "aggregate_dataset_policies",
        "is_explicitly_allowed",
        "load_source_policies",
    ),
    "cnequity.config": ("Config",),
    "cnequity.derive.adj_factors": ("STORED_ADJUST_TYPE",),
    "cnequity.domain": ("contracts", "trading_status"),
    "cnequity.domain.action_evidence": ("ex_date_rule_applies",),
    "cnequity.domain.contracts": (
        "build_contract",
        "contract_fingerprint",
        "diff_contracts",
        "format_contract_diff",
    ),
    "cnequity.domain.datasets": ("DATASETS",),
    "cnequity.domain.pit": ("classify_pit_rows",),
    "cnequity.domain.schemas": ("CORPORATE_ACTIONS_SCHEMA", "DATASET_SCHEMAS", "PRIMARY_KEYS"),
    "cnequity.domain.security_identifiers": (
        "SecurityIdentifierChange",
        "canonical_symbol",
        "canonicalize_dated_identifiers",
        "identifier_aliases",
        "identifier_changes",
        "transitions_on",
    ),
    "cnequity.domain.symbols": (
        "ETF_PREFIXES",
        "PREFIX_WHITELIST",
        "is_all_a_symbol",
        "is_cdr_symbol",
        "is_etf_symbol",
        "parse_symbol",
    ),
    "cnequity.domain.trading_status": (
        "EVIDENCE_POINT_IN_TIME",
        "LEGACY_ST_STATUSES",
        "NON_TRADING_STATES",
        "TRADING_STATES",
        "evidence_rank",
        "is_risk_warning",
        "not_trading_expr",
        "risk_warning_expr",
    ),
    "cnequity.domain.universe_profiles": (
        "UniverseProfileError",
        "profile_for_legacy_universe",
        "resolve_universe_profile",
    ),
    "cnequity.orchestrator.manifest": ("Manifest",),
    "cnequity.quality": ("ex_events", "st_coverage"),
    "cnequity.quality.cash_rights": ("reviewed_rights_inventory",),
    "cnequity.quality.ex_events": ("unexplained_factor_steps",),
    "cnequity.quality.historical_validity": ("historical_universe_validity",),
    "cnequity.quality.st_coverage": (
        "current_st_universe",
        "st_evidence_coverage_report",
        "st_evidence_supported_window",
    ),
    "cnequity.query": (
        "DatasetState",
        "dataset_attempt",
        "dataset_schema",
        "dataset_state",
        "reader",
        "resample_minute_history",
    ),
    "cnequity.query.reader": (
        "CURATED_DATASETS",
        "DATE_COLUMNS",
        "DERIVED_DATASETS",
        "ReaderError",
        "load",
    ),
    "cnequity.query.universe": ("EXCLUDED_STATUSES",),
    "cnequity.steps.delisted": ("delisted_coverage_report",),
    "cnequity.storage": ("revisions",),
    "cnequity.storage.revisions": ("RevisionStore",),
    "cnequity.storage.snapshots": ("SnapshotStore",),
    "cnequity.storage.state": ("StateStore",),
}


@pytest.mark.parametrize("module_name", sorted(DOWNSTREAM_IMPORTS))
def test_downstream_imported_names_exist(module_name):
    module = importlib.import_module(module_name)
    missing = [
        name
        for name in DOWNSTREAM_IMPORTS[module_name]
        if not hasattr(module, name) and importlib.util.find_spec(f"{module_name}.{name}") is None
    ]
    assert missing == []


def test_identifier_registry_source_is_hashable():
    """EquityLab binds frozen samples to the bytes of this file."""
    module = importlib.import_module("cnequity.domain.security_identifiers")
    assert Path(module.__file__).is_file()
    assert module.identifier_changes()

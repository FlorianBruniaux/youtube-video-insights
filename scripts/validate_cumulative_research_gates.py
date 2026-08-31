#!/usr/bin/env python3
"""Strictly validate version 1 cumulative-research gate evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
RELEVANCE_STATUSES = {"PASS", "FAIL", "UNKNOWN"}
DISCOVERY_STATUSES = {"PASS", "FAIL", "UNKNOWN"}
REFRESH_STATUSES = {"PASS", "BLOCKED", "UNKNOWN"}


class GateValidationError(ValueError):
    """Raised when gate evidence is not a complete version 1 receipt."""


def _reject_constant(value: str) -> Any:
    raise GateValidationError(f"JSON contains non-standard value {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GateValidationError(f"duplicate key: {key}")
        result[key] = value
    return result


def _object(value: object, location: str, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GateValidationError(f"{location} must be an object")
    actual = set(value)
    if actual != keys:
        unknown = sorted(actual - keys)
        if unknown:
            raise GateValidationError(f"{location} has unknown key: {unknown[0]}")
        raise GateValidationError(f"{location} keys are incomplete")
    return value


def _string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise GateValidationError(f"{location} must be a non-empty string")
    return value


def _sha256(value: object, location: str) -> None:
    if SHA256.fullmatch(_string(value, location)) is None:
        raise GateValidationError(f"{location} must be a lowercase SHA-256")


def _integer(value: object, location: str) -> int:
    if type(value) is not int:
        raise GateValidationError(f"{location} must be an integer")
    return value


def _number(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GateValidationError(f"{location} must be a number")
    return float(value)


def _status(value: object, location: str, allowed: set[str]) -> str:
    status = _string(value, location)
    if status not in allowed:
        raise GateValidationError(f"{location} is invalid")
    return status


def _validate_corpus(value: object) -> None:
    corpus = _object(
        value,
        "corpus",
        {
            "fingerprint_algorithm",
            "fingerprint",
            "vtt_file_count",
            "catalog_sha256_before_after",
            "search_sha256_before_after",
        },
    )
    _string(corpus["fingerprint_algorithm"], "corpus.fingerprint_algorithm")
    _sha256(corpus["fingerprint"], "corpus.fingerprint")
    if _integer(corpus["vtt_file_count"], "corpus.vtt_file_count") < 1:
        raise GateValidationError("corpus.vtt_file_count must be positive")
    _sha256(corpus["catalog_sha256_before_after"], "corpus.catalog_sha256_before_after")
    _sha256(corpus["search_sha256_before_after"], "corpus.search_sha256_before_after")


def _validate_relevance(value: object) -> dict[str, object]:
    relevance = _object(
        value,
        "relevance_pilot",
        {
            "query_count",
            "distinct_subject_count",
            "top_k",
            "expected_rank_1_to_5_judgment_count",
            "observed_rank_1_to_5_result_count",
            "observed_judgment_count",
            "observed_null_judgment_count",
            "observed_relevant_count",
            "packet_status",
            "representative_index",
            "packet_sha256",
        },
    )
    for key in (
        "query_count",
        "distinct_subject_count",
        "top_k",
        "expected_rank_1_to_5_judgment_count",
        "observed_rank_1_to_5_result_count",
        "observed_judgment_count",
        "observed_null_judgment_count",
        "observed_relevant_count",
    ):
        if _integer(relevance[key], f"relevance_pilot.{key}") < 0:
            raise GateValidationError(f"relevance_pilot.{key} must not be negative")
    rank_result_count = _integer(
        relevance["observed_rank_1_to_5_result_count"],
        "relevance_pilot.observed_rank_1_to_5_result_count",
    )
    judgment_count = _integer(
        relevance["observed_judgment_count"],
        "relevance_pilot.observed_judgment_count",
    )
    relevant_count = _integer(
        relevance["observed_relevant_count"],
        "relevance_pilot.observed_relevant_count",
    )
    if judgment_count > rank_result_count:
        raise GateValidationError(
            "relevance_pilot.observed_judgment_count must not exceed "
            "observed_rank_1_to_5_result_count"
        )
    if relevant_count > judgment_count:
        raise GateValidationError(
            "relevance_pilot.observed_relevant_count must not exceed "
            "observed_judgment_count"
        )
    _status(relevance["packet_status"], "relevance_pilot.packet_status", RELEVANCE_STATUSES)
    index = _object(
        relevance["representative_index"],
        "relevance_pilot.representative_index",
        {"sha256", "size_bytes", "documents_indexed", "passages_indexed"},
    )
    _sha256(index["sha256"], "relevance_pilot.representative_index.sha256")
    for key in ("size_bytes", "documents_indexed", "passages_indexed"):
        if _integer(index[key], f"relevance_pilot.representative_index.{key}") < 0:
            raise GateValidationError(f"relevance_pilot.representative_index.{key} must not be negative")
    _sha256(relevance["packet_sha256"], "relevance_pilot.packet_sha256")
    return relevance


def _validate_discovery(value: object) -> dict[str, object]:
    discovery = _object(
        value,
        "discovery_probe",
        {"tool", "source_prefix", "local_state_unchanged", "subjects"},
    )
    _string(discovery["tool"], "discovery_probe.tool")
    _string(discovery["source_prefix"], "discovery_probe.source_prefix")
    if type(discovery["local_state_unchanged"]) is not bool:
        raise GateValidationError("discovery_probe.local_state_unchanged must be boolean")
    subjects = discovery["subjects"]
    if not isinstance(subjects, list) or len(subjects) != 3:
        raise GateValidationError("discovery_probe.subjects must contain exactly 3 items")
    seen_subject_ids: set[str] = set()
    for position, subject_value in enumerate(subjects, start=1):
        subject = _object(
            subject_value,
            f"discovery_probe.subjects[{position}]",
            {"subject_id", "query", "exit_code", "candidate_count", "publication_dates_available"},
        )
        subject_id = _string(subject["subject_id"], f"discovery_probe.subjects[{position}].subject_id")
        if subject_id in seen_subject_ids:
            raise GateValidationError("discovery_probe.subjects subject_id must be unique")
        seen_subject_ids.add(subject_id)
        _string(subject["query"], f"discovery_probe.subjects[{position}].query")
        _integer(subject["exit_code"], f"discovery_probe.subjects[{position}].exit_code")
        if _integer(subject["candidate_count"], f"discovery_probe.subjects[{position}].candidate_count") < 0:
            raise GateValidationError("discovery_probe candidate_count must not be negative")
        if type(subject["publication_dates_available"]) is not bool:
            raise GateValidationError("discovery_probe publication_dates_available must be boolean")
    return discovery


def _validate_refresh(value: object) -> dict[str, object]:
    refresh = _object(
        value,
        "refresh_performance",
        {"sample_count", "p95_wall_seconds", "threshold_seconds", "incremental_refresh_required", "samples"},
    )
    if _integer(refresh["sample_count"], "refresh_performance.sample_count") != 5:
        raise GateValidationError("refresh_performance.sample_count must equal 5")
    if _number(refresh["p95_wall_seconds"], "refresh_performance.p95_wall_seconds") < 0:
        raise GateValidationError("refresh_performance.p95_wall_seconds must not be negative")
    if _number(refresh["threshold_seconds"], "refresh_performance.threshold_seconds") != 60:
        raise GateValidationError("refresh_performance.threshold_seconds must equal 60")
    if type(refresh["incremental_refresh_required"]) is not bool:
        raise GateValidationError("refresh_performance.incremental_refresh_required must be boolean")
    samples = refresh["samples"]
    if not isinstance(samples, list) or len(samples) != 5:
        raise GateValidationError("refresh_performance.samples must contain exactly 5 items")
    expected_runs = set(range(1, 6))
    actual_runs: set[int] = set()
    for position, sample_value in enumerate(samples, start=1):
        sample = _object(
            sample_value,
            f"refresh_performance.samples[{position}]",
            {"run", "wall_seconds", "exit_code", "validation_exit_code", "documents_indexed", "passages_indexed", "database_size_bytes", "database_sha256"},
        )
        actual_runs.add(_integer(sample["run"], f"refresh_performance.samples[{position}].run"))
        for key in ("wall_seconds",):
            if _number(sample[key], f"refresh_performance.samples[{position}].{key}") < 0:
                raise GateValidationError(f"refresh_performance.samples[{position}].{key} must not be negative")
        for key in ("exit_code", "validation_exit_code", "documents_indexed", "passages_indexed", "database_size_bytes"):
            if _integer(sample[key], f"refresh_performance.samples[{position}].{key}") < 0:
                raise GateValidationError(f"refresh_performance.samples[{position}].{key} must not be negative")
        _sha256(sample["database_sha256"], f"refresh_performance.samples[{position}].database_sha256")
    if actual_runs != expected_runs:
        raise GateValidationError("refresh_performance.samples must contain runs 1 through 5")
    return refresh


def _validate_artifacts(value: object) -> None:
    artifacts = _object(value, "artifacts", {"pilot_queries_sha256", "raw_evidence_directory", "raw_packet_path"})
    _sha256(artifacts["pilot_queries_sha256"], "artifacts.pilot_queries_sha256")
    _string(artifacts["raw_evidence_directory"], "artifacts.raw_evidence_directory")
    _string(artifacts["raw_packet_path"], "artifacts.raw_packet_path")


def validate(payload: object) -> None:
    evidence = _object(
        payload,
        "evidence",
        {"schema_version", "code_sha", "corpus", "gates", "relevance_pilot", "discovery_probe", "refresh_performance", "artifacts"},
    )
    if evidence["schema_version"] != 1:
        raise GateValidationError("schema_version must equal 1")
    if GIT_SHA.fullmatch(_string(evidence["code_sha"], "code_sha")) is None:
        raise GateValidationError("code_sha must be a lowercase 40-character Git SHA")
    _validate_corpus(evidence["corpus"])
    gates = _object(evidence["gates"], "gates", {"relevance_pilot", "discovery_probe", "refresh_performance", "global_activation_ready"})
    relevance_status = _status(gates["relevance_pilot"], "gates.relevance_pilot", RELEVANCE_STATUSES)
    discovery_status = _status(gates["discovery_probe"], "gates.discovery_probe", DISCOVERY_STATUSES)
    refresh_status = _status(gates["refresh_performance"], "gates.refresh_performance", REFRESH_STATUSES)
    if type(gates["global_activation_ready"]) is not bool:
        raise GateValidationError("gates.global_activation_ready must be boolean")
    if gates["global_activation_ready"] and not all(status == "PASS" for status in (relevance_status, discovery_status, refresh_status)):
        raise GateValidationError("gates.global_activation_ready requires every external gate to PASS")
    relevance = _validate_relevance(evidence["relevance_pilot"])
    discovery = _validate_discovery(evidence["discovery_probe"])
    refresh = _validate_refresh(evidence["refresh_performance"])
    if discovery_status == "PASS":
        if discovery["local_state_unchanged"] is not True:
            raise GateValidationError("discovery_probe.local_state_unchanged must be true when discovery passes")
        for position, subject_value in enumerate(discovery["subjects"], start=1):
            assert isinstance(subject_value, dict)
            if subject_value["exit_code"] != 0:
                raise GateValidationError(f"discovery_probe.subjects[{position}].exit_code must equal 0 when discovery passes")
            if subject_value["candidate_count"] < 5:
                raise GateValidationError(f"discovery_probe.subjects[{position}].candidate_count must be at least 5 when discovery passes")
    if relevance_status == "PASS":
        if relevance["packet_status"] != "PASS":
            raise GateValidationError("relevance_pilot.packet_status must equal PASS when relevance passes")
        if relevance["observed_rank_1_to_5_result_count"] != 20:
            raise GateValidationError("relevance_pilot.observed_rank_1_to_5_result_count must equal 20 when relevance passes")
        if relevance["observed_judgment_count"] != 20:
            raise GateValidationError("relevance_pilot.observed_judgment_count must equal 20 when relevance passes")
        if relevance["observed_null_judgment_count"] != 0:
            raise GateValidationError("relevance_pilot.observed_null_judgment_count must equal 0 when relevance passes")
        if relevance["observed_relevant_count"] < 16:
            raise GateValidationError("relevance_pilot.observed_relevant_count must be at least 16 when relevance passes")
    p95 = _number(refresh["p95_wall_seconds"], "refresh_performance.p95_wall_seconds")
    incremental_required = refresh["incremental_refresh_required"]
    if incremental_required != (p95 > 60):
        raise GateValidationError("refresh_performance.incremental_refresh_required must match whether p95_wall_seconds exceeds 60")
    if refresh_status == "PASS" and (p95 > 60 or incremental_required):
        raise GateValidationError("gates.refresh_performance PASS requires p95_wall_seconds at most 60 and incremental_refresh_required false")
    if refresh_status == "PASS":
        for position, sample_value in enumerate(refresh["samples"], start=1):
            assert isinstance(sample_value, dict)
            if sample_value["exit_code"] != 0:
                raise GateValidationError(f"refresh_performance.samples[{position}].exit_code must equal 0 when refresh passes")
            if sample_value["validation_exit_code"] != 0:
                raise GateValidationError(f"refresh_performance.samples[{position}].validation_exit_code must equal 0 when refresh passes")
    _validate_artifacts(evidence["artifacts"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate cumulative research gate evidence.")
    parser.add_argument("evidence", type=Path)
    arguments = parser.parse_args()
    try:
        decoded = json.loads(
            arguments.evidence.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
        validate(decoded)
    except (GateValidationError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(f"gate evidence invalid: {error}", file=sys.stderr)
        return 2
    print("gate evidence valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

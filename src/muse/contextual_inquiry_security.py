"""Windows DPAPI protection for short-lived Muse contextual case payloads.

Only ciphertext envelopes are intended to cross a persistence boundary. This
module deliberately has no plaintext fallback and does not manage storage.
"""
from __future__ import annotations

import base64
import binascii
import os
from typing import Any
from muse.error_journal import record_suppressed_error


ENVELOPE_VERSION = 1
PROTECTION_SCHEME = "windows-dpapi-current-user"
MAX_PLAINTEXT_BYTES = 2 * 1024 * 1024
_MAX_CIPHERTEXT_CHARS = 4 * 1024 * 1024
_ENTROPY = b"Muse.ContextualInquiry.v1"


class CaseProtectionError(RuntimeError):
    """Raised when case data cannot be protected or validated safely."""


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise CaseProtectionError("contextual case protection requires Windows DPAPI")
    try:
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte)),
            ]

        def make_blob(value: bytes) -> tuple[DataBlob, Any]:
            buffer = ctypes.create_string_buffer(value, len(value))
            blob = DataBlob(
                len(value),
                ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
            )
            return blob, buffer

        input_blob, input_buffer = make_blob(data)
        entropy_blob, entropy_buffer = make_blob(_ENTROPY)
        output_blob = DataBlob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        local_free = kernel32.LocalFree
        local_free.argtypes = [wintypes.HLOCAL]
        local_free.restype = wintypes.HLOCAL
        flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN; CurrentUser is the default scope.

        if protect:
            operation = crypt32.CryptProtectData
            operation.argtypes = [
                ctypes.POINTER(DataBlob), wintypes.LPCWSTR,
                ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                wintypes.DWORD, ctypes.POINTER(DataBlob),
            ]
            operation.restype = wintypes.BOOL
            succeeded = operation(
                ctypes.byref(input_blob), None, ctypes.byref(entropy_blob),
                None, None, flags, ctypes.byref(output_blob),
            )
        else:
            operation = crypt32.CryptUnprotectData
            operation.argtypes = [
                ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR),
                ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                wintypes.DWORD, ctypes.POINTER(DataBlob),
            ]
            operation.restype = wintypes.BOOL
            description = wintypes.LPWSTR()
            succeeded = operation(
                ctypes.byref(input_blob), ctypes.byref(description),
                ctypes.byref(entropy_blob), None, None, flags,
                ctypes.byref(output_blob),
            )
            if description:
                local_free(ctypes.cast(description, wintypes.HLOCAL))

        if not succeeded:
            raise OSError(ctypes.get_last_error(), "Windows DPAPI call failed")
        if not output_blob.pbData or output_blob.cbData <= 0:
            raise OSError("Windows DPAPI returned an empty result")
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    except CaseProtectionError:
        raise
    except Exception as exc:
        raise CaseProtectionError("Windows DPAPI operation failed; no plaintext fallback is permitted") from exc
    finally:
        # Keep caller-owned buffers alive through the native call.
        _ = (input_buffer, entropy_buffer)
        if "output_blob" in locals() and output_blob.pbData:
            try:
                kernel32.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))
            except Exception as _muse_suppressed:
                record_suppressed_error("contextual_inquiry_security.py:100", _muse_suppressed)


def protect_case_bytes(plaintext: bytes) -> dict[str, Any]:
    """Return a strict, versioned DPAPI CurrentUser envelope for non-empty bytes."""
    if not isinstance(plaintext, bytes) or not plaintext:
        raise ValueError("case plaintext must be non-empty bytes")
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError("case plaintext exceeds the 2 MiB protection limit")
    ciphertext = _dpapi_transform(plaintext, protect=True)
    encoded = base64.b64encode(ciphertext).decode("ascii")
    if len(encoded) > _MAX_CIPHERTEXT_CHARS:
        raise CaseProtectionError("protected case envelope exceeds the size limit")
    return {
        "version": ENVELOPE_VERSION,
        "protection": PROTECTION_SCHEME,
        "ciphertext_b64": encoded,
    }


def _serialize_contextual_inquiry(inquiry: Any) -> bytes:
    """Canonicalize a validated inquiry without carrying long-term-storage consent."""
    from dataclasses import asdict
    from enum import Enum
    import json

    from muse.contextual_inquiry import ContextualInquiry

    if not isinstance(inquiry, ContextualInquiry):
        raise TypeError("inquiry must be a ContextualInquiry")
    payload = asdict(inquiry)
    # This flag authorizes a separate knowledge-memory operation, not case-vault storage.
    payload.pop("consent_to_store", None)

    def normalize(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {str(key): normalize(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [normalize(item) for item in value]
        return value

    try:
        encoded = json.dumps(
            normalize(payload), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("contextual inquiry cannot be serialized safely") from exc
    if not encoded or len(encoded) > MAX_PLAINTEXT_BYTES:
        raise ValueError("serialized contextual inquiry is empty or exceeds the 2 MiB limit")
    return encoded


def protect_contextual_inquiry(
    inquiry: Any, *, consent: bool = False,
) -> dict[str, Any]:
    """Protect a complete inquiry only after a separate explicit case-storage consent."""
    if consent is not True:
        raise PermissionError("explicit case_storage_consent is required")
    return protect_case_bytes(_serialize_contextual_inquiry(inquiry))


def deserialize_contextual_inquiry(plaintext: bytes) -> Any:
    """Decode and fully revalidate one canonical encrypted inquiry payload."""
    import json

    from muse.contextual_inquiry import (
        ActionExperiment,
        ContextualInquiry,
        Hypothesis,
        Observation,
        SafetyRoute,
        SourceKind,
        StakeholderView,
        ViewStatus,
    )

    if not isinstance(plaintext, bytes) or not plaintext or len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError("decrypted contextual inquiry is empty or exceeds the size limit")
    try:
        data = json.loads(
            plaintext.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CaseProtectionError("decrypted contextual inquiry is not valid canonical JSON") from exc
    expected = {
        "inquiry_id", "user_goal", "observations", "hypotheses", "unknowns",
        "stakeholder_views", "action_experiments", "safety_route", "schema_version",
    }
    if not isinstance(data, dict) or set(data) != expected:
        raise CaseProtectionError("decrypted contextual inquiry has an unsupported schema")
    list_fields = (
        "observations", "hypotheses", "unknowns", "stakeholder_views", "action_experiments",
    )
    if any(not isinstance(data[field], list) for field in list_fields):
        raise CaseProtectionError("decrypted contextual inquiry contains invalid collection fields")
    try:
        observations = tuple(
            Observation(
                observation_id=item["observation_id"],
                source_kind=SourceKind(item["source_kind"]),
                content=item["content"],
                source_ref=item["source_ref"],
                observed_at=item["observed_at"],
            )
            for item in data["observations"]
        )
        hypotheses = tuple(
            Hypothesis(
                hypothesis_id=item["hypothesis_id"],
                statement=item["statement"],
                source_refs=tuple(item["source_refs"]),
                counter_refs=tuple(item["counter_refs"]),
                confidence=item["confidence"],
                unsourced=item["unsourced"],
                falsification_condition=item["falsification_condition"],
            )
            for item in data["hypotheses"]
        )
        stakeholder_views = tuple(
            StakeholderView(
                stakeholder_id=item["stakeholder_id"],
                status=ViewStatus(item["status"]),
                statement=item["statement"],
                observation_ref=item["observation_ref"],
            )
            for item in data["stakeholder_views"]
        )
        action_experiments = tuple(
            ActionExperiment(
                experiment_id=item["experiment_id"],
                owner=item["owner"],
                due_date=item["due_date"],
                resource_constraints=tuple(item["resource_constraints"]),
                expected_signal=item["expected_signal"],
                stop_condition=item["stop_condition"],
                reversible=item["reversible"],
            )
            for item in data["action_experiments"]
        )
        inquiry = ContextualInquiry(
            inquiry_id=data["inquiry_id"],
            user_goal=data["user_goal"],
            observations=observations,
            hypotheses=hypotheses,
            unknowns=tuple(data["unknowns"]),
            stakeholder_views=stakeholder_views,
            action_experiments=action_experiments,
            safety_route=SafetyRoute(data["safety_route"]),
            consent_to_store=False,
            schema_version=data["schema_version"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CaseProtectionError("decrypted contextual inquiry failed schema validation") from exc
    if _serialize_contextual_inquiry(inquiry) != plaintext:
        raise CaseProtectionError("decrypted contextual inquiry is not in canonical form")
    return inquiry




def deserialize_contextual_inquiry_review_result(plaintext: bytes, inquiry: Any) -> dict[str, Any]:
    """Validate a canonical, inquiry-bound review result without persisting plaintext.

    Findings are deliberately limited to candidate assessments. This schema cannot
    assert that a claim is verified, supported, or refuted.
    """
    import json

    from muse.contextual_inquiry import ContextualInquiry

    if not isinstance(inquiry, ContextualInquiry):
        raise TypeError("inquiry must be a ContextualInquiry")
    if not isinstance(plaintext, bytes) or not plaintext or len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError("review result is empty or exceeds the size limit")
    try:
        data = json.loads(
            plaintext.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CaseProtectionError("decrypted review result is not valid JSON") from exc

    if not isinstance(data, dict) or set(data) != {
        "schema_version", "inquiry_id", "summary", "findings", "unknowns",
    }:
        raise CaseProtectionError("decrypted review result has an unsupported schema")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise CaseProtectionError("unsupported contextual inquiry review-result schema version")
    if data["inquiry_id"] != inquiry.inquiry_id:
        raise CaseProtectionError("review result does not match the encrypted inquiry")
    if not isinstance(data["summary"], str) or not data["summary"].strip() or len(data["summary"]) > 4000:
        raise CaseProtectionError("review result summary is invalid")
    if not isinstance(data["findings"], list) or not 1 <= len(data["findings"]) <= 100:
        raise CaseProtectionError("review result findings must contain between 1 and 100 entries")
    if not isinstance(data["unknowns"], list) or len(data["unknowns"]) > 100:
        raise CaseProtectionError("review result unknowns are invalid")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 2000 for item in data["unknowns"]):
        raise CaseProtectionError("review result contains an invalid unknown")

    observation_ids = {item.observation_id for item in inquiry.observations}
    hypothesis_ids = {item.hypothesis_id for item in inquiry.hypotheses}
    finding_ids: set[str] = set()
    allowed_assessments = {"support_candidate", "counter_candidate", "unresolved"}
    for finding in data["findings"]:
        if not isinstance(finding, dict) or set(finding) != {
            "finding_id", "statement", "observation_refs", "hypothesis_refs", "assessment", "rationale",
        }:
            raise CaseProtectionError("review result contains an unsupported finding shape")
        finding_id = finding["finding_id"]
        if (
            not isinstance(finding_id, str) or not finding_id.strip() or len(finding_id) > 80
            or finding_id in finding_ids
        ):
            raise CaseProtectionError("review result finding identifier is invalid or duplicated")
        finding_ids.add(finding_id)
        if not isinstance(finding["statement"], str) or not finding["statement"].strip() or len(finding["statement"]) > 2000:
            raise CaseProtectionError("review result finding statement is invalid")
        if not isinstance(finding["rationale"], str) or not finding["rationale"].strip() or len(finding["rationale"]) > 4000:
            raise CaseProtectionError("review result finding rationale is invalid")
        assessment = finding["assessment"]
        if not isinstance(assessment, str) or assessment not in allowed_assessments:
            raise CaseProtectionError("review result assessment cannot promote evidence to a verdict")
        for field, known in (("observation_refs", observation_ids), ("hypothesis_refs", hypothesis_ids)):
            refs = finding[field]
            if (
                not isinstance(refs, list) or len(refs) > 100
                or any(not isinstance(ref, str) or ref not in known for ref in refs)
                or len(set(refs)) != len(refs)
            ):
                raise CaseProtectionError(f"review result contains invalid {field}")

    try:
        canonical = json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CaseProtectionError("review result cannot be canonicalized safely") from exc
    if canonical != plaintext:
        raise CaseProtectionError("review result is not in canonical form")
    return data


def unprotect_case_bytes(envelope: dict[str, Any]) -> bytes:
    """Validate and decrypt one exact envelope under the current Windows user."""
    if not isinstance(envelope, dict) or set(envelope) != {
        "version", "protection", "ciphertext_b64",
    }:
        raise ValueError("case protection envelope has an unsupported shape")
    version = envelope["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != ENVELOPE_VERSION:
        raise ValueError("unsupported case protection envelope version")
    if envelope["protection"] != PROTECTION_SCHEME:
        raise ValueError("unsupported case protection scheme")
    encoded = envelope["ciphertext_b64"]
    if not isinstance(encoded, str) or not encoded or len(encoded) > _MAX_CIPHERTEXT_CHARS:
        raise ValueError("case protection ciphertext is invalid or too large")
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("case protection ciphertext is not valid base64") from exc
    plaintext = _dpapi_transform(ciphertext, protect=False)
    if not plaintext or len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise CaseProtectionError("decrypted case payload is empty or exceeds the size limit")
    return plaintext

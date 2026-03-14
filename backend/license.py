# -*- coding: utf-8 -*-
"""
License management for modAI Trader.

Supports two license modes:
1) Signed, device-bound tokens (recommended for production)
2) Legacy licenses stored in licenses.json (backward compatibility)
"""

import base64
import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

SIGNED_LICENSE_PREFIX = "MODAI1"
LICENSE_KIND_PAID = "paid"
LICENSE_KIND_TRIAL = "trial"
DEFAULT_TRIAL_DAYS = 30
MIN_TRIAL_DAYS = 1
MAX_TRIAL_DAYS = 365


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_after_days_iso(days: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(days=days))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_iso_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_license_kind(kind: Optional[str]) -> str:
    candidate = str(kind or "").strip().lower()
    if candidate in {"trial", "free", "test"}:
        return LICENSE_KIND_TRIAL
    return LICENSE_KIND_PAID


def _normalize_trial_days(days: Any) -> int:
    try:
        normalized = int(days)
    except Exception:
        normalized = DEFAULT_TRIAL_DAYS
    return max(MIN_TRIAL_DAYS, min(MAX_TRIAL_DAYS, normalized))


def _get_data_dir() -> Path:
    base_dir = Path(os.getenv("MODAI_DATA_DIR", Path(__file__).parent.parent))
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        base_dir = Path(__file__).parent.parent
    return base_dir


def _private_key_path() -> Path:
    env_path = os.getenv("MODAI_LICENSE_PRIVATE_KEY_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return Path(__file__).parent.parent / ".license_private.pem"


def _public_key_candidates() -> List[Path]:
    candidates: List[Path] = []
    env_path = os.getenv("MODAI_LICENSE_PUBLIC_KEY_PATH", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    # Preferred: shipped with backend code and packaged into the app bundle.
    candidates.append(Path(__file__).with_name("license_public.pem"))
    # Fallback: user data directory.
    candidates.append(_get_data_dir() / "license_public.pem")
    return candidates


def _license_state_file() -> Path:
    return _get_data_dir() / "license_state.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _ensure_license_file() -> Path:
    data_dir = _get_data_dir()
    licenses_file = data_dir / "licenses.json"
    if licenses_file.exists():
        return licenses_file

    bundle_path = os.getenv("MODAI_LICENSES_BUNDLE")
    if bundle_path:
        bundle_file = Path(bundle_path)
        if bundle_file.exists():
            try:
                shutil.copy(bundle_file, licenses_file)
                return licenses_file
            except Exception:
                pass

    fallback = Path(__file__).parent.parent / "licenses.json"
    if fallback.exists() and fallback != licenses_file:
        try:
            shutil.copy(fallback, licenses_file)
        except Exception:
            return fallback
    return licenses_file


LICENSES_FILE = _ensure_license_file()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def _load_private_signing_key() -> Ed25519PrivateKey:
    private_path = _private_key_path()
    if not private_path.exists():
        raise FileNotFoundError(
            f"Private signing key not found at {private_path}. "
            "Run create_license.py or license_admin.py to generate it on your secure machine."
        )
    private_key = serialization.load_pem_private_key(
        private_path.read_bytes(), password=None
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("Private key must be an Ed25519 private key")
    return private_key


def _load_public_signing_key() -> Ed25519PublicKey:
    for candidate in _public_key_candidates():
        if not candidate.exists():
            continue
        public_key = serialization.load_pem_public_key(candidate.read_bytes())
        if isinstance(public_key, Ed25519PublicKey):
            return public_key
    raise FileNotFoundError(
        "Public signing key not found. Expected backend/license_public.pem "
        "or MODAI_LICENSE_PUBLIC_KEY_PATH."
    )


def ensure_signing_keys() -> Dict[str, str]:
    """
    Ensure admin signing keys exist.
    - If private+public exist -> no-op
    - If both missing -> generate a new Ed25519 key pair
    - If public exists but private missing -> fail (cannot recover matching private key)
    """
    private_path = _private_key_path()
    public_path = _public_key_candidates()[0] if os.getenv("MODAI_LICENSE_PUBLIC_KEY_PATH", "").strip() else Path(__file__).with_name("license_public.pem")

    if private_path.exists():
        private_key = _load_private_signing_key()
        if not public_path.exists():
            public_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            public_path.parent.mkdir(parents=True, exist_ok=True)
            public_path.write_bytes(public_bytes)
        return {"private_key": str(private_path), "public_key": str(public_path)}

    if public_path.exists():
        raise FileNotFoundError(
            f"Public key exists at {public_path} but private key is missing at {private_path}. "
            "Restore your private key backup."
        )

    private_key = ed25519.Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(private_bytes)
    try:
        os.chmod(private_path, 0o600)
    except Exception:
        pass

    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_bytes(public_bytes)
    return {"private_key": str(private_path), "public_key": str(public_path)}


def generate_license_key() -> str:
    """Generate legacy-style random license key"""
    random_bytes = secrets.token_bytes(16)
    key = hashlib.sha256(random_bytes).hexdigest()[:32].upper()
    return "-".join([key[i : i + 4] for i in range(0, 32, 4)])


def _load_legacy_licenses() -> List[Dict[str, Any]]:
    payload = _load_json(LICENSES_FILE, [])
    return payload if isinstance(payload, list) else []


def _save_legacy_licenses(licenses: List[Dict[str, Any]]) -> None:
    _save_json(LICENSES_FILE, licenses)


def _build_signed_payload(
    payment_tx: str,
    crypto: str,
    amount: float,
    device_id: str,
    license_kind: str = LICENSE_KIND_PAID,
    trial_days: int = DEFAULT_TRIAL_DAYS,
) -> Dict[str, Any]:
    normalized_kind = _normalize_license_kind(license_kind)
    normalized_trial_days = _normalize_trial_days(trial_days)
    expires_at = (
        _utc_after_days_iso(normalized_trial_days)
        if normalized_kind == LICENSE_KIND_TRIAL
        else None
    )
    return {
        "v": 1,
        "license_id": secrets.token_hex(12),
        "device_id": str(device_id).strip(),
        "payment_tx": str(payment_tx).strip(),
        "crypto": str(crypto).strip().upper(),
        "amount": float(amount),
        "issued_at": _utc_now_iso(),
        "expires_at": expires_at,
        "license_kind": normalized_kind,
        "trial_days": normalized_trial_days if normalized_kind == LICENSE_KIND_TRIAL else None,
    }


def _build_signed_license_key(payload: Dict[str, Any]) -> str:
    private_key = _load_private_signing_key()
    payload_raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload_raw)
    return f"{SIGNED_LICENSE_PREFIX}.{_b64url_encode(payload_raw)}.{_b64url_encode(signature)}"


def _parse_signed_license_key(key: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    value = str(key or "").strip()
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != SIGNED_LICENSE_PREFIX:
        return None, "Invalid signed license format"

    try:
        payload_raw = _b64url_decode(parts[1])
        signature_raw = _b64url_decode(parts[2])
    except Exception:
        return None, "Malformed signed license payload"

    try:
        public_key = _load_public_signing_key()
        public_key.verify(signature_raw, payload_raw)
    except Exception:
        return None, "Signed license verification failed"

    try:
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception:
        return None, "Invalid signed license JSON payload"

    if not isinstance(payload, dict):
        return None, "Invalid signed license payload type"
    return payload, None


def _persist_local_license_state(
    key: str,
    device_id: str,
    license_mode: str,
    payload: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> None:
    record = {
        "key": key,
        "device_id": device_id,
        "mode": license_mode,
        "created_at": created_at or _utc_now_iso(),
        "activated_at": _utc_now_iso(),
        "key_hash": hashlib.sha256(key.encode("utf-8")).hexdigest(),
    }
    if payload is not None:
        record["payload"] = payload
    state_path = _license_state_file()
    _save_json(state_path, record)
    try:
        os.chmod(state_path, 0o600)
    except Exception:
        pass


def get_local_license_state() -> Dict[str, Any]:
    payload = _load_json(_license_state_file(), {})
    return payload if isinstance(payload, dict) else {}


def _validate_legacy_license(
    key: str, device_id: str, allow_bind: bool = True, persist_state: bool = True
) -> Dict[str, Any]:
    licenses = _load_legacy_licenses()

    for idx, lic in enumerate(licenses):
        if str(lic.get("key", "")).strip() != key:
            continue

        if not bool(lic.get("active", True)):
            return {"valid": False, "message": "License deactivated", "license": None}

        bound_device = str(lic.get("device_id") or "").strip()
        if not bound_device:
            if not allow_bind:
                return {
                    "valid": False,
                    "message": "License not activated on this device",
                    "license": None,
                }
            lic["device_id"] = device_id
            lic["activated_at"] = _utc_now_iso()
            licenses[idx] = lic
            _save_legacy_licenses(licenses)
            if persist_state:
                _persist_local_license_state(
                    key=key,
                    device_id=device_id,
                    license_mode="legacy",
                    created_at=str(lic.get("created_at") or _utc_now_iso()),
                )
            return {
                "valid": True,
                "message": "License activated successfully",
                "license": lic,
            }

        if bound_device == device_id:
            if persist_state:
                _persist_local_license_state(
                    key=key,
                    device_id=device_id,
                    license_mode="legacy",
                    created_at=str(lic.get("created_at") or _utc_now_iso()),
                )
            return {"valid": True, "message": "License valid", "license": lic}

        return {
            "valid": False,
            "message": "License already activated on another device",
            "license": None,
        }

    return {"valid": False, "message": "Invalid license key", "license": None}


def _validate_signed_license(
    key: str, device_id: str, persist_state: bool = True
) -> Dict[str, Any]:
    payload, parse_error = _parse_signed_license_key(key)
    if parse_error:
        return {"valid": False, "message": parse_error, "license": None}
    if payload is None:
        return {"valid": False, "message": "Invalid signed license payload", "license": None}

    expected_device = str(payload.get("device_id") or "").strip()
    if not expected_device:
        return {"valid": False, "message": "Signed license missing device binding", "license": None}
    if expected_device != device_id:
        return {
            "valid": False,
            "message": "License is bound to another device",
            "license": None,
        }

    expires_at = payload.get("expires_at")
    if expires_at:
        try:
            expires = _parse_iso_datetime(expires_at)
            if datetime.now(timezone.utc) > expires:
                return {"valid": False, "message": "License expired", "license": None}
        except Exception:
            return {"valid": False, "message": "Invalid license expiration format", "license": None}

    license_kind = _normalize_license_kind(str(payload.get("license_kind") or LICENSE_KIND_PAID))
    license_info = {
        "key": key,
        "created_at": str(payload.get("issued_at") or _utc_now_iso()),
        "activated_at": _utc_now_iso(),
        "device_id": expected_device,
        "payment_tx": payload.get("payment_tx"),
        "crypto": payload.get("crypto"),
        "amount": payload.get("amount"),
        "license_id": payload.get("license_id"),
        "license_kind": license_kind,
        "expires_at": payload.get("expires_at"),
        "trial_days": payload.get("trial_days"),
        "active": True,
    }

    if persist_state:
        _persist_local_license_state(
            key=key,
            device_id=device_id,
            license_mode="signed",
            payload=payload,
            created_at=license_info["created_at"],
        )

    return {"valid": True, "message": "License valid", "license": license_info}


def create_license(
    payment_tx: str,
    crypto: str,
    amount: float,
    device_id: Optional[str] = None,
    license_kind: str = LICENSE_KIND_PAID,
    trial_days: int = DEFAULT_TRIAL_DAYS,
) -> str:
    """
    Create a license key.
    - If device_id is provided: creates a signed, device-bound key (recommended).
    - If omitted: creates a legacy random key stored in licenses.json.
    """
    clean_device = str(device_id or "").strip()
    normalized_kind = _normalize_license_kind(license_kind)
    normalized_trial_days = _normalize_trial_days(trial_days)
    if clean_device:
        ensure_signing_keys()
        payload = _build_signed_payload(
            payment_tx,
            crypto,
            amount,
            clean_device,
            license_kind=normalized_kind,
            trial_days=normalized_trial_days,
        )
        license_key = _build_signed_license_key(payload)

        # Keep an admin audit trail in licenses.json for operational visibility.
        licenses = _load_legacy_licenses()
        licenses.append(
            {
                "key": license_key,
                "type": "signed",
                "license_id": payload.get("license_id"),
                "payment_tx": payment_tx,
                "crypto": str(crypto).strip().upper(),
                "amount": float(amount),
                "created_at": payload.get("issued_at"),
                "expires_at": payload.get("expires_at"),
                "license_kind": payload.get("license_kind"),
                "trial_days": payload.get("trial_days"),
                "active": True,
                "device_id": clean_device,
                "activated_at": payload.get("issued_at"),
            }
        )
        _save_legacy_licenses(licenses)
        return license_key

    # Legacy behavior (kept only for compatibility)
    license_key = generate_license_key()
    license_data = {
        "key": license_key,
        "type": "legacy",
        "license_kind": LICENSE_KIND_PAID,
        "payment_tx": payment_tx,
        "crypto": crypto,
        "amount": amount,
        "created_at": _utc_now_iso(),
        "expires_at": None,
        "active": True,
        "device_id": None,
        "activated_at": None,
    }
    licenses = _load_legacy_licenses()
    licenses.append(license_data)
    _save_legacy_licenses(licenses)
    return license_key


def validate_license(key: str, device_id: str) -> Dict[str, Any]:
    """
    Validate license and bind/confirm it for the current device.
    """
    clean_key = str(key or "").strip()
    clean_device = str(device_id or "").strip()
    if not clean_key:
        return {"valid": False, "message": "License key is required", "license": None}
    if not clean_device:
        return {"valid": False, "message": "Device id is required", "license": None}

    if clean_key.startswith(f"{SIGNED_LICENSE_PREFIX}."):
        return _validate_signed_license(clean_key, clean_device, persist_state=True)

    return _validate_legacy_license(clean_key, clean_device, allow_bind=True, persist_state=True)


def is_local_license_active(device_id: str) -> bool:
    """
    Fast guard check used by API middleware.
    Relies on locally persisted activated license state.
    """
    clean_device = str(device_id or "").strip()
    if not clean_device:
        return False

    state = get_local_license_state()
    saved_key = str(state.get("key") or "").strip()
    if saved_key:
        if saved_key.startswith(f"{SIGNED_LICENSE_PREFIX}."):
            return bool(_validate_signed_license(saved_key, clean_device, persist_state=False).get("valid"))
        return bool(
            _validate_legacy_license(
                saved_key, clean_device, allow_bind=False, persist_state=False
            ).get("valid")
        )

    # Backward-compat fallback if state file is missing.
    for lic in _load_legacy_licenses():
        if not bool(lic.get("active", True)):
            continue
        if str(lic.get("device_id") or "").strip() != clean_device:
            continue
        expires_at = lic.get("expires_at")
        if expires_at:
            try:
                if datetime.now(timezone.utc) > _parse_iso_datetime(expires_at):
                    continue
            except Exception:
                continue
        return True
    return False


def deactivate_license(key: str) -> bool:
    """Deactivate a license in local admin store."""
    licenses = _load_legacy_licenses()
    for idx, lic in enumerate(licenses):
        if str(lic.get("key") or "").strip() == str(key or "").strip():
            lic["active"] = False
            licenses[idx] = lic
            _save_legacy_licenses(licenses)
            return True
    return False


def get_all_licenses() -> List[Dict[str, Any]]:
    """Get all known licenses from admin/local store."""
    return _load_legacy_licenses()

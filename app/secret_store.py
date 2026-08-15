from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


class SecretStoreError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _configure_crypt32():
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def dpapi_protect(value: str) -> str:
    if os.name != "nt":
        raise SecretStoreError("DPAPI is only available on Windows")
    input_blob, input_buffer = _blob(value.encode("utf-8"))
    entropy_blob, entropy_buffer = _blob(b"google-images-dashboard-v1")
    output_blob = DATA_BLOB()
    crypt32, kernel32 = _configure_crypt32()
    ok = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Google Images Dashboard",
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    )
    _ = (input_buffer, entropy_buffer)
    if not ok:
        raise SecretStoreError("Windows DPAPI encryption failed")
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(output_blob.pbData)


def dpapi_unprotect(value: str) -> str:
    if os.name != "nt":
        raise SecretStoreError("DPAPI is only available on Windows")
    encrypted = base64.b64decode(value.encode("ascii"), validate=True)
    input_blob, input_buffer = _blob(encrypted)
    entropy_blob, entropy_buffer = _blob(b"google-images-dashboard-v1")
    output_blob = DATA_BLOB()
    crypt32, kernel32 = _configure_crypt32()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    )
    _ = (input_buffer, entropy_buffer)
    if not ok:
        raise SecretStoreError("Windows DPAPI decryption failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(output_blob.pbData)


class DashboardSecretStore:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def _read(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    def public_settings(self) -> dict:
        value = self._read()
        return {
            "clash_secret_saved": bool(value.get("clash_secret_dpapi")),
            "clash_endpoint": str(value.get("clash_endpoint", "http://127.0.0.1:9097")),
            "clash_proxy_url": str(value.get("clash_proxy_url", "http://127.0.0.1:7897")),
            "storage_path": str(self.path),
            "protection": "Windows DPAPI（当前用户）",
        }

    def save_clash(self, secret: str, endpoint: str, proxy_url: str) -> None:
        payload = {
            "version": 1,
            "protection": "windows-dpapi-current-user",
            "clash_secret_dpapi": dpapi_protect(secret),
            "clash_endpoint": endpoint,
            "clash_proxy_url": proxy_url,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def clash_secret(self, provided: str = "") -> str:
        if provided.strip():
            return provided.strip()
        encrypted = str(self._read().get("clash_secret_dpapi", ""))
        if not encrypted:
            raise SecretStoreError("未输入或保存 Clash API 密钥")
        try:
            return dpapi_unprotect(encrypted)
        except (ValueError, UnicodeError):
            raise SecretStoreError("本地 Clash 密钥无法解密") from None

    def clear_clash(self) -> None:
        value = self._read()
        value.pop("clash_secret_dpapi", None)
        if value:
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        elif self.path.exists():
            self.path.unlink()

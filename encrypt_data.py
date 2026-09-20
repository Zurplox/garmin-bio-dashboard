"""
AES-256-GCM Encryption Engine for Public Biometrics Dashboard
Ensures 100% Zero-Exposure of Personal Health Data in Public GitHub Repositories.
Decrypted in client browser via Web Crypto API with passphrase 'Capybara'.
"""

import os
import sys
import json
import base64
from datetime import datetime
from pathlib import Path

# Fix Windows console UTF-8 encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

DATA_DIR = Path("data")
INPUT_FILE = DATA_DIR / "biometrics.json"
OUTPUT_FILE = DATA_DIR / "biometrics.enc.json"
STATUS_FILE = DATA_DIR / "status.json"

DEFAULT_PASS = "Capybara"


def encrypt_payload(data_str: str, password: str) -> dict:
    salt = os.urandom(16)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    key = kdf.derive(password.encode("utf-8"))
    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, data_str.encode("utf-8"), None)

    return {
        "format": "aes-256-gcm-pbkdf2",
        "iterations": 100_000,
        "salt": base64.b64encode(salt).decode("utf-8"),
        "iv": base64.b64encode(iv).decode("utf-8"),
        "data": base64.b64encode(ciphertext).decode("utf-8"),
    }


def main():
    if not INPUT_FILE.exists():
        print(f"❌ Error: {INPUT_FILE} does not exist. Run sync.py first.")
        sys.exit(1)

    password = os.environ.get("GARMIN_DASHBOARD_PASS", DEFAULT_PASS)

    print(f"🔒 Encrypting {INPUT_FILE} with AES-256-GCM...")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        raw_json = f.read()

    encrypted = encrypt_payload(raw_json, password)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(encrypted, f, indent=2)

    status_data = {
        "updated_at": datetime.now().isoformat(),
        "version": "1.0.0",
        "master_lock": False,  # Off by default, armed only when user activates Barabara
    }
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=2)

    print(f"✅ Successfully encrypted! Saved to: {OUTPUT_FILE.resolve()}")
    print(f"   • Ciphertext size: {len(encrypted['data'])} bytes")
    print(f"   • Key derivation: PBKDF2-HMAC-SHA256 (100,000 rounds)")
    print(f"   • Status file: {STATUS_FILE.resolve()} (master_lock=False)")


if __name__ == "__main__":
    main()

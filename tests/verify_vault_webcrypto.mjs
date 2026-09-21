/*
 * Decrypts the deployed vault through the exact browser code path (W3C Web
 * Crypto: PBKDF2-SHA256 -> AES-256-GCM) so the crypto contract between
 * encrypt_data.py and index.html is verified without a browser.
 *
 * Usage: node tests/verify_vault_webcrypto.mjs [envelope] [passphrase]
 */

import { readFile } from 'node:fs/promises';

const envelopePath = process.argv[2] || 'data/biometrics.enc.json';
const passphrase = process.argv[3] || 'Capybara';

const envelope = JSON.parse(await readFile(envelopePath, 'utf8'));
const decode = (value) => Uint8Array.from(Buffer.from(value, 'base64'));

if (envelope.format !== 'aes-256-gcm-pbkdf2') {
  console.error(`unexpected envelope format: ${envelope.format}`);
  process.exit(1);
}

const passwordKey = await crypto.subtle.importKey(
  'raw',
  new TextEncoder().encode(passphrase),
  'PBKDF2',
  false,
  ['deriveKey'],
);

const aesKey = await crypto.subtle.deriveKey(
  {
    name: 'PBKDF2',
    salt: decode(envelope.salt),
    iterations: envelope.iterations,
    hash: 'SHA-256',
  },
  passwordKey,
  { name: 'AES-GCM', length: 256 },
  false,
  ['decrypt'],
);

let plaintext;
try {
  plaintext = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: decode(envelope.iv) },
    aesKey,
    decode(envelope.data),
  );
} catch (error) {
  console.error(`FAIL decryption threw (${error.name}); envelope/passphrase mismatch?`);
  process.exit(1);
}

const payload = JSON.parse(new TextDecoder().decode(plaintext));
const required = [
  'updated_at', 'athlete', 'today', 'fitness', 'whoop', 'fitbit',
  'garmin_signature', 'baselines', 'history', 'clinical_intelligence',
];
const missing = required.filter((key) => !(key in payload));
if (missing.length > 0) {
  console.error(`FAIL decrypted payload is missing keys: ${missing.join(', ')}`);
  process.exit(1);
}

console.log(
  `OK decrypted ${plaintext.byteLength} bytes | iterations=${envelope.iterations}` +
  ` | updated_at=${payload.updated_at} | nights=${payload.history?.daily_sleep?.length ?? 0}`,
);

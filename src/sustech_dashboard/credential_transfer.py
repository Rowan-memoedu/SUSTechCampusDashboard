"""One-use device bootstrap, encrypted to the native client's ephemeral key."""
import base64
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def decode(value, size=None):
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError('电脑凭据交接无效')
    raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    if size is not None and len(raw) != size:
        raise ValueError('电脑凭据交接无效')
    return raw


def public_key(private):
    return encode(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def context(ticket, device):
    return json.dumps(['campus-device-bootstrap/v1', ticket, device], separators=(',', ':')).encode()


def key(shared, aad):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=aad).derive(shared)


def seal(public, credentials, ticket, device):
    private = X25519PrivateKey.generate()
    aad = context(ticket, device)
    shared = private.exchange(X25519PublicKey.from_public_bytes(decode(public, 32)))
    nonce = os.urandom(12)
    plaintext = json.dumps(credentials, ensure_ascii=False).encode()
    return {'public': public_key(private), 'nonce': encode(nonce),
            'ciphertext': encode(AESGCM(key(shared, aad)).encrypt(nonce, plaintext, aad))}


def unseal(private, envelope, ticket, device):
    aad = context(ticket, device)
    shared = private.exchange(X25519PublicKey.from_public_bytes(decode(envelope['public'], 32)))
    plaintext = AESGCM(key(shared, aad)).decrypt(decode(envelope['nonce'], 12), decode(envelope['ciphertext']), aad)
    credentials = json.loads(plaintext)
    if not isinstance(credentials, list) or len(credentials) != 2 or not all(isinstance(v, str) for v in credentials):
        raise ValueError('电脑凭据交接无效')
    return tuple(credentials)

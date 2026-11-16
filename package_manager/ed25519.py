"""Pure-Python Ed25519 (RFC 8032) implementation, zero third-party dependencies.

Personal-scale signing is low-frequency and small-payload; performance is more than enough.
"""

import hashlib

_q = 2**255 - 19
_l = 2**252 + 27742317777372353535851937790883648493


def _H(m: bytes) -> bytes:
    return hashlib.sha512(m).digest()


def _inv(x: int) -> int:
    return pow(x, _q - 2, _q)


_d = (-121665 * _inv(121666)) % _q
_I = pow(2, (_q - 1) // 4, _q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = pow(xx, (_q + 3) // 8, _q)
    if (x * x - xx) % _q != 0:
        x = (x * _I) % _q
    if x % 2 != 0:
        x = _q - x
    return x


_By = (4 * _inv(5)) % _q
_Bx = _xrecover(_By)
_B = (_Bx % _q, _By % _q)
_IDENTITY = (0, 1)


def _edwards(P, Q):
    x1, y1 = P
    x2, y2 = Q
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + _d * x1 * x2 * y1 * y2)
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - _d * x1 * x2 * y1 * y2)
    return (x3 % _q, y3 % _q)


def _scalarmult(P, e: int):
    Q = _IDENTITY
    P = (P[0] % _q, P[1] % _q)
    while e > 0:
        if e & 1:
            Q = _edwards(Q, P)
        P = _edwards(P, P)
        e >>= 1
    return Q


def _isoncurve(P) -> bool:
    x, y = P
    return (-x * x + y * y - 1 - _d * x * x * y * y) % _q == 0


def _encodepoint(P) -> bytes:
    x, y = P
    data = bytearray(32)
    for i in range(255):
        if (y >> i) & 1:
            data[i // 8] |= 1 << (i % 8)
    data[31] |= (x & 1) << 7
    return bytes(data)


def _decodepoint(s: bytes):
    y = int.from_bytes(s, "little")
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    if y >= _q:
        raise ValueError("point not on curve")
    x = _xrecover(y)
    if (x & 1) != sign:
        x = _q - x
    P = (x, y)
    if not _isoncurve(P):
        raise ValueError("point not on curve")
    return P


def _secret_expand(seed: bytes):
    h = _H(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    a, _ = _secret_expand(seed)
    return _encodepoint(_scalarmult(_B, a))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _secret_expand(seed)
    A = _encodepoint(_scalarmult(_B, a))
    r = int.from_bytes(_H(prefix + msg), "little") % _l
    R = _encodepoint(_scalarmult(_B, r))
    h = int.from_bytes(_H(R + A + msg), "little") % _l
    s = (r + h * a) % _l
    return R + s.to_bytes(32, "little")


def verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    if len(pk) != 32 or len(sig) != 64:
        return False
    try:
        A = _decodepoint(pk)
        R = _decodepoint(sig[:32])
    except Exception:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _l:
        return False
    h = int.from_bytes(_H(sig[:32] + pk + msg), "little") % _l
    return _scalarmult(_B, s) == _edwards(R, _scalarmult(A, h))

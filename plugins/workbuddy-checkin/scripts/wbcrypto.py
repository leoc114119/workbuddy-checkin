# -*- coding: utf-8 -*-
"""$wbEncrypted 信封解密（纯标准库实现，不依赖 pycryptodome / cryptography）。

WorkBuddy 桌面端把登录态里的敏感字段（accessToken / refreshToken / nickname /
phoneNumber）用 AES-256-GCM 信封加密后落盘。环境里没有可用的加密库，所以这里
自带一份纯 Python 的 AES-256 + GCM(GHASH) 实现。

## 密钥层级（关键，别再搞错）

App 用的是两层结构，**不是**「一个全局密钥直接加密」：

    ~/.workbuddy/keyblob            ← 每台机器一份（首次运行随机生成，mode 600）
      keyId: <本机数据密钥ID>
      slots[]: { type: "static-v1", protectorKeyId: <保护密钥ID>, wrapped: <信封> }
                                   ← wrapped 是用「保护密钥」加密的本机数据密钥

  * 「保护密钥」(protector) 是 App 内置的全局常量，所有安装一致；
  * 「数据密钥」每台机器不同，随机生成后存在 keyblob 里。

所以通用解法是：用保护密钥按 framing="file" 解开 keyblob.slots[].wrapped，
得到本机的数据密钥；再拿**与信封 keyId 相匹配**的那把钥匙去解字段。

⚠️ 本机实测：auth 文件的信封 keyId 恰好等于保护密钥的 ID，所以直接用保护密钥
就能解开 —— 但那是本机的巧合，**不能**据此写成通用实现。别人的机器上 auth 文件
可能用 keyblob 里的数据密钥，必须按 keyId 选钥匙。

AAD 里绑定 framing/keyId/suite，顺序错一个字节 tag 校验就失败。

安全约定：本模块只在内存里解密，绝不把明文写回磁盘、不打印完整令牌。
"""

import base64
import hashlib
import json
import os
import struct

# --------------------------------------------------------------------------
# 纯 Python AES-256
# --------------------------------------------------------------------------

_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76, 0xca,
    0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0, 0xb7, 0xfd,
    0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15, 0x04, 0xc7, 0x23,
    0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75, 0x09, 0x83, 0x2c, 0x1a,
    0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84, 0x53, 0xd1, 0x00, 0xed, 0x20,
    0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf, 0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d,
    0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8, 0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38,
    0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2, 0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17,
    0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73, 0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46,
    0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb, 0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3,
    0xac, 0x62, 0x91, 0x95, 0xe4, 0x79, 0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4,
    0xea, 0x65, 0x7a, 0xae, 0x08, 0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f,
    0x4b, 0xbd, 0x8b, 0x8a, 0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86,
    0xc1, 0x1d, 0x9e, 0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55,
    0x28, 0xdf, 0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _xtime(a):
    return ((a << 1) ^ 0x1b) & 0xff if a & 0x80 else (a << 1) & 0xff


def _mul(a, b):
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        hi = a & 0x80
        a = (a << 1) & 0xff
        if hi:
            a ^= 0x1b
        b >>= 1
    return r


def _expand_key(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


def _encrypt_block(w, nr, block):
    s = [list(block[i::4]) for i in range(4)]
    s = [[s[r][c] ^ w[c][r] for c in range(4)] for r in range(4)]
    for rnd in range(1, nr + 1):
        s = [[_SBOX[s[r][c]] for c in range(4)] for r in range(4)]
        s = [s[r][r:] + s[r][:r] for r in range(4)]
        if rnd != nr:
            ns = [[0] * 4 for _ in range(4)]
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                ns[0][c] = _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3]
                ns[1][c] = a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3]
                ns[2][c] = a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3)
                ns[3][c] = _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2)
            s = ns
        s = [[s[r][c] ^ w[rnd * 4 + c][r] for c in range(4)] for r in range(4)]
    out = bytearray(16)
    for c in range(4):
        for r in range(4):
            out[4 * c + r] = s[r][c]
    return bytes(out)


class AES:
    """AES-256 分组加密（仅需 encrypt，GCM 只用得到加密方向）。"""

    def __init__(self, key):
        self.w, self.nr = _expand_key(key)

    def encrypt(self, block):
        return _encrypt_block(self.w, self.nr, block)


def _ghash(h, data):
    """GF(2^128) 上的 GHASH。"""
    y = 0
    hh = int.from_bytes(h, "big")
    for i in range(0, len(data), 16):
        blk = data[i:i + 16].ljust(16, b"\x00")
        y ^= int.from_bytes(blk, "big")
        z, v = 0, y
        h2 = hh
        for _ in range(128):
            if v & (1 << 127):
                z ^= h2
            v = (v << 1) & ((1 << 128) - 1)
            if h2 & 1:
                h2 = (h2 >> 1) ^ 0xE1000000000000000000000000000000
            else:
                h2 >>= 1
        y = z
    return y.to_bytes(16, "big")


def _inc32(b):
    return b[:12] + ((int.from_bytes(b[12:], "big") + 1) % (1 << 32)).to_bytes(4, "big")


def aes_gcm_decrypt(key, nonce, ct, tag, aad=b""):
    """AES-GCM 解密并校验 tag。tag 不符时抛 ValueError。"""
    aes = AES(key)
    h = aes.encrypt(b"\x00" * 16)
    if len(nonce) == 12:
        j0 = nonce + b"\x00\x00\x00\x01"
    else:
        pad = b"\x00" * ((16 - len(nonce) % 16) % 16)
        j0 = _ghash(h, nonce + pad + (len(nonce) * 8).to_bytes(8, "big"))

    def pad16(b):
        return b + b"\x00" * ((16 - len(b) % 16) % 16)

    gd = pad16(aad) + pad16(ct) + (len(aad) * 8).to_bytes(8, "big") + (len(ct) * 8).to_bytes(8, "big")
    exp_tag = bytes(x ^ y for x, y in zip(_ghash(h, gd), aes.encrypt(j0)))
    if exp_tag != tag:
        raise ValueError("tag 校验失败（密钥不匹配或数据被篡改）")

    out = bytearray()
    ctr = _inc32(j0)
    for i in range(0, len(ct), 16):
        ks = aes.encrypt(ctr)
        out += bytes(x ^ y for x, y in zip(ct[i:i + 16], ks))
        ctr = _inc32(ctr)
    return bytes(out)


# --------------------------------------------------------------------------
# 信封与密钥层级
# --------------------------------------------------------------------------

# 保护密钥（App 内置全局常量）。仅用于解开 keyblob；不要假设它是数据密钥。
_PROTECTOR_SECRET = "Sik9U5aXhCdwTVEwsEySDOmDoB9r9ntFxHF1fst9LQI="
_PROTECTOR_KEY = hashlib.sha256(_PROTECTOR_SECRET.encode("utf-8")).digest()
_PROTECTOR_KEY_ID = hashlib.sha256(_PROTECTOR_KEY).hexdigest()[:16]

_FRAMING_NUM = {"file": 1, "field": 2, "record": 3, "stream": 4}
_FRAMING_TAG = {"file": b"WBEF1", "field": b"WBEV1", "record": b"WBER1", "stream": b"WBES1"}

KEYBLOB_PATH = os.path.expanduser(os.environ.get("WB_KEYBLOB") or "~/.workbuddy/keyblob")


def _wb_aad(framing, key_id, suite):
    """复刻 App 的 AAD 拼装顺序（顺序错则 tag 校验必失败）。"""
    return b"".join([
        b"WB-AAD\x00", b"\x01",
        struct.pack(">I", len(_FRAMING_TAG[framing])) + _FRAMING_TAG[framing],
        struct.pack(">I", 6) + b"sym-v1",
        struct.pack(">I", suite),
        struct.pack(">I", len(key_id)) + key_id.encode(),
        bytes([_FRAMING_NUM[framing]]),
        b"\x00", b"\x00",
    ])


# --- 非对称信封（asym-v1）--------------------------------------------------
# 结构照抄 App 自己的 sealAsymmetricEnvelope / openAsymmetricEnvelope：
#   载荷用一把随机「文件密钥」加密；文件密钥被包两次 ——
#   一次用**使用者密钥**（userWrap，我们能解），一次用开发者 RSA 公钥
#   （developerWrap，读取时用不到，只是托管/托管恢复用）。
# 所以只要手里有使用者密钥（keyblob 里的数据密钥），无需任何私钥就能解密。
_ASYM_FRAMING_TAG = {"file": b"WBEA1", "field": b"WBEV1"}   # 注意 file 是 WBEA1，不是 WBEF1
_WRAP_MAGIC = b"WB-WRAP\x00"


def _lenpref(s):
    b = s.encode("utf-8") if isinstance(s, str) else s
    return struct.pack(">I", len(b)) + b


def _opt_str(s):
    return b"\x00" if s is None else b"\x01" + _lenpref(s)


def _asym_context_aad(framing, key_id, suite, purpose, resource_id, field_path):
    """asym-v1 载荷的 AAD（对应 App 的 buildAuthenticatedContextAad）。"""
    return b"".join([
        b"WB-AAD\x00", b"\x01",
        _lenpref(_ASYM_FRAMING_TAG[framing]),
        _lenpref("asym-v1"),
        struct.pack(">I", suite),
        _lenpref(key_id),
        bytes([_FRAMING_NUM[framing]]),
        _lenpref(purpose),
        _lenpref(resource_id),
        _opt_str(field_path),
        b"\x00", b"\x00",
    ])


def _file_key_wrap_aad(framing, file_key_id, dev_key_id, suite,
                       purpose, resource_id, field_path):
    """包裹文件密钥时的 AAD（对应 App 的 buildFileKeyWrapContext）。"""
    return b"".join([
        _WRAP_MAGIC, b"\x01",
        _lenpref(_ASYM_FRAMING_TAG[framing]),
        _lenpref("asym-v1"),
        struct.pack(">I", suite),
        _lenpref(file_key_id),
        _lenpref(dev_key_id),
        bytes([_FRAMING_NUM[framing]]),
        _lenpref(purpose),
        _lenpref(resource_id),
        _opt_str(field_path),
    ])


def _open_asym(env, framing, keys):
    """解开 asym-v1 信封：先用使用者密钥拆出文件密钥，再拿它解载荷。"""
    if env.get("suite") != 1:
        raise ValueError("不支持的 asym 套件：%s" % env.get("suite"))
    wrap = env.get("userWrap")
    dev = env.get("developerWrap")
    if not isinstance(wrap, dict) or not isinstance(dev, dict):
        raise ValueError("asym 信封缺少 userWrap / developerWrap")
    purpose, resource_id = env.get("purpose"), env.get("resourceId")
    field_path = env.get("fieldPath")
    if not isinstance(purpose, str) or not isinstance(resource_id, str):
        raise ValueError("asym 信封缺少 purpose / resourceId")
    if framing == "field" and not field_path:
        raise ValueError("asym 字段信封缺少 fieldPath")

    user_key = keys.get(wrap.get("keyId"))
    if user_key is None:
        raise ValueError(
            "没有与 userWrap.keyId=%s 匹配的密钥（已有的=%s）"
            % (wrap.get("keyId"), sorted(k for k in keys if k))
        )

    file_key = aes_gcm_decrypt(
        user_key,
        base64.b64decode(wrap["nonce"]),
        base64.b64decode(wrap["ciphertext"]),
        base64.b64decode(wrap["authTag"]),
        _file_key_wrap_aad(framing, env["keyId"], dev["publicKeyId"],
                           env["suite"], purpose, resource_id, field_path),
    )
    if len(file_key) != 32 or hashlib.sha256(file_key).hexdigest()[:16] != env["keyId"]:
        raise ValueError("拆出的文件密钥与信封 keyId 不符")

    return aes_gcm_decrypt(
        file_key,
        base64.b64decode(env["nonce"]),
        base64.b64decode(env["ciphertext"]),
        base64.b64decode(env["authTag"]),
        _asym_context_aad(framing, env["keyId"], env["suite"],
                          purpose, resource_id, field_path),
    )


def _parse_envelope(raw):
    """把 base64 信封解成 dict。"""
    env = json.loads(base64.b64decode(raw))
    if not isinstance(env, dict):
        raise ValueError("信封不是对象")
    return env


def _open_envelope(env, framing, keys):
    """用 keys 里 keyId 匹配的那把钥匙解信封。"""
    key_id = env.get("keyId")
    key = keys.get(key_id)
    if key is None:
        raise ValueError(
            "没有匹配的密钥：信封 keyId=%s，已有的=%s"
            % (key_id, sorted(k for k in keys if k))
        )
    return aes_gcm_decrypt(
        key,
        base64.b64decode(env["nonce"]),
        base64.b64decode(env["ciphertext"]),
        base64.b64decode(env["authTag"]),
        _wb_aad(framing, key_id, env["suite"]),
    )


def unwrap_keyblob(path=None):
    """解开 keyblob，返回 {keyId: 本机数据密钥}。失败返回 {}。

    这是让插件在**别人机器上**也能用的关键一步：keyblob 用全局保护密钥包着
    本机随机生成的 32 字节数据密钥。
    """
    path = path or KEYBLOB_PATH
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            kb = json.load(fh)
    except Exception:
        return out
    target = kb.get("keyId")
    for slot in kb.get("slots") or []:
        if not isinstance(slot, dict) or slot.get("type") != "static-v1":
            continue
        wrapped = slot.get("wrapped")
        if not isinstance(wrapped, str):
            continue
        try:
            env = _parse_envelope(wrapped)
            # keyblob 用 framing="file" 包裹（实测确认；field/record/stream 均 tag 失败）
            pt = _open_envelope(env, "file", {_PROTECTOR_KEY_ID: _PROTECTOR_KEY})
        except Exception:
            continue
        if len(pt) == 32 and hashlib.sha256(pt).hexdigest()[:16] == target:
            out[target] = pt
    return out


def candidate_keys():
    """所有可用的解密钥匙：{keyId: key}。

    含保护密钥本身与 keyblob 里的本机数据密钥 —— 信封里带 keyId，按 keyId 选即可，
    这样无论对方机器把字段加密在哪一层都能解。
    """
    keys = {_PROTECTOR_KEY_ID: _PROTECTOR_KEY}
    keys.update(unwrap_keyblob())
    return keys


def open_wb_field(value, keys=None):
    """解开一个 $wbEncrypted 字段；非信封字段原样返回。

    同时支持两种信封：
      * 对称（默认，`envelope` 里就是载荷）—— 用与 keyId 匹配的密钥直解；
      * 非对称（`scheme: "asym-v1"`）—— 先用使用者密钥拆出文件密钥再解载荷。
    """
    if not isinstance(value, dict) or value.get("$wbEncrypted") != 1:
        return value
    keys = keys if keys is not None else candidate_keys()
    env = _parse_envelope(value["envelope"])
    if value.get("scheme") == "asym-v1" or env.get("scheme") == "asym-v1":
        return _open_asym(env, "field", keys).decode("utf-8")
    return _open_envelope(env, "field", keys).decode("utf-8")


def mask(secret, keep=6):
    """脱敏显示：只留前缀，绝不回显完整令牌。"""
    if not secret:
        return "(空)"
    s = str(secret)
    if len(s) <= keep:
        return s[0] + "*" * (len(s) - 1)
    return s[:keep] + "…(%d 字符，已脱敏)" % len(s)

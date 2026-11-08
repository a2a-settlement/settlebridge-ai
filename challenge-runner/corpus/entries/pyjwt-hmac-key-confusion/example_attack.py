"""Stand-in challenger: HMAC verification accepts public-key material."""

from __future__ import annotations

_PUBLIC_KEY = """\
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA1tUH3/0v8fvLensHO1g2
6+U4r7jBg43DVOgqmXAWQa8ArAb4NfTrsYX8YkVhZZYwuLmKczRj0GhXUVY9iDbT
sIGmgG+ySj6eiREz5VLqofFkAvRZ6y7yNv8PIGgXEhQTiDDNIkHGaFNMvn/eZ54H
is70pdTjR5Ko+/y/wg71df1nb/5KwttSvy0YsTu/XpkduonPruYfAVRG3HK+3GZd
xTygLcdamwe9jj+kjxtXRlrXVMQiXGFSU8U6bjafWnQiQ9XzjxvygBt0ZD0kRorr
p74XGyQY5ThkN8DlpJbTTFsxOnBUAQz4zhohjobIGBRimi5yVlyLOwTlpaKGFC7O
7wIDAQAB
-----END PUBLIC KEY-----
"""


def test_hmac_accepts_public_key_material(sbc_world):
    key = _PUBLIC_KEY.replace("-----END", "\t-----END")
    token = sbc_world.encode({"sub": "user"}, key=key)
    outcome = sbc_world.decode(token, key=key)
    assert outcome.accepted, outcome.error
    assert outcome.unauthorized
    assert outcome.value["sub"] == "user"

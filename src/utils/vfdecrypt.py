import hashlib
import hmac
import struct
from pathlib import Path

from Crypto.Cipher import AES

MD_LENGTH = 20
CIPHER_KEY_LENGTH = 16
CIPHER_BLOCKSIZE = 16
CHUNK_SIZE = 1024**5
V1_HEADER_SIZE = 1276
V2_HEADER_SIZE = 0x318 + 0x260
KEY_LENGTH = CIPHER_KEY_LENGTH + MD_LENGTH


class V1Header:
    def __init__(self, data: bytes):
        if len(data) < 0x1B8:
            raise ValueError(f"Data too short for V1Header: {len(data)} bytes")

        (self.kdf_iteration_count,) = struct.unpack(">I", data[0x34:0x38])
        (self.kdf_salt_len,) = struct.unpack(">I", data[0x38:0x3C])
        self.kdf_salt = data[0x3C : 0x3C + self.kdf_salt_len]
        self.unwrap_iv = data[0x68 : 0x68 + 32]
        (la,) = struct.unpack(">I", data[0x88:0x8C])
        self.len_wrapped_aes_key = la
        self.wrapped_aes_key = data[0x8C : 0x8C + la]
        (lh,) = struct.unpack(">I", data[0x1B4:0x1B8])
        self.len_hmac_sha1_key = lh
        self.wrapped_hmac_sha1_key = data[0x1B8 : 0x1B8 + lh]


class V2Header:
    def __init__(self, data: bytes):
        if len(data) < V2_HEADER_SIZE:
            raise ValueError(f"Data too short for V2Header: {len(data)} bytes")

        off = 0
        self.sig = data[off : off + 8]
        off += 8
        _, self.enc_iv_size, *_ = struct.unpack(">7I", data[off : off + 28])
        off += 28
        off += 16  # skip UUID
        (self.blocksize,) = struct.unpack(">I", data[off : off + 4])
        off += 4
        (self.datasize,) = struct.unpack(">Q", data[off : off + 8])
        off += 8
        (self.dataoffset,) = struct.unpack(">Q", data[off : off + 8])
        off += 8
        off += 0x260
        _, _, ki, ks = struct.unpack(">4I", data[off : off + 16])
        off += 16
        self.kdf_iteration_count, self.kdf_salt_len = ki, ks
        self.kdf_salt = data[off : off + ks]
        off += ks
        (be,) = struct.unpack(">I", data[off : off + 4])
        off += 4
        self.blob_enc_iv = data[off : off + be]
        off += be
        (
            self.blob_enc_key_bits,
            self.blob_enc_algorithm,
            self.blob_enc_padding,
            self.blob_enc_mode,
        ) = struct.unpack(">4I", data[off : off + 16])
        off += 16
        (eb,) = struct.unpack(">I", data[off : off + 4])
        off += 4
        self.encrypted_keyblob = data[off : off + eb]


def _determine_version(path: Path) -> int:
    with path.open("rb") as f:
        sig = f.read(8)
        if sig == b"encrcdsa":
            return 2
        f.seek(-8, 2)
        if f.read(8) == b"cdsaencr":
            return 1
    raise ValueError("Unknown header format")


def _precompute_chunk_ivs(hmac_key: bytes, max_chunks: int) -> list[bytes]:
    return [
        hmac.new(hmac_key, i.to_bytes(4, "big"), hashlib.sha1).digest()[
            :CIPHER_BLOCKSIZE
        ]
        for i in range(max_chunks)
    ]


def _decrypt_image(
    path_in: Path,
    path_out: Path,
    aes_key: bytes,
    hmac_key: bytes,
    chunk_size: int,
    data_offset: int,
    data_size: int,
):
    max_chunks = (data_size + chunk_size - 1) // chunk_size
    ivs = _precompute_chunk_ivs(hmac_key, max_chunks)

    buf = bytearray(chunk_size + CIPHER_BLOCKSIZE)
    mv = memoryview(buf)

    path_out.parent.mkdir(parents=True, exist_ok=True)
    with path_in.open("rb") as fin, path_out.open("wb") as fout:
        fin.seek(data_offset)
        remaining = data_size

        for i in range(max_chunks):
            size = min(chunk_size, remaining)
            n = fin.readinto(mv[:size])

            pad = (-n) % CIPHER_BLOCKSIZE
            if pad:
                mv[n : n + pad] = b"\x00" * pad
                n += pad

            cipher = AES.new(aes_key, AES.MODE_CBC, ivs[i])
            plain = cipher.decrypt(mv[:n])
            fout.write(plain[:size])

            remaining -= size


def decrypt_vf(path_in: Path, path_out: Path, key: str):
    ver = _determine_version(path_in)

    with path_in.open("rb") as f:
        if ver == 1:
            f.seek(0, 2)
            file_size = f.tell()
            f.seek(file_size - V1_HEADER_SIZE)
            header_data = f.read(V1_HEADER_SIZE)
            if len(header_data) < V1_HEADER_SIZE:
                raise ValueError("File too small for V1 header")
            hdr = V1Header(header_data)
            data_offset = 0
            data_size = file_size - V1_HEADER_SIZE
            chunk_size = CHUNK_SIZE
        else:
            header_data = f.read(V2_HEADER_SIZE)
            if len(header_data) < V2_HEADER_SIZE:
                raise ValueError("File too small for V2 header")
            hdr = V2Header(header_data)
            data_offset = hdr.dataoffset
            data_size = hdr.datasize
            chunk_size = hdr.blocksize or CHUNK_SIZE

    if chunk_size == 0:
        raise ValueError("Invalid chunk size (zero)")

    kb = bytes.fromhex(key)
    if len(kb) != KEY_LENGTH:
        raise ValueError(f"key must be {KEY_LENGTH} bytes (hex {2 * KEY_LENGTH} chars)")
    aes_key = kb[:CIPHER_KEY_LENGTH]
    hmac_key = kb[CIPHER_KEY_LENGTH:]

    _decrypt_image(
        path_in, path_out, aes_key, hmac_key, chunk_size, data_offset, data_size
    )

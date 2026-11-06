# Helper functions for TNA Flasher extension

import os
import secrets
from pathlib import Path


def load_or_create_secret(path: Path) -> str:
    """Return the secret stored at path, creating a random one on first use.

    The file is written to a private temp file and linked into place, so concurrent
    workers can't read a partial secret and all end up using the same value.
    Raises OSError or ValueError rather than continuing without a usable secret.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32))
            try:
                os.link(temp, path)
            except FileExistsError:
                pass  # another worker created it first; use theirs
        finally:
            temp.unlink(missing_ok=True)

    secret = path.read_text().strip()
    if len(secret) < 32:
        raise ValueError(f"Secret file {path} is empty or too short")
    return secret


UF2_BLOCK_SIZE = 512
UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_NOT_MAIN_FLASH = 0x00000001
UF2_FLAG_FAMILY_ID_PRESENT = 0x00002000
UF2_FAMILY_RP2350_ARM_S = 0xE48BFF59
RP2350_FLASH_BASE = 0x10000000
RP2350_FLASH_END = 0x11000000


def validate_rp2350_uf2(content: bytes) -> None:
    """Raise ValueError unless content is a complete RP2350 ARM-S UF2 image.

    Applies the same rules as parseUf2() in static/js/rp2350-flasher.js, so a file
    accepted here is not rejected by the browser at flash time. Blocks of other
    families (such as the RP2350-E10 absolute block) are allowed and left unchecked.
    """
    if not content or len(content) % UF2_BLOCK_SIZE:
        raise ValueError("Not a valid UF2: size is not a multiple of 512 bytes")

    addresses = set()
    block_numbers = set()
    expected_blocks = None
    for index in range(len(content) // UF2_BLOCK_SIZE):
        block = content[index * UF2_BLOCK_SIZE : (index + 1) * UF2_BLOCK_SIZE]
        words = [int.from_bytes(block[i : i + 4], "little") for i in range(0, 32, 4)]
        magic0, magic1, flags, addr, size, block_no, num_blocks, family = words
        magic_end = int.from_bytes(block[508:512], "little")
        if (
            magic0 != UF2_MAGIC_START0
            or magic1 != UF2_MAGIC_START1
            or magic_end != UF2_MAGIC_END
        ):
            raise ValueError(f"Not a valid UF2: bad magic in block {index}")

        is_image = (
            flags & UF2_FLAG_FAMILY_ID_PRESENT
            and not flags & UF2_FLAG_NOT_MAIN_FLASH
            and family == UF2_FAMILY_RP2350_ARM_S
        )
        if not is_image:
            continue

        if size != 256:
            raise ValueError(f"Unsupported UF2: block {index} payload is not 256 bytes")
        if addr % 256 or addr < RP2350_FLASH_BASE or addr + 256 > RP2350_FLASH_END:
            raise ValueError(f"Unsupported UF2: bad flash address {addr:#010x}")
        if addr in addresses:
            raise ValueError(f"Invalid UF2: address {addr:#010x} appears twice")
        if expected_blocks is None:
            expected_blocks = num_blocks
        if (
            num_blocks != expected_blocks
            or block_no >= num_blocks
            or block_no in block_numbers
        ):
            raise ValueError(f"Invalid UF2: inconsistent block numbering at block {index}")
        addresses.add(addr)
        block_numbers.add(block_no)

    if not addresses:
        raise ValueError("This UF2 has no RP2350 ARM firmware in it")
    if len(addresses) != expected_blocks:
        raise ValueError(
            f"Incomplete UF2: found {len(addresses)} of {expected_blocks} blocks"
        )
    if RP2350_FLASH_BASE not in addresses:
        raise ValueError(
            f"Unsupported UF2: image does not start at {RP2350_FLASH_BASE:#010x}"
        )

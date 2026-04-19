import pytest


def test_extension_loads():
    """Test that the extension can be imported"""
    from tnaflasher import tnaflasher_ext, db
    assert tnaflasher_ext is not None
    assert db is not None


def test_services_available():
    """Test that core services can be imported"""
    from tnaflasher.services import (
        get_available_devices,
        get_firmware_path,
        generate_flash_token,
        verify_flash_token,
        generate_flash_code,
        create_flash_code_for_payment,
        verify_flash_code,
    )


def test_flash_code_generation():
    """Test flash code format is TNA-XXXX-XXXX with valid chars"""
    from tnaflasher.services import generate_flash_code
    code = generate_flash_code()
    assert code.startswith("TNA-")
    assert len(code) == 13  # TNA-XXXX-XXXX
    parts = code.split("-")
    assert len(parts) == 3
    assert len(parts[1]) == 4
    assert len(parts[2]) == 4
    # No ambiguous characters (0, O, 1, I, L)
    for char in code.replace("TNA-", "").replace("-", ""):
        assert char not in "01OIL"


def test_flash_code_uniqueness():
    """Test that generated codes are unique"""
    from tnaflasher.services import generate_flash_code
    codes = {generate_flash_code() for _ in range(100)}
    assert len(codes) == 100  # All unique


def test_models_import():
    """Test that new models can be imported"""
    from tnaflasher.models import (
        FlashCode,
        FlashCodeResponse,
        VerifyCodeResponse,
        Miner,
    )
    # Verify Miner has flash_method with default
    miner = Miner(id="test", name="test")
    assert miner.flash_method == "webserial"

    # Verify FlashCode model
    fc = FlashCode(id="t", code="TNA-1234-5678", payment_hash="abc", device="d", version="v")
    assert fc.status == "unused"


def test_crud_imports():
    """Test that new CRUD functions can be imported"""
    from tnaflasher.crud import (
        create_flash_code,
        get_flash_code_by_code,
        get_flash_code_by_payment_hash,
        mark_flash_code_used,
        expire_flash_codes,
        check_rate_limit,
        record_rate_limit_attempt,
        cleanup_rate_limits,
    )

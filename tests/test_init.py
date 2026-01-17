import pytest


def test_extension_loads():
    """Test that the extension can be imported"""
    from tnaflasher import tnaflasher_ext, db
    assert tnaflasher_ext is not None
    assert db is not None


def test_services_available():
    """Test that services can be imported"""
    from tnaflasher.services import (
        SUPPORTED_DEVICES,
        get_available_devices,
        get_firmware_path,
        generate_flash_token,
        verify_flash_token,
    )
    assert len(SUPPORTED_DEVICES) == 5
    assert "NerdQAxePlus" in SUPPORTED_DEVICES

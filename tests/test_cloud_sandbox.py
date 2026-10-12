import pytest
from backend.services.cloud_sandbox import CloudMicroVMSandbox


def test_cloud_sandbox_unconfigured():
    # When no E2B_API_KEY is configured, it raises ValueError cleanly
    sandbox = CloudMicroVMSandbox(api_key=None)
    assert sandbox.is_configured() is False
    with pytest.raises(ValueError, match="E2B_API_KEY not configured"):
        sandbox.start()


def test_cloud_sandbox_init_with_key():
    sandbox = CloudMicroVMSandbox(api_key="e2b_test_key_123")
    assert sandbox.is_configured() is True

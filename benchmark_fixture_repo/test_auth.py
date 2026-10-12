from auth import verify_token
def test_verify():
    assert verify_token('secret')

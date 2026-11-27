"""Unit tests for self-contained Wyze provider driver."""

from pathlib import Path
from types import SimpleNamespace

from auth_test_helpers import (
    WYZE_ISSUED_ACCESS,
    WYZE_ISSUED_REFRESH,
    FakeAuthTransport,
    FakeWyzeIssuer,
)

from lookout.config.schema import LookoutConfig, WyzeConfig
from lookout.providers.wyze.authenticator import WyzeSdkIssuer
from lookout.providers.wyze.driver import WyzeProviderDriver


class _HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = SimpleNamespace(status_code=status)


def _issuer_with(responses: list[object]) -> tuple[WyzeSdkIssuer, FakeAuthTransport]:
    transport = FakeAuthTransport(responses)
    return WyzeSdkIssuer(transport_factory=lambda: transport), transport


def test_wyze_driver_auth_and_2fa_verification() -> None:
    """Real Wyze tokens come from the injected SDK issuer."""
    issuer = FakeWyzeIssuer()
    driver = WyzeProviderDriver(token_issuer=issuer)
    auth_res = driver.authenticate({"email": "user@wyze.com", "password": "secure-password"})
    assert auth_res.status == "2fa_required"
    assert auth_res.session_id is not None
    assert issuer.begin_calls == 1
    assert issuer.last_email == "user@wyze.com"

    verify_res = driver.verify_2fa(auth_res.session_id, "123456")
    assert verify_res.status == "authenticated"
    assert verify_res.auth_token == WYZE_ISSUED_ACCESS
    assert verify_res.refresh_token == WYZE_ISSUED_REFRESH
    assert "mock" not in str(verify_res.auth_token)
    assert verify_res.email == "user@wyze.com"


def test_wyze_driver_environment_sync(tmp_path: Path) -> None:
    """Wyze sidecar environment file remains owner-only and secret-free."""
    driver = WyzeProviderDriver()
    config = LookoutConfig(
        wyze=WyzeConfig(
            email="user@wyze.com",
            password="test-password",
            auth_token="token-123",
        )
    )
    synced = driver.sync_environment(config, tmp_path)
    assert len(synced) == 1
    env_path = synced[0]
    text = env_path.read_text()
    assert env_path.is_file() and "WYZE_EMAIL=user@wyze.com" in text
    assert "WYZE_ACCESS_TOKEN=token-123" in text
    assert "WYZE_API_KEY" not in text
    assert "KEY_ID" not in text
    assert "TOTP_KEY" not in text
    assert (env_path.stat().st_mode & 0o777) == 0o600


def test_email_mfa_sends_email_code_and_verifies_typed_code() -> None:
    """TR-7 (AC1): email 2FA sends a code and verifies the typed code."""
    issuer, transport = _issuer_with(
        [
            {
                "access_token": None,
                "mfa_options": ["Email"],
                "mfa_details": {"primary_option": "Email", "email": "u***@example.com"},
                "email_session_id": "email-session",
                "user_id": "user-1",
            },
            {"session_id": "email-vid"},
            {"access_token": "EMAIL_A", "refresh_token": "EMAIL_R", "user_id": "user-1"},
        ]
    )
    start = issuer.begin_login("u@example.com", "pw")
    assert start.challenge is not None and start.challenge.method == "email"
    assert transport.calls[1]["endpoint"] == "/v2/user/login/sendEmailCode"
    tokens = issuer.complete(str(start.issuer_session_id), "654321")
    assert tokens["access_token"] == "EMAIL_A"
    assert transport.calls[-1]["endpoint"] == "/api/user/login"
    assert transport.calls[-1]["json"]["verification_code"] == "654321"


def test_sms_mfa_sends_one_sms_and_never_calls_input(monkeypatch) -> None:
    """TR-9 (AC2): SMS sends exactly one message and never calls input()."""
    input_called = False

    def _fail_input(*args, **kwargs):
        nonlocal input_called
        input_called = True
        raise AssertionError("input() must never be called")

    monkeypatch.setattr("builtins.input", _fail_input)
    issuer, transport = _issuer_with(
        [
            {
                "access_token": None,
                "mfa_options": ["PrimaryPhone"],
                "mfa_details": {"primary_option": "PrimaryPhone"},
                "sms_session_id": "sms-session",
                "user_id": "user-1",
            },
            {"session_id": "sms-vid"},
            {"access_token": "SMS_A", "refresh_token": "SMS_R", "user_id": "user-1"},
        ]
    )
    start = issuer.begin_login("u@example.com", "pw")
    assert start.challenge is not None and start.challenge.method == "sms"
    issuer.complete(str(start.issuer_session_id), "111222")
    sms_calls = [call for call in transport.calls if call["endpoint"].endswith("sendSmsCode")]
    assert len(sms_calls) == 1
    assert transport.calls[-1]["endpoint"] == "/api/user/login"
    assert transport.calls[-1]["json"]["verification_code"] == "111222"
    assert input_called is False


def test_totp_code_is_sent_verbatim_never_expanded() -> None:
    """TR-10 (AC3): an authenticator code is sent verbatim, never as a secret."""
    issuer, transport = _issuer_with(
        [
            {
                "access_token": None,
                "mfa_options": ["TotpVerificationCode"],
                "mfa_details": {
                    "primary_option": "TotpVerificationCode",
                    "totp_apps": [{"app_id": "app-1"}],
                },
            },
            {"access_token": "TOTP_A", "refresh_token": "TOTP_R", "user_id": "user-1"},
        ]
    )
    start = issuer.begin_login("u@example.com", "pw")
    assert start.challenge is not None and start.challenge.method == "totp"
    issuer.complete(str(start.issuer_session_id), "654321")
    assert transport.calls[-1]["endpoint"] == "/api/user/login"
    assert transport.calls[-1]["json"]["verification_code"] == "654321"
    assert "verification_id" in transport.calls[-1]["json"]


def test_auth_payload_never_contains_totp_key() -> None:
    """TR-13 (AC4): no request payload ever carries a TOTP shared secret."""
    issuer, transport = _issuer_with(
        [
            {
                "access_token": None,
                "mfa_options": ["TotpVerificationCode"],
                "mfa_details": {
                    "primary_option": "TotpVerificationCode",
                    "totp_apps": [{"app_id": "app-1"}],
                },
            },
            {"access_token": "TOTP_A", "refresh_token": "TOTP_R", "user_id": "user-1"},
        ]
    )
    start = issuer.begin_login("u@example.com", "pw")
    issuer.complete(str(start.issuer_session_id), "654321")
    assert all("totp_key" not in call["json"] for call in transport.calls)


def test_no_2fa_login_returns_tokens_without_challenge() -> None:
    """TR-14 (AC5): a no-2FA response returns tokens and no challenge."""
    issuer, _transport = _issuer_with(
        [{"access_token": "NO2FA_A", "refresh_token": "NO2FA_R", "user_id": "user-1"}]
    )
    start = issuer.begin_login("u@example.com", "pw")
    assert start.challenge is None
    assert start.tokens["access_token"] == "NO2FA_A"


def test_login_succeeds_without_key_id_or_api_key() -> None:
    """TR-18 (AC7): login succeeds with no Developer API key headers."""
    issuer, transport = _issuer_with(
        [
            {
                "access_token": None,
                "mfa_options": ["Email"],
                "mfa_details": {"primary_option": "Email", "email": "u***@example.com"},
                "email_session_id": "email-session",
                "user_id": "user-1",
            },
            {"session_id": "email-vid"},
        ]
    )
    issuer.begin_login("u@example.com", "pw")
    assert "keyid" not in transport.calls[0]["headers"]
    assert "apikey" not in transport.calls[0]["headers"]


def test_key_headers_sent_only_when_supplied() -> None:
    """TR-19 (AC7): supplied keys are forwarded only as keyid/apikey headers."""
    issuer, transport = _issuer_with(
        [
            {
                "access_token": "NO2FA_A",
                "refresh_token": "NO2FA_R",
                "user_id": "user-1",
            }
        ]
    )
    issuer.begin_login("u@example.com", "pw", key_id="kid", api_key="key")
    assert transport.calls[0]["headers"]["keyid"] == "kid"
    assert transport.calls[0]["headers"]["apikey"] == "key"
    assert "keyid" not in transport.calls[0]["json"]
    assert "apikey" not in transport.calls[0]["json"]


def test_primary_option_email_wins_over_sms_when_both_present() -> None:
    """TR-22 (AC1): the primary option wins and the email send body is empty."""
    issuer, transport = _issuer_with(
        [
            {
                "access_token": None,
                "mfa_options": ["PrimaryPhone", "Email"],
                "mfa_details": {"primary_option": "Email", "email": "u***@example.com"},
                "email_session_id": "email-session",
                "sms_session_id": "sms-session",
                "user_id": "user-1",
            },
            {"session_id": "email-vid"},
        ]
    )
    start = issuer.begin_login("u@example.com", "pw")
    assert start.challenge is not None and start.challenge.method == "email"
    assert transport.calls[1]["endpoint"] == "/v2/user/login/sendEmailCode"
    assert transport.calls[1]["json"] == {}


def test_email_send_falls_back_to_mfa_sending_email_on_404() -> None:
    """TR-23 (AC1): a 404 on the primary email send retries the fallback once."""
    issuer, transport = _issuer_with(
        [
            {
                "access_token": None,
                "mfa_options": ["Email"],
                "mfa_details": {"primary_option": "Email", "email": "u***@example.com"},
                "email_session_id": "email-session",
                "user_id": "user-1",
            },
            _HttpError(404),
            {"session_id": "fallback-vid"},
        ]
    )
    start = issuer.begin_login("u@example.com", "pw")
    assert start.challenge is not None and start.challenge.method == "email"
    assert transport.calls[1]["endpoint"] == "/v2/user/login/sendEmailCode"
    assert transport.calls[2]["endpoint"] == "/api/user/login/email/mfa_sending_email"
    assert transport.calls[2]["json"] == {"email": "u@example.com", "user_id": "user-1"}

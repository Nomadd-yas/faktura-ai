"""api.base.auth: issue_tokens, decode_access, LoginLimiter, RateLimiter (BACKEND §2.1). Чистые функции, без HTTP."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from faktura.api.base.auth import Claims, LoginLimiter, RateLimiter, TokenPair, decode_access, issue_tokens
from faktura.config import get_settings
from faktura.domain.entities import User
from faktura.domain.errors import ERROR_CATALOG, AppError

UTC = timezone.utc
T0 = datetime(2026, 11, 20, 9, 0, 0, tzinfo=UTC)
SECRET = "unit-test-secret-unit-test-secret-1234"


@pytest.fixture(autouse=True)
def jwt_env(monkeypatch):
    monkeypatch.setenv("FAKTURA_JWT_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def user() -> User:
    return User(id="usr_01", workspace_id="ws_1", email="team@faktura.ai", name="Исследователь", is_admin=False)


@pytest.fixture
def admin() -> User:
    return User(id="usr_02", workspace_id="ws_1", email="root@faktura.ai", is_admin=True)


# --- issue_tokens ------------------------------------------------------------------------------

class TestIssueTokens:
    def test_returns_pair_with_ttl_900(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        assert isinstance(pair, TokenPair)
        assert pair.expires_in == 900
        assert pair.remember is True
        assert pair.access_token and pair.refresh_token

    def test_remember_false_is_kept_and_access_ttl_same(self, user):
        pair = issue_tokens(user, remember=False, now=T0)
        assert pair.remember is False
        assert pair.expires_in == 900  # TTL access не зависит от remember

    def test_access_is_jwt_hs256(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        header = jwt.get_unverified_header(pair.access_token)
        assert header["alg"] == "HS256"

    def test_access_decodes_back_to_user(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        claims = decode_access(pair.access_token, now=T0 + timedelta(seconds=1))
        assert isinstance(claims, Claims)
        assert claims.sub == "usr_01"
        assert claims.workspace_id == "ws_1"
        assert claims.is_admin is False

    def test_admin_flag_in_claims(self, admin):
        pair = issue_tokens(admin, remember=True, now=T0)
        assert decode_access(pair.access_token, now=T0).is_admin is True

    def test_claims_exp_is_iat_plus_900(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        claims = decode_access(pair.access_token, now=T0)
        assert claims.iat == int(T0.timestamp())
        assert claims.exp - claims.iat == 900

    def test_refresh_is_opaque_not_jwt(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        assert pair.refresh_token.count(".") != 2  # не JWT
        assert len(pair.refresh_token) >= 32
        assert pair.refresh_token != pair.access_token
        assert pair.refresh_token.isprintable() and " " not in pair.refresh_token

    def test_refresh_tokens_are_unique(self, user):
        tokens = {issue_tokens(user, remember=True, now=T0).refresh_token for _ in range(50)}
        assert len(tokens) == 50

    def test_access_does_not_contain_password_hash_or_email_secret(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        payload = pair.access_token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        assert "password" not in decoded
        assert set(json.loads(decoded)) >= {"sub", "workspace_id", "is_admin", "exp", "iat"}


# --- decode_access -----------------------------------------------------------------------------

class TestDecodeAccess:
    def test_valid_at_issue_time(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        assert decode_access(pair.access_token, now=T0).sub == "usr_01"

    def test_valid_just_before_expiry(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        assert decode_access(pair.access_token, now=T0 + timedelta(seconds=899)).sub == "usr_01"

    def test_expired_after_901s_is_token_expired(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        with pytest.raises(AppError) as e:
            decode_access(pair.access_token, now=T0 + timedelta(seconds=901))
        assert e.value.code == "token_expired"
        assert e.value.http_status == 401

    def test_expired_long_ago(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        with pytest.raises(AppError) as e:
            decode_access(pair.access_token, now=T0 + timedelta(days=31))
        assert e.value.code == "token_expired"

    @pytest.mark.parametrize("bad", ["", "abc", "a.b.c", "Bearer xyz", "....", "x" * 500])
    def test_garbage_is_unauthorized(self, bad):
        with pytest.raises(AppError) as e:
            decode_access(bad, now=T0)
        assert e.value.code == "unauthorized"
        assert e.value.http_status == 401

    def test_wrong_signature_is_unauthorized(self, user):
        forged = jwt.encode({"sub": "usr_01", "workspace_id": "ws_1", "is_admin": True,
                             "iat": int(T0.timestamp()), "exp": int(T0.timestamp()) + 900},
                            "another-secret-another-secret-123456", algorithm="HS256")
        with pytest.raises(AppError) as e:
            decode_access(forged, now=T0)
        assert e.value.code == "unauthorized"

    def test_tampered_payload_is_unauthorized(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        head, payload, sig = pair.access_token.split(".")
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        data["is_admin"] = True
        forged_payload = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
        with pytest.raises(AppError) as e:
            decode_access(f"{head}.{forged_payload}.{sig}", now=T0)
        assert e.value.code == "unauthorized"

    def test_alg_none_is_rejected(self, user):
        unsigned = jwt.encode({"sub": "usr_01", "workspace_id": "ws_1", "is_admin": True,
                               "iat": int(T0.timestamp()), "exp": int(T0.timestamp()) + 900},
                              key=None, algorithm="none")
        with pytest.raises(AppError) as e:
            decode_access(unsigned, now=T0)
        assert e.value.code == "unauthorized"

    def test_opaque_refresh_is_not_accepted_as_access(self, user):
        pair = issue_tokens(user, remember=True, now=T0)
        with pytest.raises(AppError) as e:
            decode_access(pair.refresh_token, now=T0)
        assert e.value.code == "unauthorized"

    def test_token_missing_required_claims_is_unauthorized(self):
        incomplete = jwt.encode({"sub": "usr_01", "exp": int(T0.timestamp()) + 900}, SECRET, algorithm="HS256")
        with pytest.raises(AppError) as e:
            decode_access(incomplete, now=T0)
        assert e.value.code == "unauthorized"


# --- каталог кодов -----------------------------------------------------------------------------

@pytest.mark.parametrize("code,status", [
    ("invalid_credentials", 401), ("refresh_invalid", 401), ("registration_disabled", 403),
    ("account_disabled", 403), ("email_taken", 409), ("too_many_attempts", 429), ("too_many_requests", 429),
])
def test_auth_codes_have_http_status_in_catalog_or_in_apperror(code, status):
    """Коды из api_contract §1.5 должны давать верный HTTP-статус (по каталогу, а не 500 по умолчанию)."""
    assert AppError(code).http_status == status, f"{code} отсутствует в ERROR_CATALOG: {code in ERROR_CATALOG}"


# --- LoginLimiter ------------------------------------------------------------------------------

class TestLoginLimiter:
    def test_fresh_key_passes(self):
        LoginLimiter().check("email:a@b.c", now=T0)

    def test_four_failures_still_allowed(self):
        lim = LoginLimiter()
        for i in range(4):
            lim.fail("email:a@b.c", now=T0 + timedelta(seconds=i))
        lim.check("email:a@b.c", now=T0 + timedelta(seconds=5))

    def test_five_failures_block_sixth_attempt(self):
        lim = LoginLimiter()
        for i in range(5):
            lim.check("email:a@b.c", now=T0 + timedelta(seconds=i))
            lim.fail("email:a@b.c", now=T0 + timedelta(seconds=i))
        with pytest.raises(AppError) as e:
            lim.check("email:a@b.c", now=T0 + timedelta(seconds=6))
        assert e.value.code == "too_many_attempts"
        assert e.value.http_status == 429
        retry = e.value.details["retry_after"]
        assert isinstance(retry, int) and 0 < retry <= 15 * 60

    def test_retry_after_shrinks_with_time(self):
        lim = LoginLimiter()
        for _ in range(5):
            lim.fail("email:a@b.c", now=T0)
        with pytest.raises(AppError) as e1:
            lim.check("email:a@b.c", now=T0 + timedelta(seconds=10))
        with pytest.raises(AppError) as e2:
            lim.check("email:a@b.c", now=T0 + timedelta(seconds=310))
        assert e2.value.details["retry_after"] < e1.value.details["retry_after"]

    def test_window_expires_after_15_minutes(self):
        lim = LoginLimiter()
        for _ in range(5):
            lim.fail("email:a@b.c", now=T0)
        lim.check("email:a@b.c", now=T0 + timedelta(minutes=15, seconds=1))

    def test_still_blocked_just_before_window_end(self):
        lim = LoginLimiter()
        for _ in range(5):
            lim.fail("email:a@b.c", now=T0)
        with pytest.raises(AppError):
            lim.check("email:a@b.c", now=T0 + timedelta(minutes=14, seconds=59))

    def test_keys_are_independent(self):
        lim = LoginLimiter()
        for _ in range(5):
            lim.fail("email:a@b.c", now=T0)
        lim.check("email:other@b.c", now=T0)

    def test_reset_clears_counter(self):
        lim = LoginLimiter()
        for _ in range(4):
            lim.fail("email:a@b.c", now=T0)
        lim.reset("email:a@b.c")
        for _ in range(4):
            lim.fail("email:a@b.c", now=T0)
        lim.check("email:a@b.c", now=T0)  # 4 < 5 после сброса

    def test_reset_unknown_key_is_noop(self):
        LoginLimiter().reset("email:never@seen")

    def test_ip_limit_is_20_not_5(self):
        lim = LoginLimiter()
        for _ in range(19):
            lim.fail("ip:10.0.0.1", now=T0)
        lim.check("ip:10.0.0.1", now=T0)
        lim.fail("ip:10.0.0.1", now=T0)
        with pytest.raises(AppError) as e:
            lim.check("ip:10.0.0.1", now=T0)
        assert e.value.code == "too_many_attempts"

    def test_failures_spread_over_window_expire_individually(self):
        lim = LoginLimiter()
        for i in range(5):
            lim.fail("email:a@b.c", now=T0 + timedelta(minutes=3 * i))  # 0, 3, 6, 9, 12 мин
        with pytest.raises(AppError):
            lim.check("email:a@b.c", now=T0 + timedelta(minutes=13))
        lim.check("email:a@b.c", now=T0 + timedelta(minutes=15, seconds=1))  # самая старая выпала из окна


# --- RateLimiter -------------------------------------------------------------------------------

class TestRateLimiter:
    def test_600_hits_per_minute_allowed(self):
        rl = RateLimiter()
        for _ in range(600):
            rl.hit("user:u1", now=T0)

    def test_601st_hit_is_rate_limited(self):
        rl = RateLimiter()
        for _ in range(600):
            rl.hit("user:u1", now=T0)
        with pytest.raises(AppError) as e:
            rl.hit("user:u1", now=T0 + timedelta(seconds=1))
        assert e.value.http_status == 429
        retry = e.value.details["retry_after"]
        assert isinstance(retry, int) and 1 <= retry <= 60

    def test_still_limited_before_window_end(self):
        rl = RateLimiter()
        for _ in range(600):
            rl.hit("user:u1", now=T0)
        with pytest.raises(AppError):
            rl.hit("user:u1", now=T0 + timedelta(seconds=59))

    def test_window_slides_after_60s(self):
        rl = RateLimiter()
        for _ in range(600):
            rl.hit("user:u1", now=T0)
        rl.hit("user:u1", now=T0 + timedelta(seconds=61))

    def test_keys_are_independent(self):
        rl = RateLimiter()
        for _ in range(600):
            rl.hit("user:u1", now=T0)
        rl.hit("user:u2", now=T0)

    def test_rejected_hits_do_not_extend_the_block_forever(self):
        rl = RateLimiter()
        for _ in range(600):
            rl.hit("user:u1", now=T0)
        for s in range(1, 30):
            with pytest.raises(AppError):
                rl.hit("user:u1", now=T0 + timedelta(seconds=s))
        rl.hit("user:u1", now=T0 + timedelta(seconds=61))

    def test_hits_spread_in_time_do_not_trigger(self):
        rl = RateLimiter()
        for i in range(600):  # 5 запросов в секунду в течение 2 минут: в любом окне 60 с около 300
            rl.hit("user:u1", now=T0 + timedelta(seconds=i / 5))

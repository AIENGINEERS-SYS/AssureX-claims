"""Rate-limit integration and adversarial fuzz tests.

These tests intentionally avoid database access: malformed auth requests exercise the
limiter before route validation/authentication, so failures point at rate limiting
rather than unrelated persistence setup.
"""
from ipaddress import IPv6Address
import random

import pytest

from backend import create_app


def make_limited_app(test_name, **overrides):
    config = {
        "TESTING": True,
        "ASSUREX_ENV": "development",
        "JWT_SECRET_KEY": "rate-limit-test-key-" * 3,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "BCRYPT_LOG_ROUNDS": 4,
        "OCR_PROVIDER": "disabled",
        "RATELIMIT_ENABLED": True,
        "RATELIMIT_STORAGE_URI": "memory://",
        "RATELIMIT_HEADERS_ENABLED": True,
        "RATELIMIT_KEY_PREFIX": f"pytest-rate-limit-{test_name}",
        "RATELIMIT_TRUST_PROXY_HEADERS": False,
        "RATELIMIT_TRUSTED_PROXY_COUNT": 1,
    }
    config.update(overrides)
    return create_app(config)


@pytest.fixture
def app(request):
    return make_limited_app(request.node.name)


@pytest.fixture
def client(app):
    return app.test_client()


def auth_probe(client, path, *, remote="203.0.113.10", forwarded=None):
    headers = {}
    if forwarded is not None:
        headers["X-Forwarded-For"] = forwarded
    return client.post(
        path,
        json={},
        headers=headers,
        environ_overrides={"REMOTE_ADDR": remote},
    )


@pytest.mark.parametrize(
    ("path", "limit", "prelimit_status"),
    [
        ("/api/auth/register", 5, 400),
        ("/api/auth/login", 10, 400),
        ("/api/auth/password", 5, 401),
        ("/api/auth/refresh", 30, 401),
    ],
)
def test_sensitive_auth_limits_enforce_exact_boundary(client, path, limit, prelimit_status):
    responses = [auth_probe(client, path) for _ in range(limit)]

    assert all(response.status_code == prelimit_status for response in responses)
    assert responses[0].headers["X-RateLimit-Limit"] == str(limit)
    assert responses[-1].headers["X-RateLimit-Remaining"] == "0"

    blocked = auth_probe(client, path)
    assert blocked.status_code == 429
    assert blocked.json["error"]["code"] == "too_many_requests"
    assert blocked.headers["X-RateLimit-Limit"] == str(limit)
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    assert blocked.headers.get("Retry-After")


def test_default_limit_applies_to_undecorated_health_route(client):
    for _ in range(200):
        response = client.get(
            "/api/health", environ_overrides={"REMOTE_ADDR": "203.0.113.11"}
        )
        assert response.status_code == 200

    blocked = client.get(
        "/api/health", environ_overrides={"REMOTE_ADDR": "203.0.113.11"}
    )
    assert blocked.status_code == 429
    assert blocked.json["error"]["code"] == "too_many_requests"
    assert blocked.headers["X-RateLimit-Limit"] == "200"


def test_endpoint_specific_buckets_do_not_cross_contaminate(client):
    for _ in range(5):
        assert auth_probe(client, "/api/auth/register").status_code == 400
    assert auth_probe(client, "/api/auth/register").status_code == 429

    # Exhausting registration must not consume the distinct login bucket.
    login = auth_probe(client, "/api/auth/login")
    assert login.status_code == 400
    assert login.headers["X-RateLimit-Limit"] == "10"
    assert login.headers["X-RateLimit-Remaining"] == "9"


def test_rate_limit_buckets_are_isolated_by_socket_peer(client):
    for _ in range(5):
        assert auth_probe(
            client, "/api/auth/register", remote="203.0.113.20"
        ).status_code == 400
    assert auth_probe(
        client, "/api/auth/register", remote="203.0.113.20"
    ).status_code == 429

    other_client = auth_probe(
        client, "/api/auth/register", remote="203.0.113.21"
    )
    assert other_client.status_code == 400
    assert other_client.headers["X-RateLimit-Remaining"] == "4"


def test_untrusted_forwarded_for_cannot_bypass_limit(client):
    for i in range(5):
        response = auth_probe(
            client,
            "/api/auth/register",
            remote="10.0.0.8",
            forwarded=f"198.51.100.{i + 1}",
        )
        assert response.status_code == 400

    blocked = auth_probe(
        client,
        "/api/auth/register",
        remote="10.0.0.8",
        forwarded="192.0.2.250",
    )
    assert blocked.status_code == 429


def test_trusted_proxy_uses_rightmost_selected_client_hop(app):
    app.config["RATELIMIT_TRUST_PROXY_HEADERS"] = True
    app.config["RATELIMIT_TRUSTED_PROXY_COUNT"] = 1
    client = app.test_client()

    # Attacker-controlled prefixes change, but the trusted proxy-appended client
    # address remains the rightmost forwarded hop and therefore the bucket key.
    for i in range(5):
        response = auth_probe(
            client,
            "/api/auth/register",
            remote="10.0.0.9",
            forwarded=f"198.51.100.{i + 1}, 203.0.113.30",
        )
        assert response.status_code == 400

    assert auth_probe(
        client,
        "/api/auth/register",
        remote="10.0.0.9",
        forwarded="192.0.2.99, 203.0.113.30",
    ).status_code == 429

    # A genuinely different client behind the same trusted proxy gets its own bucket.
    assert auth_probe(
        client,
        "/api/auth/register",
        remote="10.0.0.9",
        forwarded="192.0.2.99, 203.0.113.31",
    ).status_code == 400


def test_equivalent_ipv6_spellings_share_one_bucket(client):
    address = IPv6Address("2001:db8::5")
    spellings = (address.compressed, address.exploded)

    for i in range(5):
        assert auth_probe(
            client, "/api/auth/register", remote=spellings[i % 2]
        ).status_code == 400

    assert auth_probe(
        client, "/api/auth/register", remote=spellings[1]
    ).status_code == 429


def test_ipv4_mapped_ipv6_and_ipv4_share_one_bucket(client):
    aliases = ("192.0.2.55", "::ffff:192.0.2.55")
    for i in range(5):
        assert auth_probe(
            client, "/api/auth/register", remote=aliases[i % 2]
        ).status_code == 400

    assert auth_probe(
        client, "/api/auth/register", remote=aliases[1]
    ).status_code == 429


@pytest.mark.parametrize("count", [0, -1, 11, True])
def test_invalid_trusted_proxy_count_is_rejected(count):
    with pytest.raises(RuntimeError, match="RATELIMIT_TRUSTED_PROXY_COUNT"):
        make_limited_app(f"bad-hop-{count}", RATELIMIT_TRUSTED_PROXY_COUNT=count)


def test_non_boolean_proxy_trust_flag_is_rejected():
    with pytest.raises(RuntimeError, match="RATELIMIT_TRUST_PROXY_HEADERS"):
        make_limited_app(
            "bad-proxy-flag", RATELIMIT_TRUST_PROXY_HEADERS="true"
        )


# ---------------------------------------------------------------- fuzz-style security probes


def test_fuzz_rotating_forwarded_for_cannot_escape_untrusted_bucket(client):
    rng = random.Random(20260928)
    statuses = []
    for _ in range(40):
        forwarded = ".".join(str(rng.randint(1, 254)) for _ in range(4))
        statuses.append(
            auth_probe(
                client,
                "/api/auth/register",
                remote="10.10.10.10",
                forwarded=forwarded,
            ).status_code
        )

    assert statuses[:5] == [400] * 5
    assert statuses[5:] == [429] * 35


def test_fuzz_attacker_prefixes_cannot_rotate_trusted_proxy_bucket(app):
    app.config["RATELIMIT_TRUST_PROXY_HEADERS"] = True
    app.config["RATELIMIT_TRUSTED_PROXY_COUNT"] = 1
    client = app.test_client()
    rng = random.Random(424242)

    statuses = []
    for _ in range(30):
        prefix_count = rng.randint(0, 5)
        prefixes = [
            ".".join(str(rng.randint(1, 254)) for _ in range(4))
            for _ in range(prefix_count)
        ]
        chain = ", ".join(prefixes + ["203.0.113.77"])
        statuses.append(
            auth_probe(
                client,
                "/api/auth/register",
                remote="10.0.0.77",
                forwarded=chain,
            ).status_code
        )

    assert statuses[:5] == [400] * 5
    assert statuses[5:] == [429] * 25


def test_fuzz_two_trusted_hops_ignore_untrusted_prefixes(app):
    app.config["RATELIMIT_TRUST_PROXY_HEADERS"] = True
    app.config["RATELIMIT_TRUSTED_PROXY_COUNT"] = 2
    client = app.test_client()
    rng = random.Random(8080)

    for i in range(5):
        prefixes = [
            ".".join(str(rng.randint(1, 254)) for _ in range(4))
            for _ in range(rng.randint(0, 4))
        ]
        forwarded = ", ".join(prefixes + ["203.0.113.88", "10.20.30.40"])
        assert auth_probe(
            client,
            "/api/auth/register",
            remote="10.20.30.41",
            forwarded=forwarded,
        ).status_code == 400

    blocked_chain = "192.0.2.1, 203.0.113.88, 10.20.30.40"
    assert auth_probe(
        client,
        "/api/auth/register",
        remote="10.20.30.41",
        forwarded=blocked_chain,
    ).status_code == 429

    other_client_chain = "192.0.2.1, 203.0.113.89, 10.20.30.40"
    assert auth_probe(
        client,
        "/api/auth/register",
        remote="10.20.30.41",
        forwarded=other_client_chain,
    ).status_code == 400


def test_fuzz_malformed_trusted_client_hops_fail_closed_to_peer(app):
    app.config["RATELIMIT_TRUST_PROXY_HEADERS"] = True
    app.config["RATELIMIT_TRUSTED_PROXY_COUNT"] = 1
    client = app.test_client()
    rng = random.Random(31337)

    malformed = [
        "unknown",
        "999.999.999.999",
        "not-an-ip",
        "1.2.3",
        "[2001:db8::1]:443",
    ]
    malformed.extend(
        f"bad-{rng.getrandbits(48):012x}" for _ in range(20)
    )

    statuses = [
        auth_probe(
            client,
            "/api/auth/register",
            remote="10.0.0.90",
            forwarded=value,
        ).status_code
        for value in malformed
    ]

    assert statuses[:5] == [400] * 5
    assert statuses[5:] == [429] * (len(statuses) - 5)

from __future__ import annotations

import sys
import types

from universal_agent import youtube_ingest


def test_extract_video_id_from_watch_url() -> None:
    assert youtube_ingest.extract_video_id("https://www.youtube.com/watch?v=dxlyCPGCvy8") == "dxlyCPGCvy8"


def test_classify_api_error_detects_proxy_billing_issue() -> None:
    cls = youtube_ingest._classify_api_error(
        "youtube_transcript_api_failed",
        "Tunnel connection failed: 402 Payment Required",
    )
    assert cls == "proxy_quota_or_billing"


def test_classify_api_error_detects_proxy_auth_issue() -> None:
    cls = youtube_ingest._classify_api_error(
        "youtube_transcript_api_failed",
        "407 Proxy Authentication Required",
    )
    assert cls == "proxy_auth_failed"


def test_classify_api_error_detects_webshare_no_proxies_allocated() -> None:
    cls = youtube_ingest._classify_api_error(
        "youtube_transcript_api_failed",
        "Configuration no_proxies_allocated 407 You are connecting to a proxy which is not in your proxy list anymore.",
    )
    assert cls == "proxy_pool_unallocated"


def test_classify_api_error_detects_webshare_invalid_auth_credentials_message() -> None:
    cls = youtube_ingest._classify_api_error(
        "proxy_http_error",
        "Not authenticated or invalid authentication credentials. Make sure to update your proxy address, proxy username and port.",
    )
    assert cls == "proxy_auth_failed"


def test_classify_api_error_detects_proxy_connect_tunnel_failure() -> None:
    cls = youtube_ingest._classify_api_error(
        "youtube_transcript_api_failed",
        "Tunnel connection failed: 404 Not Found",
    )
    assert cls == "proxy_connect_failed"


def test_classify_api_error_detects_proxy_connect_unreachable() -> None:
    cls = youtube_ingest._classify_api_error(
        "yt_dlp_metadata_failed",
        "Unable to connect to proxy",
    )
    assert cls == "proxy_connect_failed"


def test_build_webshare_proxy_config_defaults_to_current_residential_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_USERNAME", "proxy-user")
    monkeypatch.setenv("PROXY_PASSWORD", "proxy-pass")
    monkeypatch.delenv("WEBSHARE_PROXY_HOST", raising=False)
    monkeypatch.delenv("PROXY_HOST", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PORT", raising=False)
    monkeypatch.delenv("PROXY_PORT", raising=False)

    class FakeProxyConfig:
        def __init__(self, **kwargs):
            self.domain_name = kwargs["domain_name"]
            self.proxy_port = kwargs["proxy_port"]
            self.url = (
                f"http://{kwargs['proxy_username']}:{kwargs['proxy_password']}"
                f"@{kwargs['domain_name']}:{kwargs['proxy_port']}"
            )

    monkeypatch.setitem(
        sys.modules,
        "youtube_transcript_api.proxies",
        types.SimpleNamespace(WebshareProxyConfig=FakeProxyConfig),
    )

    config, mode = youtube_ingest._build_webshare_proxy_config()

    assert mode == "webshare"
    assert config is not None
    assert config.domain_name == "proxy.webshare.io"
    assert config.proxy_port == 80


def test_classify_api_error_detects_video_unavailable() -> None:
    cls = youtube_ingest._classify_api_error(
        "youtube_transcript_api_failed",
        "Could not retrieve transcript. The video is no longer available",
    )
    assert cls == "video_unavailable"


def test_classify_api_error_detects_transcript_unavailable() -> None:
    cls = youtube_ingest._classify_api_error(
        "youtube_transcript_api_failed",
        "Subtitles are disabled for this video",
    )
    assert cls == "transcript_unavailable"


def test_normalize_video_target_from_video_id() -> None:
    url, video_id = youtube_ingest.normalize_video_target(None, "dxlyCPGCvy8")
    assert video_id == "dxlyCPGCvy8"
    assert url == "https://www.youtube.com/watch?v=dxlyCPGCvy8"


def test_ingest_youtube_transcript_uses_youtube_transcript_api(monkeypatch) -> None:
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source": "youtube_transcript_api",
            "transcript_text": "line 1\nline 2\nline 3\nline 4\nline 5\nline 6",
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        max_chars=1000,
        min_chars=20,
    )
    assert out["ok"] is True
    assert out["status"] == "succeeded"
    assert out["source"] == "youtube_transcript_api"
    assert out["transcript_quality_pass"] is True


def test_ingest_youtube_transcript_fails_for_low_quality(monkeypatch) -> None:
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source": "youtube_transcript_api",
            "transcript_text": "thanks for watching",
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        max_chars=1000,
        min_chars=20,
    )
    assert out["ok"] is False
    assert out["error"] == "transcript_quality_failed"
    assert out["failure_class"] == "empty_or_low_quality_transcript"
    assert isinstance(out.get("transcript_quality_reason"), str)


def test_ingest_youtube_transcript_failure_class_passthrough(monkeypatch) -> None:
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error": "youtube_transcript_api_failed",
            "failure_class": "request_blocked",
            "detail": "Sign in to confirm you're not a bot",
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
    )
    assert out["ok"] is False
    assert out["failure_class"] == "request_blocked"


def test_ingest_youtube_transcript_includes_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source": "youtube_transcript_api",
            "transcript_text": "line 1\nline 2\nline 3\nline 4\nline 5\nline 6",
        },
    )
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_metadata_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source": "yt_dlp",
            "metadata": {"title": "Demo Title", "channel": "Demo Channel"},
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        max_chars=1000,
        min_chars=20,
    )

    assert out["ok"] is True
    assert out["metadata_status"] == "attempted_succeeded"
    assert out["metadata_source"] == "yt_dlp"
    assert out["metadata"]["title"] == "Demo Title"


def test_ingest_youtube_transcript_failure_still_includes_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error": "youtube_transcript_api_failed",
            "failure_class": "request_blocked",
        },
    )
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_metadata_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source": "yt_dlp",
            "metadata": {"title": "Still Useful"},
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
    )

    assert out["ok"] is False
    assert out["failure_class"] == "request_blocked"
    assert out["metadata_status"] == "attempted_succeeded"
    assert out["metadata"]["title"] == "Still Useful"


def test_run_extract_enforces_english_without_proxy(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeFetched:
        snippets = [types.SimpleNamespace(text="hello"), types.SimpleNamespace(text="world")]

    class FakeApi:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs

        def fetch(self, video_id: str, languages=None):
            captured["video_id"] = video_id
            captured["languages"] = languages
            return FakeFetched()

    monkeypatch.delenv("PROXY_USERNAME", raising=False)
    monkeypatch.delenv("PROXY_PASSWORD", raising=False)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", types.SimpleNamespace(YouTubeTranscriptApi=FakeApi))
    monkeypatch.delitem(sys.modules, "youtube_transcript_api.proxies", raising=False)

    out = youtube_ingest._run_youtube_transcript_api_extract("dxlyCPGCvy8")

    assert out["ok"] is True
    assert out["language"] == "en"
    assert out["proxy_mode"] == "disabled"
    assert captured["video_id"] == "dxlyCPGCvy8"
    assert captured["languages"] == ["en"]
    assert captured["init_kwargs"] == {}


def test_run_extract_uses_webshare_proxy_when_env_present(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeFetched:
        snippets = [types.SimpleNamespace(text="line1"), types.SimpleNamespace(text="line2")]

    class FakeProxyConfig:
        def __init__(self, **kwargs):
            captured["proxy_kwargs"] = kwargs
            self.url = "http://proxy.example"

    class FakeApi:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs

        def fetch(self, video_id: str, languages=None):
            captured["video_id"] = video_id
            captured["languages"] = languages
            return FakeFetched()

    monkeypatch.setenv("PROXY_USERNAME", "proxy-user")
    monkeypatch.setenv("PROXY_PASSWORD", "proxy-pass")
    monkeypatch.setenv("PROXY_FILTER_IP_LOCATIONS", "us,de,us")
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", types.SimpleNamespace(YouTubeTranscriptApi=FakeApi))
    monkeypatch.setitem(
        sys.modules,
        "youtube_transcript_api.proxies",
        types.SimpleNamespace(WebshareProxyConfig=FakeProxyConfig),
    )

    proxy_config, proxy_mode = youtube_ingest._build_webshare_proxy_config()
    out = youtube_ingest._run_youtube_transcript_api_extract(
        "dxlyCPGCvy8",
        proxy_config=proxy_config,
        proxy_mode=proxy_mode,
    )

    assert out["ok"] is True
    assert out["proxy_mode"] == "webshare"
    assert captured["video_id"] == "dxlyCPGCvy8"
    assert captured["languages"] == ["en"]
    assert captured["proxy_kwargs"] == {
        "proxy_username": "proxy-user",
        "proxy_password": "proxy-pass",
        "domain_name": "proxy.webshare.io",
        "proxy_port": 80,
        "filter_ip_locations": ["us", "de"],
    }
    assert "proxy_config" in captured["init_kwargs"]


def test_run_extract_retries_with_fresh_proxy_on_transient_failure(monkeypatch) -> None:
    """First proxy attempt fails → fresh YouTubeTranscriptApi() reconnect → success.

    This locks in the residential-proxy bad-IP retry behavior. Each new
    YouTubeTranscriptApi() opens a new proxy connection and lands on a
    different egress IP, so a single bad IP must NOT poison the whole
    fetch — we retry up to UA_YOUTUBE_TRANSCRIPT_PROXY_RETRIES times.
    """
    construction_log: list[dict[str, object]] = []
    fetch_calls: list[int] = [0]

    class FakeFetched:
        snippets = [types.SimpleNamespace(text="hello"), types.SimpleNamespace(text="world")]

    class FakeProxyConfig:
        def __init__(self, **kwargs):
            self.url = "http://proxy.example"

    class FakeApi:
        def __init__(self, **kwargs):
            construction_log.append(kwargs)

        def fetch(self, video_id: str, languages=None):
            fetch_calls[0] += 1
            if fetch_calls[0] == 1:
                raise RuntimeError("simulated bad-IP block on first attempt")
            return FakeFetched()

    monkeypatch.setenv("UA_YOUTUBE_TRANSCRIPT_PROXY_RETRIES", "3")
    monkeypatch.delenv("UA_YOUTUBE_TRANSCRIPT_NOPROXY_FALLBACK", raising=False)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", types.SimpleNamespace(YouTubeTranscriptApi=FakeApi))

    out = youtube_ingest._run_youtube_transcript_api_extract(
        "dxlyCPGCvy8",
        proxy_config=FakeProxyConfig(),
        proxy_mode="webshare",
    )

    assert out["ok"] is True
    assert out["proxy_mode"] == "webshare"
    assert fetch_calls[0] == 2
    assert len(construction_log) == 2
    assert "proxy_config" in construction_log[0]
    assert "proxy_config" in construction_log[1]


def test_run_extract_does_not_fall_back_to_no_proxy_by_default(monkeypatch) -> None:
    """All proxy attempts fail → return failure, do NOT silently retry without proxy.

    Datacenter no-proxy attempts are guaranteed to be blocked by YouTube,
    so falling back to no-proxy from a VPS just hides the real error and
    burns a video unnecessarily. Opt-in only via
    UA_YOUTUBE_TRANSCRIPT_NOPROXY_FALLBACK=1.
    """
    construction_log: list[dict[str, object]] = []

    class FakeProxyConfig:
        def __init__(self, **kwargs):
            self.url = "http://proxy.example"

    class FakeApi:
        def __init__(self, **kwargs):
            construction_log.append(kwargs)

        def fetch(self, video_id: str, languages=None):
            raise RuntimeError("RequestBlocked simulated on every IP")

    monkeypatch.setenv("UA_YOUTUBE_TRANSCRIPT_PROXY_RETRIES", "3")
    monkeypatch.delenv("UA_YOUTUBE_TRANSCRIPT_NOPROXY_FALLBACK", raising=False)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", types.SimpleNamespace(YouTubeTranscriptApi=FakeApi))

    out = youtube_ingest._run_youtube_transcript_api_extract(
        "dxlyCPGCvy8",
        proxy_config=FakeProxyConfig(),
        proxy_mode="webshare",
    )

    assert out["ok"] is False
    assert out["error"] == "youtube_transcript_api_failed"
    assert out["failure_class"] == "request_blocked"
    # Three attempts, all through the proxy. None without.
    assert len(construction_log) == 3
    for kwargs in construction_log:
        assert "proxy_config" in kwargs


def test_require_proxy_blocks_when_no_credentials(monkeypatch) -> None:
    """require_proxy=True with no proxy creds → immediate hard failure.

    Provider router (`_build_proxy_config`) defaults to dataimpulse; covering
    both provider branches by parametrizing PROXY_PROVIDER.
    """
    # Clear ALL provider creds so neither builder can succeed.
    for var in (
        "PROXY_USERNAME", "PROXY_PASSWORD",
        "WEBSHARE_PROXY_USER", "WEBSHARE_PROXY_PASS",
        "DATAIMPULSE_PROXY_USER", "DATAIMPULSE_PROXY_PASS",
    ):
        monkeypatch.delenv(var, raising=False)

    # Default provider (dataimpulse) — error message should mention
    # DATAIMPULSE_PROXY_USER as the env var to set.
    monkeypatch.delenv("PROXY_PROVIDER", raising=False)
    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        require_proxy=True,
    )
    assert out["ok"] is False
    assert out["error"] == "proxy_not_configured"
    assert out["failure_class"] == "proxy_not_configured"
    assert out["proxy_mode"] == "disabled"
    assert "PROXY NOT CONFIGURED" in out["detail"]
    assert "DATAIMPULSE_PROXY_USER" in out["detail"]
    assert out["attempts"] == []

    # Webshare provider branch — error message should mention PROXY_USERNAME.
    monkeypatch.setenv("PROXY_PROVIDER", "webshare")
    out2 = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        require_proxy=True,
    )
    assert out2["ok"] is False
    assert out2["error"] == "proxy_not_configured"
    assert "PROXY_USERNAME" in out2["detail"]


def test_require_proxy_blocks_when_module_unavailable(monkeypatch) -> None:
    """require_proxy=True with creds but missing proxies module → hard failure.

    Patches the provider-router entry point (`_build_proxy_config`) so this
    test stays correct regardless of which provider is the current default.
    """
    monkeypatch.setenv("PROXY_USERNAME", "user")
    monkeypatch.setenv("PROXY_PASSWORD", "pass")
    monkeypatch.setenv("DATAIMPULSE_PROXY_USER", "user")
    monkeypatch.setenv("DATAIMPULSE_PROXY_PASS", "pass")
    monkeypatch.delitem(sys.modules, "youtube_transcript_api.proxies", raising=False)

    def _mock_build():
        return None, "module_unavailable"

    monkeypatch.setattr(youtube_ingest, "_build_proxy_config", _mock_build)

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        require_proxy=True,
    )

    assert out["ok"] is False
    assert out["error"] == "proxy_not_configured"
    assert out["failure_class"] == "proxy_not_configured"
    assert out["proxy_mode"] == "module_unavailable"
    assert "WebshareProxyConfig" in out["detail"]


def test_require_proxy_false_allows_no_proxy(monkeypatch) -> None:
    """require_proxy=False (default) proceeds even without proxy creds."""
    monkeypatch.delenv("PROXY_USERNAME", raising=False)
    monkeypatch.delenv("PROXY_PASSWORD", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_USER", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASS", raising=False)

    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source": "youtube_transcript_api",
            "transcript_text": "line 1\nline 2\nline 3\nline 4\nline 5\nline 6",
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        require_proxy=False,
        max_chars=1000,
        min_chars=20,
    )

    assert out["ok"] is True
    assert out["proxy_mode"] == "disabled"


def test_require_proxy_true_proceeds_with_valid_proxy(monkeypatch) -> None:
    """require_proxy=True with valid proxy creds → proceeds normally.

    Patches the provider-router entry point (`_build_proxy_config`) so the
    test pins the proxy_mode it expects (`webshare`) without depending on
    which provider is currently the default.
    """
    class FakeProxyConfig:
        def __init__(self, **kwargs):
            self.url = "http://proxy.example"

    fake_proxy = FakeProxyConfig()
    monkeypatch.setattr(
        youtube_ingest,
        "_build_proxy_config",
        lambda: (fake_proxy, "webshare"),
    )
    monkeypatch.setattr(
        youtube_ingest,
        "_run_youtube_transcript_api_extract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source": "youtube_transcript_api",
            "transcript_text": "line 1\nline 2\nline 3\nline 4\nline 5\nline 6",
        },
    )

    out = youtube_ingest.ingest_youtube_transcript(
        video_url="https://www.youtube.com/watch?v=dxlyCPGCvy8",
        video_id=None,
        require_proxy=True,
        max_chars=1000,
        min_chars=20,
    )

    assert out["ok"] is True
    assert out["proxy_mode"] == "webshare"

"""Guard: the canonical nginx vhost must route /briefs/* to the gateway (:8002).

Regression context
------------------
The hourly intel digest emails "Read full brief →" links to
``https://app.clearspringcg.com/briefs/<artifact_id>``. That page is served by
the UA gateway (``gateway_server.py::briefs_viewer_get``) on port 8002 — NOT the
Next.js frontend on :3000. If nginx lacks an explicit ``location ^~ /briefs/``
proxy block, those requests fall through to ``location /`` (Next.js) and return
"404: This page could not be found", silently breaking every brief link in every
digest email.

That routing block once lived only as a hand-edit on the VPS and was missing from
the repo's source-of-truth config (config drift). This test pins it in
``deploy/nginx/universal-agent-app`` so it can't silently regress.
"""

from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_VHOST = REPO_ROOT / "deploy" / "nginx" / "universal-agent-app"


def test_nginx_vhost_exists():
    assert NGINX_VHOST.is_file(), f"missing canonical nginx vhost: {NGINX_VHOST}"


def test_briefs_routes_to_gateway_8002():
    text = NGINX_VHOST.read_text(encoding="utf-8")

    assert "location ^~ /briefs/" in text, (
        "nginx vhost is missing `location ^~ /briefs/` — brief links will 404 by "
        "falling through to the Next.js frontend (:3000)."
    )

    match = re.search(r"location\s+\^~\s+/briefs/\s*\{(.*?)\}", text, re.DOTALL)
    assert match, "could not parse the /briefs/ location block"
    block = match.group(1)

    assert "127.0.0.1:8002" in block, (
        "/briefs/ must proxy_pass to the gateway at 127.0.0.1:8002 (the brief "
        "viewer route), not anywhere else."
    )
    assert "127.0.0.1:3000" not in block, (
        "/briefs/ must NOT proxy to the Next.js frontend (:3000) — that frontend "
        "has no brief route and returns a 404 page."
    )

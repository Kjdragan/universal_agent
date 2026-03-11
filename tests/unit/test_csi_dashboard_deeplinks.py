from __future__ import annotations

from pathlib import Path


_PAGE = Path("web-ui/app/dashboard/csi/page.tsx")


def test_csi_dashboard_supports_event_and_notification_deeplinks():
    content = _PAGE.read_text(encoding="utf-8")
    assert 'params.get("notification_id")' in content
    assert 'params.get("event_id")' in content
    assert 'item?.metadata?.event_id' in content

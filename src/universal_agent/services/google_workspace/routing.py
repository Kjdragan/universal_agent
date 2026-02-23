from __future__ import annotations

from .models import ExecutionRoute, GoogleIntent, RoutingDecision

DIRECT_V1_INTENTS: frozenset[GoogleIntent] = frozenset(
    {
        GoogleIntent.GMAIL_READ,
        GoogleIntent.GMAIL_SEND_REPLY,
        GoogleIntent.GMAIL_MODIFY,
        GoogleIntent.CALENDAR_READ_WRITE,
        GoogleIntent.DRIVE_READ_DOWNLOAD_EXPORT,
        GoogleIntent.SHEETS_READ_APPEND,
    }
)


COMPOSIO_DEFAULT_INTENTS: frozenset[GoogleIntent] = frozenset(
    {
        GoogleIntent.DOCS_WRITE,
        GoogleIntent.LONG_TAIL_GOOGLE,
        GoogleIntent.CROSS_SAAS_ORCHESTRATION,
    }
)


def default_route_for_intent(intent: GoogleIntent) -> ExecutionRoute:
    if intent in DIRECT_V1_INTENTS:
        return ExecutionRoute.DIRECT
    return ExecutionRoute.COMPOSIO


def decide_route(
    intent: GoogleIntent,
    *,
    direct_enabled: bool,
    direct_implemented: set[GoogleIntent] | None = None,
    allow_composio_fallback: bool = True,
) -> RoutingDecision:
    route = default_route_for_intent(intent)
    implemented = direct_implemented if direct_implemented is not None else set(DIRECT_V1_INTENTS)

    if route is ExecutionRoute.COMPOSIO:
        return RoutingDecision(
            intent=intent,
            route=ExecutionRoute.COMPOSIO,
            reason="Intent is currently Composio-first by policy.",
            fallback_route=None,
        )

    if not direct_enabled:
        return RoutingDecision(
            intent=intent,
            route=ExecutionRoute.COMPOSIO,
            reason="Direct Google path is disabled by feature flag.",
            fallback_route=None,
        )

    if intent not in implemented:
        if allow_composio_fallback:
            return RoutingDecision(
                intent=intent,
                route=ExecutionRoute.COMPOSIO,
                reason="Direct path not implemented yet for this intent.",
                fallback_route=ExecutionRoute.DIRECT,
            )
        return RoutingDecision(
            intent=intent,
            route=ExecutionRoute.DIRECT,
            reason="Direct path selected but implementation is missing and fallback is disabled.",
            fallback_route=None,
        )

    return RoutingDecision(
        intent=intent,
        route=ExecutionRoute.DIRECT,
        reason="Intent is approved for direct v1 execution.",
        fallback_route=ExecutionRoute.COMPOSIO if allow_composio_fallback else None,
    )

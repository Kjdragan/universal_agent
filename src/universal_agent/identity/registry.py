"""Identity registry + email recipient resolution."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable

_DEFAULT_ALIAS_KEYS = {
    "me",
    "my email",
    "myself",
}

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _normalize_alias_key(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value or ""))


@dataclass(frozen=True)
class IdentityRegistry:
    primary_email: str | None
    secondary_emails: tuple[str, ...]
    aliases: dict[str, str]
    alias_keys: set[str]

    def resolve_alias(self, value: str) -> str | None:
        return self.aliases.get(_normalize_alias_key(value))

    def is_alias(self, value: str) -> bool:
        return _normalize_alias_key(value) in self.alias_keys

    def all_emails(self) -> tuple[str, ...]:
        emails = []
        if self.primary_email:
            emails.append(self.primary_email)
        emails.extend(self.secondary_emails)
        return tuple(emails)


def _load_registry_from_file(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle) or {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


def _build_aliases(
    primary_email: str | None,
    secondary_emails: Iterable[str],
    explicit_aliases: dict[str, str],
) -> tuple[dict[str, str], set[str]]:
    aliases: dict[str, str] = {}
    alias_keys = set(_DEFAULT_ALIAS_KEYS)
    if primary_email:
        for key in _DEFAULT_ALIAS_KEYS:
            aliases[_normalize_alias_key(key)] = primary_email
    for email in secondary_emails:
        if email.endswith("@gmail.com"):
            alias_keys.update({"my gmail", "gmail"})
            aliases.setdefault("my gmail", email)
        if email.endswith("@outlook.com") or email.endswith("@hotmail.com"):
            alias_keys.update({"my outlook", "outlook"})
            aliases.setdefault("my outlook", email)
    for key, value in explicit_aliases.items():
        alias_keys.add(_normalize_alias_key(key))
        if value:
            aliases[_normalize_alias_key(key)] = value
    return aliases, alias_keys


@lru_cache
def load_identity_registry() -> IdentityRegistry:
    registry_path = os.getenv("UA_IDENTITY_REGISTRY_PATH", "").strip()
    file_data: dict[str, Any] = {}
    if registry_path:
        file_data = _load_registry_from_file(registry_path)
    else:
        default_path = Path.cwd() / "identity_registry.json"
        if default_path.exists():
            file_data = _load_registry_from_file(str(default_path))

    primary_email = os.getenv("UA_PRIMARY_EMAIL") or file_data.get("primary_email")
    primary_email = primary_email.strip() if isinstance(primary_email, str) else None
    if primary_email and not _is_valid_email(primary_email):
        primary_email = None

    secondary_emails = _parse_csv(os.getenv("UA_SECONDARY_EMAILS"))
    file_secondary = file_data.get("secondary_emails") or []
    if isinstance(file_secondary, list):
        secondary_emails.extend(str(item).strip() for item in file_secondary if item)
    secondary_emails = [email for email in secondary_emails if _is_valid_email(email)]

    explicit_aliases: dict[str, str] = {}
    alias_pairs = _parse_csv(os.getenv("UA_EMAIL_ALIASES"))
    for pair in alias_pairs:
        if ":" in pair:
            alias, email = pair.split(":", 1)
            alias = alias.strip()
            email = email.strip()
            if alias and _is_valid_email(email):
                explicit_aliases[alias] = email
    file_aliases = file_data.get("aliases")
    if isinstance(file_aliases, dict):
        for key, value in file_aliases.items():
            if isinstance(key, str) and isinstance(value, str) and _is_valid_email(value):
                explicit_aliases[key] = value

    aliases, alias_keys = _build_aliases(primary_email, secondary_emails, explicit_aliases)
    return IdentityRegistry(
        primary_email=primary_email,
        secondary_emails=tuple(secondary_emails),
        aliases=aliases,
        alias_keys=alias_keys,
    )


def clear_identity_registry_cache() -> None:
    load_identity_registry.cache_clear()


def _resolve_email_value(
    value: Any, registry: IdentityRegistry
) -> tuple[Any, list[tuple[str, str]] , list[str]]:
    if isinstance(value, list):
        updated_list = []
        replacements: list[tuple[str, str]] = []
        errors: list[str] = []
        for item in value:
            updated_item, item_replacements, item_errors = _resolve_email_value(item, registry)
            updated_list.append(updated_item)
            replacements.extend(item_replacements)
            errors.extend(item_errors)
        return updated_list, replacements, errors
    if isinstance(value, str):
        normalized = _normalize_alias_key(value)
        if _is_valid_email(normalized):
            return value, [], []
        resolved = registry.resolve_alias(normalized)
        if resolved:
            return resolved, [(value, resolved)], []
        if registry.is_alias(normalized):
            return value, [], [value]
        return value, [], []
    return value, [], []


def _resolve_email_args(
    args: dict[str, Any], registry: IdentityRegistry
) -> tuple[dict[str, Any], list[tuple[str, str]], list[str]]:
    replacements: list[tuple[str, str]] = []
    errors: list[str] = []
    updated_args = dict(args)
    for key in ("recipient_email", "to", "cc", "bcc"):
        if key in updated_args:
            updated_value, value_replacements, value_errors = _resolve_email_value(
                updated_args[key], registry
            )
            if updated_value != updated_args[key]:
                updated_args[key] = updated_value
            replacements.extend(value_replacements)
            errors.extend(value_errors)
    return updated_args, replacements, errors


def _is_email_tool(name: str) -> bool:
    return "SEND_EMAIL" in name.upper()


def _should_enforce_recipient_policy() -> bool:
    return os.getenv("UA_ENFORCE_IDENTITY_RECIPIENTS", "").lower() in {"1", "true", "yes"}


def _extract_emails(value: Any) -> list[str]:
    if isinstance(value, list):
        emails: list[str] = []
        for item in value:
            emails.extend(_extract_emails(item))
        return emails
    if isinstance(value, str):
        normalized = value.strip()
        if _is_valid_email(normalized):
            return [normalized]
    return []


def _collect_recipients(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    recipients: list[str] = []
    if _is_email_tool(tool_name):
        for key in ("recipient_email", "to", "cc", "bcc"):
            if key in tool_input:
                recipients.extend(_extract_emails(tool_input[key]))
        return recipients
    if tool_name.upper().endswith("COMPOSIO_MULTI_EXECUTE_TOOL"):
        tools = tool_input.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                tool_slug = str(tool.get("tool_slug") or tool.get("tool_name") or "")
                if not _is_email_tool(tool_slug):
                    continue
                args = tool.get("arguments")
                if isinstance(args, dict):
                    recipients.extend(_collect_recipients(tool_slug, args))
    return recipients


def validate_recipient_policy(
    tool_name: str, tool_input: dict[str, Any], user_query: str
) -> list[str]:
    if not _should_enforce_recipient_policy():
        return []
    registry = load_identity_registry()
    allowed = set(registry.all_emails())
    query_text = (user_query or "").lower()
    invalid: list[str] = []
    for email in _collect_recipients(tool_name, tool_input):
        if email in allowed:
            continue
        if email.lower() in query_text:
            continue
        invalid.append(email)
    return invalid


def resolve_email_recipients(
    tool_name: str, tool_input: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str], list[tuple[str, str]]]:
    if not isinstance(tool_input, dict):
        return None, [], []

    registry = load_identity_registry()
    updates: dict[str, Any] | None = None
    errors: list[str] = []
    replacements: list[tuple[str, str]] = []

    if _is_email_tool(tool_name):
        updated_args, replacements, errors = _resolve_email_args(tool_input, registry)
        if updated_args != tool_input:
            updates = updated_args
        return updates, errors, replacements

    if tool_name.upper().endswith("COMPOSIO_MULTI_EXECUTE_TOOL"):
        tools = tool_input.get("tools")
        if not isinstance(tools, list):
            return None, [], []
        updated_tools = []
        changed = False
        for tool in tools:
            if not isinstance(tool, dict):
                updated_tools.append(tool)
                continue
            tool_slug = str(tool.get("tool_slug") or tool.get("tool_name") or "")
            if not _is_email_tool(tool_slug):
                updated_tools.append(tool)
                continue
            args = tool.get("arguments")
            if not isinstance(args, dict):
                updated_tools.append(tool)
                continue
            updated_args, tool_replacements, tool_errors = _resolve_email_args(args, registry)
            if updated_args != args:
                tool = dict(tool)
                tool["arguments"] = updated_args
                changed = True
            updated_tools.append(tool)
            replacements.extend(tool_replacements)
            errors.extend(tool_errors)
        if changed:
            updates = dict(tool_input)
            updates["tools"] = updated_tools
        return updates, errors, replacements

    return None, [], []

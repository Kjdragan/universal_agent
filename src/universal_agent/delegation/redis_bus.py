from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from universal_agent.delegation.schema import MissionEnvelope, MissionResultEnvelope

MISSION_STREAM = "ua:missions:delegation"
MISSION_CONSUMER_GROUP = "ua_workers"
MISSION_DLQ_STREAM = "ua:missions:dlq"


class RedisLikeClient(Protocol):
    def xgroup_create(self, *args: Any, **kwargs: Any) -> Any: ...
    def xadd(self, *args: Any, **kwargs: Any) -> Any: ...
    def xreadgroup(self, *args: Any, **kwargs: Any) -> Any: ...
    def xack(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class ConsumedMission:
    stream: str
    message_id: str
    envelope: MissionEnvelope
    raw: dict[str, Any]


class RedisMissionBus:
    def __init__(
        self,
        client: RedisLikeClient,
        *,
        stream_name: str = MISSION_STREAM,
        consumer_group: str = MISSION_CONSUMER_GROUP,
        dlq_stream: str = MISSION_DLQ_STREAM,
    ) -> None:
        self._client = client
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.dlq_stream = dlq_stream

    @classmethod
    def from_url(
        cls,
        redis_url: str,
        *,
        stream_name: str = MISSION_STREAM,
        consumer_group: str = MISSION_CONSUMER_GROUP,
        dlq_stream: str = MISSION_DLQ_STREAM,
    ) -> "RedisMissionBus":
        try:
            import redis  # type: ignore
        except Exception as exc:  # pragma: no cover - runtime environment specific
            raise RuntimeError("redis package is required for RedisMissionBus.from_url") from exc

        client = redis.Redis.from_url(redis_url, decode_responses=True)
        return cls(client, stream_name=stream_name, consumer_group=consumer_group, dlq_stream=dlq_stream)

    def ensure_group(self) -> None:
        try:
            self._client.xgroup_create(
                name=self.stream_name,
                groupname=self.consumer_group,
                id="0",
                mkstream=True,
            )
        except Exception as exc:
            # Ignore BUSYGROUP-equivalent failures.
            if "BUSYGROUP" not in str(exc):
                raise

    def publish_mission(self, envelope: MissionEnvelope) -> str:
        payload = envelope.model_dump(mode="json")
        return str(
            self._client.xadd(
                self.stream_name,
                fields={
                    "job_id": payload["job_id"],
                    "idempotency_key": payload["idempotency_key"],
                    "envelope": json.dumps(payload, sort_keys=True),
                },
            )
        )

    def publish_result(self, result: MissionResultEnvelope) -> str:
        payload = result.model_dump(mode="json")
        return str(
            self._client.xadd(
                f"{self.stream_name}:results",
                fields={
                    "job_id": payload["job_id"],
                    "status": payload["status"],
                    "envelope": json.dumps(payload, sort_keys=True),
                },
            )
        )

    def consume(
        self,
        *,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 1000,
        stream_id: str = ">",
    ) -> list[ConsumedMission]:
        rows = self._client.xreadgroup(
            groupname=self.consumer_group,
            consumername=consumer_name,
            streams={self.stream_name: stream_id},
            count=max(1, int(count)),
            block=max(1, int(block_ms)),
        )
        consumed: list[ConsumedMission] = []
        for stream, entries in rows or []:
            stream_name = stream.decode() if isinstance(stream, bytes) else str(stream)
            for message_id, field_map in entries:
                normalized = {
                    (k.decode() if isinstance(k, bytes) else str(k)): (
                        v.decode() if isinstance(v, bytes) else v
                    )
                    for k, v in dict(field_map).items()
                }
                envelope_raw = json.loads(str(normalized.get("envelope") or "{}"))
                envelope = MissionEnvelope.model_validate(envelope_raw)
                consumed.append(
                    ConsumedMission(
                        stream=stream_name,
                        message_id=message_id.decode() if isinstance(message_id, bytes) else str(message_id),
                        envelope=envelope,
                        raw=normalized,
                    )
                )
        return consumed

    def ack(self, message_id: str) -> int:
        return int(self._client.xack(self.stream_name, self.consumer_group, message_id))

    def fail_and_maybe_dlq(
        self,
        *,
        consumed: ConsumedMission,
        failure_error: str,
        retry_count: int,
    ) -> bool:
        max_retries = max(0, int(consumed.envelope.max_retries))
        should_dlq = int(retry_count) >= max_retries
        if should_dlq:
            payload = consumed.envelope.model_dump(mode="json")
            payload["failure_error"] = str(failure_error)
            payload["retry_count"] = int(retry_count)
            self._client.xadd(
                self.dlq_stream,
                fields={
                    "job_id": payload["job_id"],
                    "envelope": json.dumps(payload, sort_keys=True),
                },
            )
            self.ack(consumed.message_id)
        return should_dlq

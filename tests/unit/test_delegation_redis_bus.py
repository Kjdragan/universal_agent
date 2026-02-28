import json

from universal_agent.delegation.redis_bus import RedisMissionBus
from universal_agent.delegation.schema import MissionEnvelope, MissionPayload


class FakeRedis:
    def __init__(self):
        self.streams = {}
        self.groups = set()
        self.acks = []

    def xgroup_create(self, name, groupname, id, mkstream=False):
        key = (name, groupname)
        if key in self.groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.groups.add(key)
        if mkstream:
            self.streams.setdefault(name, [])

    def xadd(self, name, fields):
        seq = len(self.streams.setdefault(name, [])) + 1
        msg_id = f"{seq}-0"
        self.streams[name].append((msg_id, dict(fields)))
        return msg_id

    def xreadgroup(self, groupname, consumername, streams, count=1, block=0):
        stream_name = next(iter(streams.keys()))
        entries = self.streams.get(stream_name, [])[:count]
        return [(stream_name, entries)]

    def xack(self, stream_name, group_name, message_id):
        self.acks.append((stream_name, group_name, message_id))
        return 1


def test_publish_consume_and_dlq_roundtrip():
    fake = FakeRedis()
    bus = RedisMissionBus(fake)
    bus.ensure_group()

    envelope = MissionEnvelope(
        job_id="job-1",
        idempotency_key="idem-1",
        priority=1,
        timeout_seconds=60,
        max_retries=2,
        payload=MissionPayload(task="Build repo", context={"run_path": "abc"}),
    )

    bus.publish_mission(envelope)
    consumed = bus.consume(consumer_name="worker_factory-1", count=1)
    assert len(consumed) == 1
    assert consumed[0].envelope.job_id == "job-1"

    sent_to_dlq = bus.fail_and_maybe_dlq(
        consumed=consumed[0],
        failure_error="boom",
        retry_count=2,
    )
    assert sent_to_dlq is True

    dlq_entries = fake.streams.get(bus.dlq_stream, [])
    assert len(dlq_entries) == 1
    payload = json.loads(dlq_entries[0][1]["envelope"])
    assert payload["job_id"] == "job-1"

    assert fake.acks == [(bus.stream_name, bus.consumer_group, consumed[0].message_id)]

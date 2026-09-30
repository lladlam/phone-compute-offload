"""Scheduler decisions. No sockets."""

import unittest

from pco.agent.scheduler import History, LinkEstimate, Sample, Scheduler
from pco.protocol.messages import Task, WorkerHello


def worker(**overrides) -> WorkerHello:
    base = dict(
        worker_id="w1",
        name="phone",
        architecture="aarch64",
        cpu_count=8,
        load_1m=0.2,
        executors=[{"name": "ffmpeg", "version": "6", "kind": "cpu"}, {"name": "generic", "kind": "cpu"}],
        temperature_c=40.0,
        battery_percent=80.0,
        battery_charging=False,
    )
    base.update(overrides)
    return WorkerHello(**base)


class SchedulerTest(unittest.TestCase):
    def test_tiny_task_stays_local(self):
        # The example from the design note: 2s local, 5s compute + 1s transfer remote.
        task = Task(adapter="generic", estimated_compute_s=2.0, input_bytes=1_000_000, estimated_output_bytes=1_000_000)
        link = LinkEstimate(uplink_bps=1_000_000, downlink_bps=1_000_000, rtt_s=0.0)
        decision = Scheduler(History(), link).decide(task, [worker()])
        self.assertEqual(decision.where, "local")
        self.assertGreater(decision.remote_cost_s, decision.local_cost_s)

    def test_long_task_goes_remote_when_phone_is_fast_enough(self):
        # Local 120s. Phone history says this adapter runs at a speed that
        # finishes the same bytes in 85s, plus a small transfer.
        task = Task(
            adapter="ffmpeg",
            required_executor="ffmpeg",
            estimated_compute_s=120.0,
            input_bytes=8_000_000,
            estimated_output_bytes=4_000_000,
        )
        history = History()
        # 8e6 bytes / 85s
        history.remote["ffmpeg"] = {"w1": [Sample(85.0, 8_000_000)] * 3}
        link = LinkEstimate(uplink_bps=1_000_000, downlink_bps=1_000_000, rtt_s=0.0)
        decision = Scheduler(history, link).decide(task, [worker()])
        self.assertEqual(decision.where, "remote")
        self.assertAlmostEqual(decision.local_cost_s, 120.0)
        # 85 compute + 8 upload + 4 download
        self.assertAlmostEqual(decision.remote_cost_s, 97.0, places=1)

    def test_unmeasured_remote_is_pessimistic(self):
        task = Task(adapter="ffmpeg", required_executor="ffmpeg", estimated_compute_s=10.0, input_bytes=1000, estimated_output_bytes=1000)
        decision = Scheduler(History(), LinkEstimate(rtt_s=0)).decide(task, [worker()])
        self.assertEqual(decision.where, "local")
        self.assertGreater(decision.remote_cost_s, 10.0)

    def test_hot_phone_is_vetoed(self):
        task = Task(adapter="generic", estimated_compute_s=100.0, input_bytes=1, estimated_output_bytes=1)
        history = History()
        history.remote["generic"] = {"w1": [Sample(1.0, 1)] * 5}
        decision = Scheduler(history, LinkEstimate()).decide(task, [worker(temperature_c=90.0)])
        self.assertEqual(decision.where, "local")
        self.assertIn("temperature", decision.reason)

    def test_low_battery_not_charging_is_vetoed(self):
        task = Task(adapter="generic", estimated_compute_s=100.0, input_bytes=1, estimated_output_bytes=1)
        history = History()
        history.remote["generic"] = {"w1": [Sample(1.0, 1)] * 5}
        decision = Scheduler(history, LinkEstimate()).decide(
            task, [worker(battery_percent=5.0, battery_charging=False)]
        )
        self.assertEqual(decision.where, "local")
        self.assertIn("battery", decision.reason)

    def test_low_battery_while_charging_is_allowed(self):
        task = Task(adapter="generic", estimated_compute_s=100.0, input_bytes=1, estimated_output_bytes=1)
        history = History()
        history.remote["generic"] = {"w1": [Sample(1.0, 1)] * 5}
        decision = Scheduler(history, LinkEstimate()).decide(
            task, [worker(battery_percent=5.0, battery_charging=True)]
        )
        self.assertEqual(decision.where, "remote")

    def test_missing_executor_stays_local(self):
        task = Task(adapter="ffmpeg", required_executor="ffmpeg", estimated_compute_s=100, input_bytes=1, estimated_output_bytes=1)
        history = History()
        history.remote["ffmpeg"] = {"w1": [Sample(1.0, 1)] * 5}
        bare = worker(executors=[{"name": "generic", "kind": "cpu"}])
        decision = Scheduler(history, LinkEstimate()).decide(task, [bare])
        self.assertEqual(decision.where, "local")
        self.assertIn("missing executor", decision.reason)

    def test_cuda_is_not_sent_to_a_cpu_phone(self):
        task = Task(adapter="generic", required_kind="gpu", required_api="cuda", estimated_compute_s=100, input_bytes=1, estimated_output_bytes=1)
        decision = Scheduler(History(), LinkEstimate()).decide(task, [worker()], force="remote")
        self.assertEqual(decision.where, "local")
        self.assertIn("no capable", decision.reason)

    def test_blacklist_after_three_failures(self):
        task = Task(adapter="generic", estimated_compute_s=100, input_bytes=1, estimated_output_bytes=1)
        history = History()
        history.remote["generic"] = {"w1": [Sample(1.0, 1)] * 5}
        scheduler = Scheduler(history, LinkEstimate())
        for _ in range(3):
            scheduler.note_failure("w1")
        decision = scheduler.decide(task, [worker()])
        self.assertEqual(decision.where, "local")
        self.assertIn("blacklisted", decision.reason)

    def test_force_local_ignores_a_faster_phone(self):
        task = Task(adapter="generic", estimated_compute_s=100, input_bytes=1, estimated_output_bytes=1)
        history = History()
        history.remote["generic"] = {"w1": [Sample(1.0, 1)] * 5}
        decision = Scheduler(history, LinkEstimate()).decide(task, [worker()], force="local")
        self.assertEqual(decision.where, "local")
        self.assertEqual(decision.reason, "forced local")


if __name__ == "__main__":
    unittest.main()

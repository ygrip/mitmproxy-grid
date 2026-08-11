import unittest

from worker_registry import WorkerRegistry


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class WorkerRegistryTest(unittest.TestCase):
    def worker(self, worker_id, slots, instances=0):
        return {
            "workerId": worker_id,
            "apiUrl": f"http://{worker_id}:8090",
            "proxyHost": worker_id,
            "availableSlots": slots,
            "instances": instances,
            "gridVersion": "2.1.0",
            "apiVersion": "2",
        }

    def test_select_worker_prefers_most_available_capacity(self):
        registry = WorkerRegistry()
        registry.register(self.worker("worker-a", 2))
        registry.register(self.worker("worker-b", 8))

        self.assertEqual("worker-b", registry.select_worker().worker_id)

    def test_remember_and_forget_adjusts_local_capacity(self):
        registry = WorkerRegistry()
        registry.register(self.worker("worker-a", 3))

        registry.remember_instance("instance-1", "worker-a")
        self.assertEqual(2, registry.worker("worker-a").available_slots)
        self.assertEqual("worker-a", registry.owner("instance-1").worker_id)

        registry.forget_instance("instance-1")
        self.assertEqual(3, registry.worker("worker-a").available_slots)
        self.assertIsNone(registry.owner("instance-1"))

    def test_stale_worker_is_removed_with_owned_instances(self):
        clock = FakeClock()
        registry = WorkerRegistry(stale_after=10, clock=clock)
        registry.register(self.worker("worker-a", 3))
        registry.remember_instance("instance-1", "worker-a")

        clock.value = 11
        self.assertEqual(["worker-a"], registry.prune())
        self.assertIsNone(registry.owner("instance-1"))

    def test_discover_owner_recovers_mapping_after_coordinator_restart(self):
        registry = WorkerRegistry()
        registry.register(self.worker("worker-a", 3))
        worker_b = registry.register(self.worker("worker-b", 3))

        def fetch_instances(worker):
            if worker.worker_id == worker_b.worker_id:
                return [{"instanceId": "instance-42"}]
            return []

        owner = registry.discover_owner("instance-42", fetch_instances)

        self.assertEqual("worker-b", owner.worker_id)
        self.assertEqual("worker-b", registry.owner("instance-42").worker_id)


if __name__ == "__main__":
    unittest.main()

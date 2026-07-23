from lkp_indexer.watcher_service import ProcessCpuMonitor, select_watch_mode


class Clock:
    def __init__(self) -> None:
        self.wall = 0.0
        self.cpu = 0.0

    def monotonic(self) -> float:
        return self.wall

    def process_time(self) -> float:
        return self.cpu

    def advance(self, *, wall: float, cpu: float) -> None:
        self.wall += wall
        self.cpu += cpu


def test_cpu_monitor_ignores_startup_grace_and_requires_consecutive_samples():
    clock = Clock()
    monitor = ProcessCpuMonitor(
        warning_percent=50,
        warning_samples=3,
        grace_seconds=20,
        monotonic=clock.monotonic,
        process_time=clock.process_time,
    )

    clock.advance(wall=10, cpu=9)
    grace_sample = monitor.sample()
    assert grace_sample.in_grace is True
    assert grace_sample.consecutive_high == 0

    clock.advance(wall=10, cpu=9)
    first = monitor.sample()
    clock.advance(wall=10, cpu=9)
    second = monitor.sample()
    clock.advance(wall=10, cpu=9)
    third = monitor.sample()

    assert first.alert is False
    assert second.alert is False
    assert third.alert is True
    assert third.consecutive_high == 3


def test_cpu_monitor_clears_alert_after_a_normal_sample():
    clock = Clock()
    monitor = ProcessCpuMonitor(
        warning_percent=50,
        warning_samples=2,
        grace_seconds=0,
        monotonic=clock.monotonic,
        process_time=clock.process_time,
    )

    clock.advance(wall=10, cpu=8)
    monitor.sample()
    clock.advance(wall=10, cpu=8)
    assert monitor.sample().alert is True
    clock.advance(wall=10, cpu=1)
    recovered = monitor.sample()

    assert recovered.alert is False
    assert recovered.consecutive_high == 0


def test_watch_mode_is_selected_per_root():
    polling_roots = {"/data/vault"}

    assert (
        select_watch_mode(
            "/home/user/src",
            force_polling=False,
            polling_roots=polling_roots,
        )
        == "native"
    )
    assert (
        select_watch_mode(
            "/data/vault",
            force_polling=False,
            polling_roots=polling_roots,
        )
        == "polling"
    )
    assert (
        select_watch_mode(
            "/home/user/src",
            force_polling=True,
            polling_roots=polling_roots,
        )
        == "polling"
    )

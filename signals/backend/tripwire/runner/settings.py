"""Runner settings, read from env vars, with the check that keeps one judge call inside one lease."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    threads: int = 4
    lease_seconds: int = 180
    max_attempts: int = 3
    max_backoff_seconds: int = 300
    give_up_hours: int = 24
    fetch_timeout_seconds: int = 10       # each Laminar query inside judge()
    model_timeout_seconds: int = 60       # each model call inside judge()
    idle_seconds: float = 2.0             # wait when there's nothing to claim
    sweep_seconds: float = 30.0

    @classmethod
    def from_env(cls):
        env = os.environ.get
        return cls(
            threads=int(env("RUNNER_THREADS", cls.threads)),
            lease_seconds=int(env("RUNNER_LEASE_SECONDS", cls.lease_seconds)),
            max_attempts=int(env("RUNNER_MAX_ATTEMPTS", cls.max_attempts)),
            max_backoff_seconds=int(env("RUNNER_MAX_BACKOFF_SECONDS", cls.max_backoff_seconds)),
            give_up_hours=int(env("RUNNER_GIVE_UP_HOURS", cls.give_up_hours)),
            fetch_timeout_seconds=int(env("JUDGE_FETCH_TIMEOUT_SECONDS", cls.fetch_timeout_seconds)),
            model_timeout_seconds=int(env("JUDGE_MODEL_TIMEOUT_SECONDS", cls.model_timeout_seconds)),
        )

    def check(self):
        """A judge call can take two Laminar queries, a model call and one repair. If that can
        outlast the lease, another thread would claim the run and it would be judged twice."""
        if min(self.fetch_timeout_seconds, self.model_timeout_seconds) <= 0:
            raise ValueError("Judge fetch and model timeouts must be positive")
        needed = 2 * self.fetch_timeout_seconds + 2 * self.model_timeout_seconds + 30
        if self.lease_seconds < needed:
            raise ValueError(f"RUNNER_LEASE_SECONDS is {self.lease_seconds}; the judge's time limits "
                             f"need at least {needed}")

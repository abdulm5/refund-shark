import dotenv

# Tests must never read the developer's .env, which may hold a real API key. Patch before any module
# that calls load_dotenv (the CLI, the API) is imported.
dotenv.load_dotenv = lambda *args, **kwargs: False

import pytest

from shark.pipeline import load_scenarios


def scenario(id):
    return next(s for s in load_scenarios() if s.id == id).model_copy(deep=True)


def logs(case):
    """The customer request-log record that supplies counted Downtime Minutes."""
    return next(i for i in case.incidents if i.evidence_kind == "customer_metrics")


@pytest.fixture
def case():
    return scenario("jira-sept-claim")


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    """Keep the suite offline and free regardless of a developer's .env: no status polling, no API key."""
    monkeypatch.setenv("SHARK_WATCH_INTERVAL", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

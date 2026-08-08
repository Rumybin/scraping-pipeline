"""Tests for the `python -m pipeline run --site <id>` CLI entry point."""

import pytest

import pipeline.__main__ as cli
from pipeline.core.exceptions import ConfigurationError
from pipeline.orchestrator.run import RunResult
from pipeline.quality.dq_engine import STRICT_PROFILE, evaluate


def _result(gate_status: str) -> RunResult:
    report = evaluate([], run_id="run-1", site_id="books_sandbox", profile=STRICT_PROFILE)
    report = report.model_copy(update={"gate_status": gate_status})
    return RunResult(
        run_id="run-1",
        site_id="books_sandbox",
        record_count=1000,
        quarantined_count=0,
        gate_status=gate_status,  # type: ignore[arg-type]  # test passes a plain str for the Literal
        dq_report=report,
    )


async def _fake_run_site_pass(site_id: str) -> RunResult:
    return _result("pass")


async def _fake_run_site_fail(site_id: str) -> RunResult:
    return _result("fail")


async def _fake_run_site_raises(site_id: str) -> RunResult:
    raise ConfigurationError(f"site {site_id!r} is not registered in sites.yaml")


def test_main_returns_zero_and_prints_summary_on_passing_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "run_site", _fake_run_site_pass)

    exit_code = cli.main(["run", "--site", "books_sandbox"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "1000 records" in out
    assert "gate=pass" in out


def test_main_returns_one_when_the_dq_gate_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_site", _fake_run_site_fail)

    exit_code = cli.main(["run", "--site", "books_sandbox"])

    assert exit_code == 1


def test_main_returns_one_and_prints_to_stderr_on_configuration_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "run_site", _fake_run_site_raises)

    exit_code = cli.main(["run", "--site", "does_not_exist"])

    assert exit_code == 1
    assert "run failed" in capsys.readouterr().err


def test_main_requires_a_site_argument() -> None:
    with pytest.raises(SystemExit):
        cli.main(["run"])

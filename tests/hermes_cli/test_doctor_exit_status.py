"""Regression for #117276: ``hermes doctor``'s exit status must agree with its unresolved findings."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("issues,manual,fixed,fix,expected", [
    (["needs repair"], [], 0, False, 1),
    ([], ["manual repair"], 0, False, 1),
    ([], [], 0, False, 0),
    ([], [], 1, True, 0),
    ([], ["remaining repair"], 1, True, 1),
])
def test_doctor_command_reports_remaining_findings(monkeypatch, capsys, issues, manual, fixed, fix, expected):
    import hermes_cli.doctor as doctor
    from hermes_cli.main import cmd_doctor
    from hermes_cli.doctor_report import Finding

    def check(should_fix):
        assert should_fix is fix
        return Finding(issues=issues, manual_issues=manual, fixed=fixed)

    monkeypatch.setattr(doctor, "DOCTOR_CHECKS", ((None, check),))
    result = cmd_doctor(SimpleNamespace(fix=fix, ack=None, live=False))
    output = capsys.readouterr().out
    assert result == expected
    for issue in issues + manual:
        assert issue in output


@pytest.mark.parametrize("unresolved", [False, True])
def test_doctor_cli_process_status_matches_summary(unresolved):
    import subprocess
    import sys
    from pathlib import Path

    program = f"""
import sys
import hermes_cli.doctor as doctor
from hermes_cli.doctor_report import Finding
from hermes_cli.main import main
issues = ['fixture unresolved problem'] if {unresolved!r} else []
doctor.DOCTOR_CHECKS = ((None, lambda fix: Finding(issues=issues)),)
sys.argv = ['hermes', 'doctor']
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == int(unresolved), result.stdout + result.stderr
    assert ("fixture unresolved problem" if unresolved else "All checks passed") in result.stdout


def test_doctor_raising_check_counts_as_unresolved(monkeypatch, capsys):
    """Regression for #132335: a decorated check that raises must fail the run, not print
    "All checks passed!" with exit 0 — even when it recorded nothing before crashing."""
    import hermes_cli.doctor as doctor
    from hermes_cli.doctor_report import doctor_check
    from hermes_cli.main import cmd_doctor

    @doctor_check("boom check failed: {e}")
    def boom(should_fix, f):
        raise RuntimeError("kaput")

    monkeypatch.setattr(doctor, "DOCTOR_CHECKS", ((None, boom),))
    result = cmd_doctor(SimpleNamespace(fix=False, ack=None, live=False))
    output = capsys.readouterr().out
    assert result == 1
    assert "All checks passed" not in output
    assert "did not complete" in output


def test_doctor_raising_check_keeps_partial_findings(monkeypatch, capsys):
    """Issues recorded before the crash survive alongside the crash finding."""
    import hermes_cli.doctor as doctor
    from hermes_cli.doctor_report import doctor_check
    from hermes_cli.main import cmd_doctor

    @doctor_check("partial check failed: {e}")
    def partial(should_fix, f):
        f.issues.append("recorded before the crash")
        raise RuntimeError("kaput")

    monkeypatch.setattr(doctor, "DOCTOR_CHECKS", ((None, partial),))
    result = cmd_doctor(SimpleNamespace(fix=False, ack=None, live=False))
    output = capsys.readouterr().out
    assert result == 1
    assert "recorded before the crash" in output
    assert "did not complete" in output

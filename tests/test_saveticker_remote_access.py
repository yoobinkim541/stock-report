import ipaddress
import subprocess

import pytest

from reports import saveticker_remote_access as access


def result(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, "")


def healthy_probe(kind, host, port):
    if kind == "cdp":
        return 200
    if kind == "vnc":
        return b"RFB 003.008\n"
    if kind == "http":
        return 200
    raise AssertionError(kind)


def test_reuses_healthy_services_without_starting_units():
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[:3] == ["tailscale", "ip", "-4"]:
            return result("100.101.2.3\n")
        return result("active\n")

    url = access.ensure_remote_access(run_command=run, probe=healthy_probe)

    assert url == "http://100.101.2.3:6081/vnc.html?autoconnect=true&resize=scale"
    assert ipaddress.ip_address(url.split("//", 1)[1].split(":", 1)[0]) in ipaddress.ip_network("100.64.0.0/10")
    assert not any("systemd-run" in argv for argv in calls)
    assert {tuple(call[-1:]) for call in calls if "is-active" in call} == {
        ("hermes-source-display",), ("hermes-source-browser",),
    }


def test_starts_only_missing_remote_units_with_two_hour_runtime():
    started = []

    def run(argv):
        if argv[:3] == ["tailscale", "ip", "-4"]:
            return result("100.70.1.9\n")
        if "is-active" in argv:
            return result("inactive\n", 3) if argv[-1] == "hermes-source-novnc-private" else result("active\n")
        if argv[0] == "systemd-run":
            started.append(argv)
            return result()
        raise AssertionError(argv)

    def missing_then_healthy(kind, host, port):
        if kind == "http" and not started:
            return 503
        return healthy_probe(kind, host, port)

    url = access.ensure_remote_access(run_command=run, probe=missing_then_healthy, wait=lambda _: None)
    assert url == "http://100.70.1.9:6081/vnc.html?autoconnect=true&resize=scale"
    assert len(started) == 1
    assert "--property=RuntimeMaxSec=2h" in started[0]
    assert any(arg.startswith("--unit=hermes-source-novnc-private-recovery-") for arg in started[0])
    assert started[0][-2:] == ["100.70.1.9:6081", "127.0.0.1:5905"]


def test_recovery_units_use_requested_remaining_ttl():
    started = []

    def run(argv):
        if argv[:3] == ["tailscale", "ip", "-4"]:
            return result("100.71.1.9")
        if "is-active" in argv:
            return result("inactive", 3) if argv[-1] == "hermes-source-novnc-private" else result("active")
        if argv[0] == "systemd-run":
            started.append(argv)
            return result()
        raise AssertionError(argv)

    def http_missing_until_started(kind, host, port):
        return 503 if kind == "http" and not started else healthy_probe(kind, host, port)

    access.ensure_remote_access(60, run_command=run, probe=http_missing_until_started, wait=lambda _: None)
    assert len(started) == 1
    assert "--property=RuntimeMaxSec=60s" in started[0]


@pytest.mark.parametrize("ttl", [0, -1, 7201, True, 60.0, "60", None])
def test_invalid_ttl_is_rejected_before_any_operation(ttl):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid TTL must be rejected before commands or probes")

    with pytest.raises(ValueError, match="max_seconds"):
        access.ensure_remote_access(ttl, run_command=forbidden, probe=forbidden)


@pytest.mark.parametrize("address", ["8.8.8.8", "2001:db8::1", "100.128.0.1", "not-an-ip"])
def test_rejects_non_tailscale_ipv4(address):
    def run(argv):
        if argv[:3] == ["tailscale", "ip", "-4"]:
            return result(address)
        raise AssertionError("must validate address before service commands")

    with pytest.raises(RuntimeError, match="Tailscale IPv4"):
        access.ensure_remote_access(run_command=run, probe=healthy_probe)


@pytest.mark.parametrize("missing", ["hermes-source-display", "hermes-source-browser"])
def test_fails_clearly_when_required_display_or_browser_is_missing(missing):
    def run(argv):
        if argv[:3] == ["tailscale", "ip", "-4"]:
            return result("100.80.1.1")
        if "is-active" in argv and argv[-1] == missing:
            return result("inactive", 3)
        if "is-active" in argv:
            return result("active")
        raise AssertionError("must not attempt broad process recovery")

    with pytest.raises(RuntimeError, match=missing):
        access.ensure_remote_access(run_command=run, probe=healthy_probe)


def test_remote_units_bind_only_tailscale_ipv4_and_vnc_disables_ipv6():
    commands = []

    def run(argv):
        if argv[:3] == ["tailscale", "ip", "-4"]:
            return result("100.90.4.2")
        if "is-active" in argv and argv[-1] in {"hermes-source-vnc-local", "hermes-source-novnc-private"}:
            return result("inactive", 3)
        if "is-active" in argv:
            return result("active")
        if argv[0] == "systemd-run":
            commands.append(argv)
            return result()
        raise AssertionError(argv)

    def missing_then_healthy(kind, host, port):
        if kind == "vnc" and not commands:
            return b""
        if kind == "http" and not any("websockify" in arg for arg in commands[-1]):
            return 503
        return healthy_probe(kind, host, port)

    access.ensure_remote_access(run_command=run, probe=missing_then_healthy, wait=lambda _: None)
    all_args = " ".join(" ".join(command) for command in commands)
    assert "-no6" in all_args and "-noipv6" in all_args
    assert "100.90.4.2:6081" in all_args
    assert any(arg.startswith("--unit=hermes-source-vnc-local-recovery-") for arg in commands[0])
    novnc = next(command for command in commands if any("websockify" in arg for arg in command))
    assert novnc[-2:] == ["100.90.4.2:6081", "127.0.0.1:5905"]
    assert "0.0.0.0" not in all_args and "[::]" not in all_args


def test_bad_health_probes_fail_instead_of_returning_unhealthy_url():
    def broken_probe(kind, host, port):
        return 503 if kind == "http" else b"bad" if kind == "vnc" else 200

    def run(argv):
        return result("100.90.4.3") if argv[:3] == ["tailscale", "ip", "-4"] else result("active")

    with pytest.raises(RuntimeError, match="noVNC"):
        access.ensure_remote_access(run_command=run, probe=broken_probe, wait=lambda _: None)

"""Structured environment audit for SonicFX Ultimate (stdlib-only).

Produces a typed, JSON-serialisable report of everything the engine depends on:
OS, PipeWire/WirePlumber services and versions, audio graph devices, quantum,
hosted-DSP plugin availability, and the Python venv state.

Run as CLI: ``python -m sonicfx.diagnostics.environment [--json PATH]``
Exit code: 0 = all critical checks OK, 1 = at least one critical failure.
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OK, WARN, MISSING, FAIL = "ok", "warn", "missing", "fail"
_STATUS_RANK = {OK: 0, WARN: 1, MISSING: 2, FAIL: 3}

#: Fully-formed filenames required inside the standard LADSPA/LV2 search dirs.
#: LV2 plugins live inside ``<name>.lv2/`` bundles; the matcher handles both
#: bare files and bundle-relative matches.
_PLUGIN_PROBES: dict[str, list[str]] = {
    # widening / spatialiser (swh-plugins, LADSPA)
    "swh.plugins": ["matrix_spatialiser_1422.so"],
    # dynamics / reverb sets
    "calf.lv2": ["calf.so", "Compressor.ttl", "Reverb.ttl", "MultiBandCompressor.ttl"],
    "zam.ladspa": ["ZaMaximX2-ladspa.so", "ZamCompX2-ladspa.so"],
    "zam.lv2": ["ZaMaximX2_dsp.so", "ZamCompX2_dsp.so"],
    "swh.limiter": ["fast_lookahead_limiter_1913.so"],
    # headphone crossfeed
    "bs2b.ladspa": ["bs2b.so"],
    # ambisonics helpers (optional)
    "amb.ladspa": ["ambisonic1.so", "ambisonic2.so", "ambisonic3.so"],
}

_PLUGIN_DIRS = [
    Path("/usr/lib/x86_64-linux-gnu/ladspa"),
    Path("/usr/lib/ladspa"),
    Path("/usr/lib/x86_64-linux-gnu/lv2"),
    Path("/usr/lib/lv2"),
    Path.home() / ".lv2",
]


@dataclass
class Check:
    name: str
    status: str  # ok | warn | missing | fail
    detail: str = ""
    data: Any = None

    @property
    def critical(self) -> bool:
        return self.name in _CRITICAL_CHECKS


_CRITICAL_CHECKS = frozenset(
    {
        "os.linux",
        "service.pipewire",
        "service.pipewire_pulse",
        "service.wireplumber",
        "tool.pw_cli",
        "tool.wpctl",
        "tool.pw_dump",
    }
)


def _which(name: str) -> Path | None:
    p = shutil.which(name)
    return Path(p) if p else None


def _run(cmd: list[str], timeout: float = 15.0) -> tuple[int, str]:
    """Run a command, never raising; returns (returncode, combined output)."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out.strip()


def _first_line(text: str) -> str:
    return text.splitlines()[0].strip() if text else ""


def _check_services() -> dict[str, Check]:
    out: dict[str, Check] = {}
    for svc in ("pipewire", "pipewire-pulse", "wireplumber"):
        code, text = _run(["systemctl", "--user", "is-active", svc])
        state = _first_line(text) or ("unknown" if code != 0 else "unknown")
        active = state == "active"
        out[f"service.{svc.replace('-', '_')}"] = Check(
            name=f"service.{svc.replace('-', '_')}",
            status=OK if active else FAIL,
            detail=f"systemctl --user is-active -> {state}",
            data=state,
        )
    return out


def _check_versions() -> dict[str, Check]:
    out: dict[str, Check] = {}

    def _ver(tool: str, key: str) -> None:
        path = _which(tool)
        if not path:
            out[key] = Check(key, MISSING, f"{tool} not found in PATH")
            return
        code, text = _run([tool, "--version"])
        detail = _first_line(text)
        # e.g. "Compiled with libpipewire 1.6.2" / "libwireplumber 0.5.13"
        m = re.search(r"lib(pipewire|wireplumber)\s+(\S+)", text)
        if m:
            detail = f"{tool} {m.group(2)}"
        out[key] = Check(key, OK if code == 0 else FAIL, detail or text[:80])

    _ver("pipewire", "version.pipewire")
    _ver("wireplumber", "version.wireplumber")
    return out


def _check_tools() -> dict[str, Check]:
    tools = ["pw-cli", "wpctl", "pw-dump", "pw-top", "pw-link", "pw-loopback", "pw-metadata",
             "pactl", "gcc", "cmake", "make"]
    out: dict[str, Check] = {}
    for t in tools:
        path = _which(t)
        out[f"tool.{t.replace('-', '_')}"] = Check(
            name=f"tool.{t.replace('-', '_')}",
            status=(OK if path else MISSING),
            detail=str(path) if path else "not in PATH",
            data=str(path) if path else None,
        )
    return out


def _check_plugins() -> dict[str, Check]:
    out: dict[str, Check] = {}
    found_bundles: set[str] = set()
    files_by_dir: dict[str, set[str]] = {}
    for d in _PLUGIN_DIRS:
        if d.is_dir():
            files_by_dir[str(d)] = {p.name for p in d.iterdir()}
            files_by_dir[str(d)].update(
                f"{b.name}/{f}" for b in d.glob("*.lv2") for f in b.iterdir()
            )
    for key, needed in _PLUGIN_PROBES.items():
        hits: list[str] = []
        for probe in needed:
            for d, files in files_by_dir.items():
                if probe in files:
                    hits.append(f"{d}/{probe}")
                else:
                    hits.extend(
                        f"{d}/{entry}" for entry in files
                        if entry.endswith("/" + probe)
                    )
        found_bundles.update(hits)
        if hits:
            labels = sorted("/".join(Path(h).parts[-2:]) for h in hits)
            out[f"plugin.{key}"] = Check(
                f"plugin.{key}", OK, "found: " + ", ".join(labels)[:80])
        else:
            out[f"plugin.{key}"] = Check(
                f"plugin.{key}", MISSING,
                f"none of {', '.join(needed)} found in "
                f"{', '.join(str(d) for d in _PLUGIN_DIRS)}")
    out["plugin.bundles"] = Check("plugin.bundles", OK, "inventory", data=sorted(found_bundles))
    return out


def _filter_chain_examples() -> Check:
    p = Path("/usr/share/pipewire/filter-chain")
    if not p.is_dir():
        return Check("pipewire.filter_chain_examples", MISSING,
                     "no /usr/share/pipewire/filter-chain")
    confs = sorted(c.name for c in p.glob("*.conf"))
    return Check(
        "pipewire.filter_chain_examples", OK,
        f"{len(confs)} stock filter-chain configs", data=confs)


def _audio_graph() -> dict[str, Check]:
    out: dict[str, Check] = {}
    code, text = _run(["pw-dump", "--version"], timeout=10)
    if code != 0:
        out["graph.pw_dump"] = Check("graph.pw_dump", FAIL, "pw-dump failed")
        return out
    code, text = _run(["pw-dump"])
    if code != 0 or not text:
        out["graph.pw_dump"] = Check("graph.pw_dump", FAIL, "pw-dump produced no data")
        return out
    try:
        objs = json.loads(text)
    except json.JSONDecodeError as exc:
        out["graph.pw_dump"] = Check("graph.pw_dump", FAIL, f"JSON parse: {exc}")
        return out

    sinks, sources = [], []
    for obj in objs:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        info = obj.get("info") or {}
        props = info.get("props") or {}
        media_class = props.get("media.class", "")
        if not media_class.startswith("Audio"):
            continue
        entry = {
            "node-id": obj["id"],
            "name": props.get("node.name") or _first_line(str(info.get("type"))),
            "media.class": media_class,
            "channels": props.get("audio.channels"),
            "position": props.get("audio.position"),
            "state": info.get("state") or "unset",
        }
        if "Sink" in media_class and "Monitor" not in str(entry["name"]):
            sinks.append(entry)
        elif "Source" in media_class and "Monitor" not in str(entry["name"]):
            sources.append(entry)

    out["graph.sinks"] = Check(
        "graph.sinks", OK if sinks else FAIL, f"{len(sinks)} sinks", data=sinks)
    out["graph.sources"] = Check(
        "graph.sources", OK if sources else WARN, f"{len(sources)} sources", data=sources)
    return out


def _quantum_settings() -> Check:
    code, text = _run(["pw-metadata", "-n", "settings"], timeout=10)
    data: dict[str, str] = {}
    if code == 0:
        for line in text.splitlines():
            m = re.match(r"update:.*id:\d+\s+key:'([^']+)'\s+value:'([^']*)'", line)
            if m:
                data[m.group(1)] = m.group(2)
    quantum = data.get("clock.quantum")
    return Check(
        "graph.settings",
        OK if quantum else WARN,
        f"clock.quantum={quantum} clock.rate={data.get('clock.rate', '?')}",
        data=data,
    )


def _python_env() -> dict[str, Check]:
    out: dict[str, Check] = {}
    out["python.version"] = Check(
        "python.version", OK,
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        data=sys.version,
    )
    try:
        import numpy  # noqa: F401
        out["python.numpy"] = Check("python.numpy", OK, numpy.__version__)  # type: ignore[attr-defined]
    except Exception as exc:  # ImportError or broken build
        out["python.numpy"] = Check("python.numpy", MISSING, str(exc)[:120])
    try:
        import scipy  # noqa: F401
        out["python.scipy"] = Check("python.scipy", OK, scipy.__version__)  # type: ignore[attr-defined]
    except Exception as exc:
        out["python.scipy"] = Check("python.scipy", MISSING, str(exc)[:120])
    try:
        import PySide6  # noqa: F401
        out["python.pyside6"] = Check("python.pyside6", OK, PySide6.__version__)
    except Exception as exc:
        out["python.pyside6"] = Check("python.pyside6", MISSING, str(exc)[:120])
    return out


def _os_release() -> dict[str, Any]:
    result: dict[str, str] = {}
    p = Path("/etc/os-release")
    if p.is_file():
        for line in p.read_text().splitlines():
            m = re.match(r'([A-Z_]+)="?([^"\n]*)"?', line)
            if m:
                result[m.group(1)] = m.group(2)
    return result


def run_audit() -> dict[str, Any]:
    """Full audit. Returns a JSON-ready dict; raises nothing."""
    checks: dict[str, Check] = {}

    os_rel = _os_release()
    checks["os.linux"] = Check(
        "os.linux",
        OK if os_rel.get("ID", "").lower() in {"ubuntu", "linuxmint", "debian"} else WARN,
        f"{os_rel.get('PRETTY_NAME', '?')} (ID={os_rel.get('ID', '?')}) "
        f"on {platform.machine()}",
        data=os_rel,
    )
    checks["kernel"] = Check("kernel", OK, platform.release())
    checks["session.desktop"] = Check(
        "session.desktop",
        WARN if not (shutil.which("pipewire") and sys.platform == "linux") else OK,
        f"XDG_SESSION_TYPE={os_rel.get('SESSION_TYPE', '')}; "
        f"DESKTOP={shutil.which('gnome-shell') is not None and 'GNOME detected' or 'default'}",
    )

    for section in (
        _check_services(),
        _check_versions(),
        _check_tools(),
        _check_plugins(),
        _audio_graph(),
        _python_env(),
    ):
        checks.update(section)

    checks["pipewire.filter_chain_examples"] = _filter_chain_examples()
    checks["graph.settings"] = _quantum_settings()

    critical_fail = any(c.critical and _STATUS_RANK[c.status] >= 2 for c in checks.values())
    return {
        "generated": str(Path(__file__)),
        "python": sys.version.split(),
        "os": os_rel,
        "kernel": platform.release(),
        "machine": platform.machine(),
        "checks": {k: {"status": v.status, "detail": v.detail, "data": v.data}
                   for k, v in checks.items() if k != "plugin.bundles"},
        "plugin_inventory": [
            {"status": v.status, "detail": v.detail, "data": v.data}
            for k, v in checks.items() if k == "plugin.bundles"
        ],
        "critical_status": "fail" if critical_fail else "ok",
        "engine_host_ok": not critical_fail,
    }


def render_report(report: dict[str, Any]) -> str:
    """Human-readable multi-line summary."""
    lines = ["SonicFX Ultimate — environment audit", "=" * 50]
    osr = report.get("os") or {}
    lines.append(f"OS      : {osr.get('PRETTY_NAME', '?')} ({report.get('machine')})")
    lines.append(f"Kernel  : {report.get('kernel')}")
    lines.append("")
    for k, v in report["checks"].items():
        mark = {"ok": "[ OK ]", "warn": "[WARN]", "missing": "[MISS]", "fail": "[FAIL]"}.get(
            v["status"], "[ ??? ]")
        data = ""
        if v.get("data") is not None and not isinstance(v["data"], (dict, list)):
            data = f" — {v['data']}"
        lines.append(f"{mark} {k:38s} {v['detail']}{data}")
    lines.append("")
    lines.append(f"Critical status: {report['critical_status'].upper()}")
    lines.append(
        "Real-time DSP hosting readiness: "
        + ("READY" if report["engine_host_ok"]
           else "NOT READY — see [FAIL]/[MISS] critical rows")
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Audit the SonicFX runtime environment")
    ap.add_argument("--json", metavar="PATH", help="also write the report as JSON to PATH")
    args = ap.parse_args(argv)

    report = run_audit()
    print(render_report(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nJSON report saved to: {args.json}")
    return 0 if report["critical_status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())

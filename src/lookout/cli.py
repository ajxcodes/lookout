"""Lookout unified command line interface for web service and hardware diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import os

import uvicorn

from lookout.adapters.benchmark import BenchmarkEngine
from lookout.config.schema import LookoutConfig


def run_benchmark() -> None:
    """Run hardware diagnostic benchmark and print human-readable summary table."""
    print("=" * 60)
    print(" Lookout Hardware Diagnostics & Edge Sizing Benchmark")
    print("=" * 60)

    async def _run() -> None:
        engine = BenchmarkEngine(config=LookoutConfig())
        report = await engine.run_quick_benchmark()

        hw = report.hardware
        print(f" OS / Platform:         {hw.get('os')} ({hw.get('cpu_arch')})")
        print(f" CPU Cores:             {hw.get('cpu_count')}")
        print(f" System Memory:         {hw.get('memory_mb')} MB")
        print(f" HW Acceleration:       {hw.get('detected_hwaccel', 'none').upper()}")
        print("-" * 60)
        print(f" Transcode FPS:         {report.transcode_fps:.1f} fps")
        print(f" Edge ML Latency:       {report.inference_latency_ms:.2f} ms")
        print(f" SQLite Write IOPS:     {report.sqlite_iops:.0f} iops")
        print(f" Recommended Cameras:   {report.recommended_max_cameras} continuous feeds")
        print("-" * 60)
        print(" Sizing Advice:")
        for rec in report.sizing_recommendations:
            print(f"  • {rec}")
        print("=" * 60)

    asyncio.run(_run())


def run_serve(host: str | None = None, port: int | None = None) -> None:
    """Start Lookout FastAPI web service with uvicorn factory."""
    resolved_host = host or os.environ.get("LOOKOUT_HOST", "0.0.0.0")
    resolved_port = port or int(os.environ.get("LOOKOUT_PORT", "8000"))
    uvicorn.run(
        "lookout.adapters.web.app:create_app",
        factory=True,
        host=resolved_host,
        port=resolved_port,
    )


def main() -> None:
    """CLI entrypoint parsing subcommands."""
    parser = argparse.ArgumentParser(
        prog="lookout",
        description="Lookout: Smart Home Camera Intelligence & Security Appliance",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Start Lookout web server")
    serve_parser.add_argument("--host", default=None, help="Bind host (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=None, help="Bind port (default: 8000)")

    # benchmark command
    subparsers.add_parser("benchmark", help="Run hardware diagnostics and edge sizing benchmark")

    args = parser.parse_args()

    if args.command == "benchmark":
        run_benchmark()
    else:
        # Default action is serve
        run_serve(
            host=getattr(args, "host", None),
            port=getattr(args, "port", None),
        )


if __name__ == "__main__":
    main()

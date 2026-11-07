"""Run the CQA API and built frontend from any working directory."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Reload the Python API during development")
    parser.add_argument("--build", action="store_true", help="Build the frontend before starting")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    if args.build:
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            parser.exit(1, "Node.js/npm is required to build the frontend.\n")
        result = subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=False)
        if result.returncode:
            parser.exit(result.returncode, "Frontend build failed. Run npm ci in frontend first.\n")
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        parser.exit(1, "Frontend is not built. Run npm ci in frontend, then this script with --build.\n")
    command = [
        sys.executable, "-m", "uvicorn", "backend.cqa.main:app",
        "--app-dir", str(ROOT), "--host", args.host, "--port", str(args.port),
    ]
    if args.reload:
        command += ["--reload", "--reload-dir", str(ROOT / "backend")]
    print(f"CQA: http://{args.host}:{args.port}", flush=True)
    try:
        raise SystemExit(subprocess.call(command, cwd=ROOT))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""Build lambda.zip for AWS Lambda (Python 3.12, x86_64) without Docker.

Run from backend/:  ../.venv/bin/python build_lambda.py
Output: ../build/lambda.zip   (handler: lambda_handler.handler)
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
PKG = BUILD / "pkg"
SKIP = ("statsmodels", "uvicorn")  # only needed to fit the model / run locally, not to serve results

shutil.rmtree(BUILD, ignore_errors=True)
PKG.mkdir(parents=True)

reqs = [l for l in (ROOT / "backend" / "requirements.txt").read_text().splitlines() if l and not l.startswith(SKIP)]
(BUILD / "requirements.txt").write_text("\n".join(reqs) + "\n")

platforms = ["manylinux_2_28_x86_64", "manylinux2014_x86_64", "manylinux_2_17_x86_64"]
cmd = [sys.executable, "-m", "pip", "install", "-q", "-r", str(BUILD / "requirements.txt"), "--target", str(PKG),
       "--python-version", "3.12", "--implementation", "cp", "--only-binary=:all:"]
for p in platforms:
    cmd += ["--platform", p]
subprocess.run(cmd, check=True)

# Trim files the API never needs (keeps the unpacked size under Lambda's 250 MB limit)
for d in list(PKG.rglob("__pycache__")) + list(PKG.rglob("tests")):
    shutil.rmtree(d, ignore_errors=True)

# Our own code in the same layout as the repo (the code finds its files relative to itself)
stage = BUILD / "stage"
shutil.copytree(PKG, stage)
shutil.copy(ROOT / "backend" / "lambda_handler.py", stage)
shutil.copytree(ROOT / "backend" / "app", stage / "backend" / "app", ignore=shutil.ignore_patterns("__pycache__"))
shutil.copytree(ROOT / "backend" / "artifacts", stage / "backend" / "artifacts")
(stage / "data").mkdir()
shutil.copy(ROOT / "data" / "daily_sales.csv", stage / "data")

out = BUILD / "lambda.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for f in stage.rglob("*"):
        if f.is_file():
            z.write(f, f.relative_to(stage))
unpacked = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file()) / 1e6
print(f"zip: {out.stat().st_size / 1e6:.1f} MB, unpacked: {unpacked:.0f} MB (limit 250)")

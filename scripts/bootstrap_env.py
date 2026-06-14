from __future__ import annotations

import argparse
import os
import platform
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path


SKILL_ENV_HOME = Path.home() / ".codex" / "skill-envs" / "markitdown-document-converter"
DEFAULT_ENV = SKILL_ENV_HOME / ".venv"
DEFAULT_7ZIP_DIR = SKILL_ENV_HOME / "tools" / "7zip"
DEFAULT_7ZIP_PORTABLE_DIR = SKILL_ENV_HOME / "tools" / "7zip-portable"
SEVEN_ZIP_URL = "https://www.7-zip.org/a/7z2601-x64.exe"
SEVEN_ZIP_GITHUB_EXE = "https://github.com/ip7z/7zip/releases/download/26.01/7z2601-x64.exe"
SEVEN_ZIP_7ZR = "https://github.com/ip7z/7zip/releases/download/26.01/7zr.exe"


def venv_python(venv: Path) -> Path:
    if platform.system() == "Windows":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def proxy_env() -> dict[str, str]:
    env = os.environ.copy()
    if env.get("HTTPS_PROXY") or env.get("https_proxy"):
        return env
    try:
        with socket.create_connection(("127.0.0.1", 7890), timeout=0.5):
            env["HTTP_PROXY"] = "http://127.0.0.1:7890"
            env["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    except OSError:
        pass
    return env


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(cmd))
    subprocess.check_call(cmd, env=env)


def create_venv(venv: Path) -> Path:
    py = venv_python(venv)
    if py.exists():
        return py
    venv.parent.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "venv", str(venv)])
    return py


def install_python_packages(py: Path) -> None:
    env = proxy_env()
    packages = [
        "markitdown[all]",
        "markitdown-ocr",
        "openai",
        "cryptography",
        "pypdf",
        "openpyxl",
        "pandas",
        "pyyaml",
        "pymupdf",
        "html-to-markdown",
        "trafilatura",
        "markdownify",
        "python-docx",
        "Pillow",
    ]
    if platform.system() == "Windows":
        packages.append("pywin32")
    run([str(py), "-m", "pip", "install", "--upgrade", "pip"], env=env)
    run([str(py), "-m", "pip", "install", *packages], env=env)


def install_paddleocr_packages(py: Path) -> None:
    env = proxy_env()
    packages = [
        "paddlepaddle",
        "paddleocr",
    ]
    run([str(py), "-m", "pip", "install", *packages], env=env)


def find_7zip() -> Path | None:
    candidates = [
        os.environ.get("SEVEN_ZIP_EXE"),
        str(DEFAULT_7ZIP_PORTABLE_DIR / "full" / "7z.exe"),
        str(DEFAULT_7ZIP_DIR / "7z.exe"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for item in candidates:
        if not item:
            continue
        path = Path(item)
        if path.exists():
            return path
    return None


def download(url: str, out: Path) -> None:
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    env = proxy_env()
    proxy_handler = urllib.request.ProxyHandler(
        {
            "http": env.get("HTTP_PROXY") or env.get("http_proxy"),
            "https": env.get("HTTPS_PROXY") or env.get("https_proxy"),
        }
    )
    opener = urllib.request.build_opener(proxy_handler)
    print(f"Downloading {url}")
    with opener.open(url, timeout=120) as response:
        out.write_bytes(response.read())


def install_portable_7zip(target_dir: Path) -> Path | None:
    if platform.system() != "Windows":
        return None
    existing = find_7zip()
    if existing:
        return existing
    target_dir.mkdir(parents=True, exist_ok=True)
    sevenzr = target_dir / "7zr.exe"
    installer = target_dir / "7z2601-x64.exe"
    full_dir = target_dir / "full"
    download(SEVEN_ZIP_7ZR, sevenzr)
    download(SEVEN_ZIP_GITHUB_EXE, installer)
    if full_dir.exists():
        import shutil

        shutil.rmtree(full_dir)
    run([str(sevenzr), "x", "-y", f"-o{full_dir}", str(installer)])
    exe = full_dir / "7z.exe"
    return exe if exe.exists() else None


def install_7zip(target_dir: Path) -> Path | None:
    if platform.system() != "Windows":
        return None
    existing = find_7zip()
    if existing:
        return existing
    portable = install_portable_7zip(DEFAULT_7ZIP_PORTABLE_DIR)
    if portable:
        return portable
    target_dir.mkdir(parents=True, exist_ok=True)
    installer = target_dir.parent / "7zip-installer.exe"
    download(SEVEN_ZIP_URL, installer)
    try:
        run([str(installer), "/S", f"/D={target_dir}"])
    except OSError as exc:
        print(f"7-Zip installer could not run without elevation: {exc}")
        print("Install 7-Zip manually or set SEVEN_ZIP_EXE to an existing 7z.exe for RAR support.")
        return None
    except subprocess.CalledProcessError as exc:
        print(f"7-Zip installer failed: {exc}")
        print("Install 7-Zip manually or set SEVEN_ZIP_EXE to an existing 7z.exe for RAR support.")
        return None
    exe = target_dir / "7z.exe"
    return exe if exe.exists() else None


def verify(py: Path) -> None:
    code = (
        "import importlib.util; "
        "mods=['markitdown','openai','pypdf','openpyxl','pandas','fitz',"
        "'html_to_markdown','trafilatura','markdownify','docx','PIL']; "
        "print('\\n'.join(f'{m}: {bool(importlib.util.find_spec(m))}' for m in mods))"
    )
    run([str(py), "-c", code])


def verify_paddleocr(py: Path) -> None:
    code = (
        "import importlib.util; "
        "mods=['paddle','paddleocr']; "
        "print('\\n'.join(f'{m}: {bool(importlib.util.find_spec(m))}' for m in mods))"
    )
    run([str(py), "-c", code])


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the MarkItDown document converter environment.")
    parser.add_argument("--env-dir", default=str(DEFAULT_ENV), help="Virtual environment directory.")
    parser.add_argument("--install-7zip", action="store_true", help="Download and install portable 7-Zip into the skill env.")
    parser.add_argument("--with-paddleocr", action="store_true", help="Install local PaddleOCR dependencies for scanned PDFs/images.")
    parser.add_argument("--skip-packages", action="store_true", help="Create/verify the environment without installing packages.")
    args = parser.parse_args()

    venv = Path(args.env_dir).expanduser().resolve()
    py = create_venv(venv)
    if not args.skip_packages:
        install_python_packages(py)
    if args.with_paddleocr:
        install_paddleocr_packages(py)
    sevenzip = install_7zip(DEFAULT_7ZIP_DIR) if args.install_7zip else find_7zip()
    verify(py)
    if args.with_paddleocr:
        verify_paddleocr(py)
    print()
    print(f"Python: {py}")
    print(f"7-Zip: {sevenzip or 'not found; RAR files will be marked unresolved'}")
    print("Set MARKITDOWN_PYTHON to this Python path if you want callers to reuse it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

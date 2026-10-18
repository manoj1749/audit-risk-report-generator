"""
Fallback pip installer for a venv created with --without-pip, used when
ensurepip's normal self-bootstrap fails.

Hit in practice on pip >=26.1: ensurepip installs a wheel literally named
"pip" via bundled wheels sitting on sys.path, and pip's own post-CVE
import guard (_prevent_import_hook, added to stop a malicious wheel from
impersonating pip during a self-update) treats that exactly like the
attack it's guarding against and refuses to import its own
pip._internal.operations.install.wheel module:

    ImportError: No module named 'pip._internal.operations.install.wheel'

Works around it by never asking pip to "install pip": download pip's
wheel directly from PyPI and extract it into the venv's site-packages
ourselves, then write the pip/pip3/pip3.X console scripts by hand.
"""
import json
import pathlib
import stat
import subprocess
import sys
import urllib.request
import zipfile

PIP_VERSION = "25.3"  # last pre-26.x release; avoids the import-guard bug above


def venv_paths(venv_dir):
    venv_dir = pathlib.Path(venv_dir).resolve()
    py = venv_dir / "bin" / "python"
    major_minor = subprocess.check_output(
        [str(py), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        text=True,
    ).strip()
    site_packages = venv_dir / "lib" / f"python{major_minor}" / "site-packages"
    return venv_dir, py, major_minor, site_packages


def wheel_url(package, version):
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/{package}/{version}/json", timeout=30
    ) as resp:
        data = json.load(resp)
    for entry in data["urls"]:
        if entry["packagetype"] == "bdist_wheel":
            return entry["url"]
    raise RuntimeError(f"No wheel found for {package}=={version}")


def main():
    if len(sys.argv) != 2:
        print("usage: bootstrap_pip.py <venv-dir>", file=sys.stderr)
        sys.exit(1)

    venv_dir, py, major_minor, site_packages = venv_paths(sys.argv[1])

    print(f"📥 Fetching pip {PIP_VERSION} wheel directly from PyPI...")
    url = wheel_url("pip", PIP_VERSION)
    tmp_whl = venv_dir / "_pip_bootstrap.whl"
    urllib.request.urlretrieve(url, tmp_whl)

    with zipfile.ZipFile(tmp_whl) as zf:
        zf.extractall(site_packages)
    tmp_whl.unlink()

    bin_dir = venv_dir / "bin"
    script_body = (
        f"#!{py}\n"
        "import sys\n"
        "from pip._internal.cli.main import main\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n"
    )

    for name in ("pip", "pip3", f"pip{major_minor}"):
        script_path = bin_dir / name
        script_path.write_text(script_body)
        script_path.chmod(
            script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    print(f"✅ pip {PIP_VERSION} installed manually into {site_packages}")


if __name__ == "__main__":
    main()

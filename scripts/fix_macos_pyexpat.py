"""
Works around a Homebrew python@3.11 bottle / macOS version mismatch where
pyexpat.so was linked expecting a newer libexpat than the one the OS
actually ships at /usr/lib/libexpat.1.dylib, e.g.:

    ImportError: dlopen(...pyexpat.cpython-311-darwin.so, ...):
    Symbol not found: _XML_SetAllocTrackerActivationThreshold
    Expected in: /usr/lib/libexpat.1.dylib

pyexpat is buried deep in pip's own import chain (via its vendored
distlib -> xmlrpc.client -> xml.parsers.expat), so this breaks
essentially every pip command, not just XML-handling code.

Fix: point pyexpat.so at Homebrew's own (newer) libexpat instead of the
system one. Safe to run repeatedly and on machines without the bug —
it checks first and does nothing if the import already works.
"""
import glob
import os
import shutil
import subprocess
import sys
import sysconfig


def find_pyexpat_so():
    stdlib = sysconfig.get_paths()["stdlib"]
    matches = glob.glob(os.path.join(stdlib, "lib-dynload", "pyexpat*.so"))
    return matches[0] if matches else None


def already_works():
    result = subprocess.run(
        [sys.executable, "-c", "import xml.parsers.expat"],
        capture_output=True,
    )
    return result.returncode == 0


def main():
    if sys.platform != "darwin":
        return
    if already_works():
        return

    so_path = find_pyexpat_so()
    if not so_path:
        print("⚠️  pyexpat is broken but its .so couldn't be located — skipping the automatic fix.")
        return

    if not shutil.which("brew"):
        print("⚠️  pyexpat is broken and Homebrew isn't available to fix it automatically.")
        return

    print("🔧 Installing Homebrew's expat (system libexpat is too old for this Python build)...")
    subprocess.run(["brew", "install", "expat"], check=False)

    prefix = subprocess.run(
        ["brew", "--prefix", "expat"], capture_output=True, text=True
    )
    if prefix.returncode != 0:
        print("⚠️  Couldn't find Homebrew's expat after installing it — skipping the fix.")
        return
    new_lib = os.path.join(prefix.stdout.strip(), "lib", "libexpat.1.dylib")
    if not os.path.exists(new_lib):
        print(f"⚠️  Expected {new_lib} but it doesn't exist — skipping the fix.")
        return

    backup = so_path + ".bak"
    if not os.path.exists(backup):
        shutil.copy(so_path, backup)

    result = subprocess.run(
        [
            "install_name_tool",
            "-change",
            "/usr/lib/libexpat.1.dylib",
            new_lib,
            so_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"⚠️  install_name_tool failed: {result.stderr}")
        return

    if already_works():
        print(f"✅ Repointed {so_path} at {new_lib}")
    else:
        print("⚠️  Repointed the library but pyexpat still doesn't import — restoring backup.")
        shutil.copy(backup, so_path)


if __name__ == "__main__":
    main()

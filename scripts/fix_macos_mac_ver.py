"""
Works around a macOS 26 (Tahoe) bug where Python's platform.mac_ver()
returns all-empty values instead of the real OS version, because
_mac_ver_xml() can't read /System/Library/CoreServices/SystemVersion.plist
on that OS version. pip >=24.2 calls mac_ver() unconditionally (via its
vendored `truststore` module) when building its SSL context, so this
breaks `pip install` and even `python -m venv` (ensurepip) outright with:

    ValueError: invalid literal for int() with base 10: ''

`sw_vers` still reports the real version fine, so the fix patches the
stdlib's _mac_ver_xml() to fall back to it. Safe to run repeatedly and
on machines that don't have the bug — it checks first and does nothing
if mac_ver() already works.
"""
import platform
import shutil
import sys


def main():
    if sys.platform != "darwin":
        return
    if platform.mac_ver()[0]:
        return  # already works, nothing to do

    path = platform.__file__
    try:
        with open(path) as f:
            src = f.read()
    except OSError as e:
        print(f"⚠️  Couldn't read {path} to check for the mac_ver bug: {e}")
        return

    old = """def _mac_ver_xml():
    fn = '/System/Library/CoreServices/SystemVersion.plist'
    if not os.path.exists(fn):
        return None

    try:
        import plistlib
    except ImportError:
        return None

    with open(fn, 'rb') as f:
        pl = plistlib.load(f)
    release = pl['ProductVersion']
    versioninfo = ('', '', '')
    machine = os.uname().machine
    if machine in ('ppc', 'Power Macintosh'):
        # Canonical name
        machine = 'PowerPC'

    return release, versioninfo, machine"""

    new = """def _mac_ver_xml():
    fn = '/System/Library/CoreServices/SystemVersion.plist'
    release = None
    if os.path.exists(fn):
        try:
            import plistlib
            with open(fn, 'rb') as f:
                pl = plistlib.load(f)
            release = pl.get('ProductVersion')
        except Exception:
            release = None
    if not release:
        try:
            import subprocess
            release = subprocess.check_output(
                ['/usr/bin/sw_vers', '-productVersion'], text=True
            ).strip()
        except Exception:
            release = None
    if not release:
        return None

    versioninfo = ('', '', '')
    machine = os.uname().machine
    if machine in ('ppc', 'Power Macintosh'):
        machine = 'PowerPC'

    return release, versioninfo, machine"""

    if old not in src:
        print(
            f"⚠️  mac_ver() is broken on this machine but platform.py's source "
            f"doesn't match what this patch expects (Python {platform.python_version()}) "
            f"— skipping the automatic fix. pip installs may fail; see "
            f"https://github.com/pypa/pip/issues (search 'truststore mac_ver')."
        )
        return

    try:
        shutil.copy(path, path + ".bak")
        with open(path, "w") as f:
            f.write(src.replace(old, new))
    except OSError as e:
        print(f"⚠️  Couldn't patch {path}: {e}")
        return

    print(f"🔧 Patched a macOS/Python bug in {path} (backup: {path}.bak)")


if __name__ == "__main__":
    main()

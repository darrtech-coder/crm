import os
import sys
import shutil
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def ensure_poppler():
    if os.name == "nt":  # Windows
        poppler_dir = os.path.join(BASE_DIR, "poppler", "Library", "bin")
        if not os.path.exists(poppler_dir):
            raise RuntimeError(
                "Poppler binaries missing. Please place Poppler in ./poppler (with Library/bin inside)."
            )
        print(f"✅ Poppler found for Windows at {poppler_dir}")
        return poppler_dir

    else:  # POSIX (Linux, macOS)
        # Check if pdfinfo is available
        if shutil.which("pdfinfo"):
            print("✅ Poppler already installed on system")
            return None

        distro = ""
        try:
            distro = subprocess.check_output(["uname", "-s"]).decode().strip()
        except Exception:
            pass

        if "Darwin" in distro:  # macOS
            print("⚠️ Installing poppler via brew (needs brew installed).")
            subprocess.run(["brew", "install", "poppler"], check=True)
        else:  # Linux
            print("⚠️ Installing poppler via apt (needs sudo).")
            subprocess.run(["sudo", "apt-get", "update"], check=True)
            subprocess.run(["sudo", "apt-get", "install", "-y", "poppler-utils"], check=True)

        if shutil.which("pdfinfo"):
            print("✅ Poppler installation successful")
        else:
            raise RuntimeError("Failed to install poppler!")
        return None


if __name__ == "__main__":
    ensure_poppler()
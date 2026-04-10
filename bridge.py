"""USB Drive Bridge — presents a FAT32 image as a USB mass storage device.

Runs on a Raspberry Pi Zero W / 2W. Exposes a small HTTP API so that
BendGen (running on another machine) can deploy files to and read files
from the virtual USB drive that is plugged into the Titan press brake.

Endpoints:
    GET  /api/status           — gadget + image health check
    POST /api/deploy           — upload a ZIP, write it to the USB image
    GET  /api/backups          — list files currently on the USB image
    GET  /api/backup/<name>    — download a file from the USB image

Implementation notes:

- Reads (list + download) use a read-only loop mount of the backing
  file. This coexists with the gadget (which has the file open
  read-write) because Linux allows additional read-only opens. No
  eject needed, so the Titan continues to see the drive uninterrupted.

- Writes (deploy) use mtools (mdel + mcopy) to manipulate the FAT
  image directly, after ejecting the gadget. This sidesteps the
  loop-mount timing race where the kernel doesn't immediately release
  the backing file after eject, which manifests as EBUSY on mount.
"""

import os
import subprocess
import tempfile
import time
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    """Allow BendGen (running on a different host/port) to call the bridge API."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# --- Configuration ---
USB_IMAGE = os.environ.get("USB_IMAGE", "/piusb.bin")
USB_IMAGE_SIZE_MB = int(os.environ.get("USB_IMAGE_SIZE_MB", "512"))
GADGET_LUN = os.environ.get(
    "GADGET_LUN",
    "/sys/kernel/config/usb_gadget/g1/functions/mass_storage.0/lun.0",
)
MOUNT_POINT = os.environ.get("MOUNT_POINT", "/mnt/usb_image")
ALLOWED_EXTENSIONS = {".zip"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
EJECT_WAIT_SECONDS = 1.0  # let the kernel fully release the backing file


# ── Gadget helpers ───────────────────────────────────────────────────────

def _sysfs_write(path, value):
    """Write a value to a sysfs file."""
    Path(path).write_text(str(value))


def _sysfs_read(path):
    """Read a value from a sysfs file."""
    try:
        return Path(path).read_text().strip()
    except FileNotFoundError:
        return None


def gadget_is_active():
    """Check whether the gadget LUN is presenting an image."""
    current = _sysfs_read(f"{GADGET_LUN}/file")
    return bool(current)


def eject_media():
    """Tell the host (Titan) the media has been removed and unbind the
    backing file so we can safely write to it locally.
    """
    # forced_eject isn't available on every kernel — tolerate failure
    try:
        _sysfs_write(f"{GADGET_LUN}/forced_eject", "")
    except OSError:
        pass
    _sysfs_write(f"{GADGET_LUN}/file", "")
    time.sleep(EJECT_WAIT_SECONDS)


def insert_media():
    """Re-present the image to the host (Titan)."""
    _sysfs_write(f"{GADGET_LUN}/file", USB_IMAGE)
    time.sleep(0.3)


# ── Image read helpers (read-only loop mount) ────────────────────────────

def mount_ro():
    """Mount the FAT32 image read-only. Coexists with the gadget."""
    Path(MOUNT_POINT).mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["mount", "-o", "loop,ro", USB_IMAGE, MOUNT_POINT],
        check=True,
    )


def unmount():
    """Unmount the image."""
    subprocess.run(["umount", MOUNT_POINT], check=True)


# ── Image write helpers (mtools) ─────────────────────────────────────────

def mtools_clear_root():
    """Delete all files at the root of the USB image.

    Must be called with the gadget ejected (file unbound) so we don't
    race with the kernel mass_storage cache.
    """
    result = subprocess.run(
        ["mdir", "-b", "-i", USB_IMAGE, "::/"],
        capture_output=True, text=True, check=False,
    )
    for line in result.stdout.splitlines():
        name = line.strip()
        if not name.startswith("::"):
            continue
        # mdel only removes files; directories are silently skipped
        subprocess.run(
            ["mdel", "-i", USB_IMAGE, name],
            check=False, capture_output=True,
        )


def mtools_copy_to_image(src_path, dest_name):
    """Copy a local file onto the root of the USB image."""
    subprocess.run(
        ["mcopy", "-o", "-i", USB_IMAGE, src_path, f"::/{dest_name}"],
        check=True,
    )


# ── API routes ───────────────────────────────────────────────────────────

@app.route("/api/status")
def status():
    """Health check: is the gadget active? Does the image exist?"""
    image_exists = os.path.isfile(USB_IMAGE)
    lun_exists = os.path.isdir(GADGET_LUN)
    active = gadget_is_active() if lun_exists else False
    return jsonify({
        "ok": True,
        "image_exists": image_exists,
        "gadget_lun_exists": lun_exists,
        "gadget_active": active,
        "image_path": USB_IMAGE,
        "image_size_mb": USB_IMAGE_SIZE_MB,
    })


@app.route("/api/deploy", methods=["POST"])
def deploy():
    """Receive a file, write it to the USB image, re-present to host.

    Flow: eject → mtools clear root → mtools copy new file → reinsert.
    """
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    uploaded = request.files["file"]
    filename = secure_filename(uploaded.filename or "backup.zip")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"ok": False, "error": f"Only {sorted(ALLOWED_EXTENSIONS)} files allowed"}), 400

    file_bytes = uploaded.read(MAX_UPLOAD_BYTES + 1)
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        return jsonify({"ok": False, "error": f"File too large (max {MAX_UPLOAD_BYTES // 1024 // 1024} MB)"}), 400

    # Write the upload to a local temp file so mtools can copy from it
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        eject_media()
        mtools_clear_root()
        mtools_copy_to_image(tmp_path, filename)
        insert_media()
        return jsonify({"ok": True, "filename": filename, "size": len(file_bytes)})

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or "")
        try: insert_media()
        except Exception: pass
        return jsonify({"ok": False, "error": f"mtools failed: {stderr.strip() or str(e)}"}), 500

    except Exception as e:
        try: insert_media()
        except Exception: pass
        return jsonify({"ok": False, "error": str(e)}), 500

    finally:
        try: os.unlink(tmp_path)
        except OSError: pass


@app.route("/api/backups")
def list_backups():
    """List files currently on the USB image.

    Uses a read-only loop mount — no eject, Titan keeps seeing the drive.
    """
    try:
        mount_ro()
        files = []
        for item in sorted(Path(MOUNT_POINT).iterdir()):
            if item.is_file():
                stat = item.stat()
                files.append({
                    "name": item.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
        unmount()
        return jsonify({"ok": True, "files": files})

    except Exception as e:
        try: unmount()
        except Exception: pass
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/backup/<filename>")
def download_backup(filename):
    """Download a specific file from the USB image.

    Uses a read-only loop mount — no eject.
    """
    filename = secure_filename(filename)
    if not filename:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    try:
        mount_ro()

        file_path = Path(MOUNT_POINT) / filename
        if not file_path.is_file():
            unmount()
            return jsonify({"ok": False, "error": "File not found"}), 404

        # Copy to a temp location so we can unmount before sending
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1])
        tmp.write(file_path.read_bytes())
        tmp.close()

        unmount()

        return send_from_directory(
            os.path.dirname(tmp.name),
            os.path.basename(tmp.name),
            as_attachment=True,
            download_name=filename,
        )

    except Exception as e:
        try: unmount()
        except Exception: pass
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Startup ──────────────────────────────────────────────────────────────

def main():
    host = os.environ.get("BRIDGE_HOST", "0.0.0.0")
    port = int(os.environ.get("BRIDGE_PORT", "8080"))
    print(f"USB Drive Bridge listening on {host}:{port}")
    print(f"Image: {USB_IMAGE}  LUN: {GADGET_LUN}")
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()

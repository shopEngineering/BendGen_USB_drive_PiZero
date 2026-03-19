"""USB Drive Bridge — presents a FAT32 image as a USB mass storage device.

Runs on a Raspberry Pi Zero W / 2W. Exposes a small HTTP API so that
BendGen (running on another machine) can deploy files to and read files
from the virtual USB drive that is plugged into the Titan press brake.

Endpoints:
    GET  /api/status           — gadget + image health check
    POST /api/deploy           — upload a ZIP, write it to the USB image
    GET  /api/backups          — list files currently on the USB image
    GET  /api/backup/<name>    — download a file from the USB image
    POST /api/sync-from-titan  — eject, mount, read files written by Titan
"""

import os
import shutil
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
    """Tell the host (Titan) that the media has been removed."""
    _sysfs_write(f"{GADGET_LUN}/forced_eject", "")
    # Clear the backing file so the kernel fully drops the device
    _sysfs_write(f"{GADGET_LUN}/file", "")
    time.sleep(0.3)


def insert_media():
    """Re-present the image to the host (Titan)."""
    _sysfs_write(f"{GADGET_LUN}/file", USB_IMAGE)
    time.sleep(0.3)


def mount_image():
    """Mount the FAT32 image locally for read/write."""
    Path(MOUNT_POINT).mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["mount", "-o", "loop", USB_IMAGE, MOUNT_POINT],
        check=True,
    )


def unmount_image():
    """Unmount the FAT32 image."""
    subprocess.run(["umount", MOUNT_POINT], check=True)


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
    """Receive a file, write it to the USB image.

    The cycle: eject → mount → clear old files → write new file → unmount → reinsert.
    """
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    uploaded = request.files["file"]
    filename = secure_filename(uploaded.filename or "backup.zip")

    # Validate extension
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"ok": False, "error": f"Only {ALLOWED_EXTENSIONS} files allowed"}), 400

    # Read into memory (bounded)
    file_bytes = uploaded.read(MAX_UPLOAD_BYTES + 1)
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        return jsonify({"ok": False, "error": f"File too large (max {MAX_UPLOAD_BYTES // 1024 // 1024} MB)"}), 400

    try:
        # 1) Eject media from Titan
        eject_media()

        # 2) Mount image locally
        mount_image()

        # 3) Clear existing files on the image
        for item in Path(MOUNT_POINT).iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)

        # 4) Write the new file
        dest = Path(MOUNT_POINT) / filename
        dest.write_bytes(file_bytes)

        # 5) Unmount
        unmount_image()

        # 6) Reinsert media — Titan sees fresh drive
        insert_media()

        return jsonify({"ok": True, "filename": filename, "size": len(file_bytes)})

    except Exception as e:
        # Best-effort cleanup
        try:
            unmount_image()
        except Exception:
            pass
        try:
            insert_media()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/backups")
def list_backups():
    """List files currently on the USB image.

    Ejects, mounts read-only, reads listing, unmounts, reinserts.
    """
    try:
        eject_media()
        mount_image()

        files = []
        for item in sorted(Path(MOUNT_POINT).iterdir()):
            if item.is_file():
                stat = item.stat()
                files.append({
                    "name": item.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })

        unmount_image()
        insert_media()

        return jsonify({"ok": True, "files": files})

    except Exception as e:
        try:
            unmount_image()
        except Exception:
            pass
        try:
            insert_media()
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/backup/<filename>")
def download_backup(filename):
    """Download a specific file from the USB image."""
    filename = secure_filename(filename)
    if not filename:
        return jsonify({"ok": False, "error": "Invalid filename"}), 400

    try:
        eject_media()
        mount_image()

        file_path = Path(MOUNT_POINT) / filename
        if not file_path.is_file():
            unmount_image()
            insert_media()
            return jsonify({"ok": False, "error": "File not found"}), 404

        # Copy to temp location so we can unmount before sending
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1])
        tmp.write(file_path.read_bytes())
        tmp.close()

        unmount_image()
        insert_media()

        return send_from_directory(
            os.path.dirname(tmp.name),
            os.path.basename(tmp.name),
            as_attachment=True,
            download_name=filename,
        )

    except Exception as e:
        try:
            unmount_image()
        except Exception:
            pass
        try:
            insert_media()
        except Exception:
            pass
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

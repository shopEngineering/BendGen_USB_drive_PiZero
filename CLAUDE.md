# USB Drive Bridge for Pi Zero

## Quick Reference

- **Run locally (for testing):** `python bridge.py` → http://localhost:8080
- **Deploy to Pi:** SSH in, run `bash install.sh`, reboot
- **Commits:** Use author `shopEngineering <shopEngineering@users.noreply.github.com>` — no personal info

## Project Structure

```
bridge.py          — Flask service (~180 lines). USB gadget management + HTTP API
install.sh         — Full Pi setup: dwc2, FAT32 image, configfs gadget, systemd services
requirements.txt   — flask, werkzeug
README.md          — Detailed setup guide with ASCII diagrams
```

## How It Works

1. Pi Zero's USB data port connects to Titan via cable
2. `install.sh` enables `dwc2` overlay and creates a FAT32 disk image (`/piusb.bin`)
3. A systemd service (`usb-gadget`) configures configfs to present the image as a removable USB drive
4. Another systemd service (`usb-bridge`) runs `bridge.py` on port 8080
5. BendGen (on another computer) calls the API to deploy/read files
6. Deploy cycle: eject virtual media → mount image → write file → unmount → reinsert media (~1-2 sec)

## API

| Endpoint | Method | What it does |
|----------|--------|-------------|
| `/api/status` | GET | Health check — image exists? gadget active? |
| `/api/deploy` | POST | Receive ZIP file, eject→write→reinsert |
| `/api/backups` | GET | List files on USB image (ejects briefly) |
| `/api/backup/<name>` | GET | Download a file from USB image |

## Key Design Decisions

- **Read-write gadget** (`ro=0`) — Titan needs to write backups too, not just read
- **CORS enabled** — BendGen frontend calls this directly from a different origin
- **Runs as root** — needs sysfs/mount access for gadget control. The service is minimal to limit exposure.
- **`removable=1`** — Titan sees it as a card reader with swappable media, enabling the eject/reinsert trick
- **`forced_eject`** — overrides SCSI PREVENT MEDIUM REMOVAL locks the host may hold
- **Separate power supply required** — Titan USB port may not provide enough current for the Pi

## Sysfs Paths

```
/sys/kernel/config/usb_gadget/g1/functions/mass_storage.0/lun.0/
  file           — path to backing image (write to present, clear to eject)
  forced_eject   — write empty string to force eject
  removable      — 1 = removable media
  ro             — 0 = read-write, 1 = read-only
```

## Testing Without a Pi

`bridge.py` can run on any machine for API testing. The gadget operations will fail (no sysfs), but you can test the Flask routes and CORS. Set `USB_IMAGE` to a test file.

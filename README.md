# USB Drive Bridge

Turns a Raspberry Pi Zero W / Zero 2W into a WiFi-connected USB drive for the Titan press brake.

## What it does

The Pi plugs into the Titan's USB port and presents itself as a removable USB flash drive. A small HTTP API lets BendGen (running on any computer) send files to the Pi, which writes them to the virtual drive. The Titan sees a freshly inserted USB drive with your file on it.

## Install

On a Pi Zero W / Zero 2W running Raspberry Pi OS:

```bash
bash install.sh
```

This will:
1. Enable USB OTG gadget mode (dwc2)
2. Create a 512MB FAT32 disk image
3. Configure the USB mass storage gadget
4. Install the bridge API as a systemd service
5. Prompt for a reboot

## API

The bridge listens on port 8080.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Health check — is the gadget active? |
| `/api/deploy` | POST | Upload a ZIP file to the USB drive |
| `/api/backups` | GET | List files on the USB drive (ejects briefly) |
| `/api/backup/<name>` | GET | Download a file from the USB drive |

## Hardware Setup

Connect the Pi Zero's **USB data port** (not the power port) to the Titan's USB port. Power the Pi via the power port or through a powered USB hub.

## Configuration

Environment variables (set in systemd service or shell):

| Variable | Default | Description |
|----------|---------|-------------|
| `USB_IMAGE` | `/piusb.bin` | Path to the FAT32 disk image |
| `USB_IMAGE_SIZE_MB` | `512` | Image size (only used during creation) |
| `BRIDGE_PORT` | `8080` | HTTP API port |

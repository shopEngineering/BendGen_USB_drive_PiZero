# USB Drive Bridge for Pi Zero W

Turns a Raspberry Pi Zero W / Zero 2W into a WiFi-connected USB drive for the Titan press brake. BendGen sends bend programs to the Pi over WiFi, and the Pi writes them to a virtual USB drive that the Titan reads directly — no USB drive swapping needed.

## How It Works

```
┌─────────────────┐         WiFi / LAN         ┌──────────────────┐
│   Your Computer │ ◄──────────────────────────►│  Pi Zero W / 2W  │
│                 │    BendGen talks to Pi       │                  │
│  BendGen app    │    on port 8080              │  USB Drive Bridge│
│  (browser)      │                              │  (bridge.py)     │
└─────────────────┘                              └──────┬───────────┘
                                                        │ USB cable
                                                        │ (data port)
                                                 ┌──────┴───────────┐
                                                 │   Titan Press    │
                                                 │   Brake          │
                                                 │                  │
                                                 │  Sees a USB      │
                                                 │  flash drive     │
                                                 └──────────────────┘
```

When you click "Deploy" in BendGen:
1. Pi ejects the virtual USB drive (~instant)
2. Mounts the FAT32 image locally, writes your ZIP file
3. Unmounts and re-presents the drive to the Titan
4. Titan sees a freshly inserted USB drive with your program
5. Total time: ~1-2 seconds

---

## What You Need

| Item | Notes |
|------|-------|
| **Raspberry Pi Zero W** or **Zero 2W** | Only these models support USB OTG on the data port |
| **Micro SD card** (8GB+) | Class 10 or better |
| **Micro USB to USB-A data cable** | Must be a data cable, not charge-only |
| **5V micro USB power supply** (2.5A+) | Powers the Pi separately |
| **A computer on the same WiFi** | Runs BendGen |

---

## Step-by-Step Setup

### 1. Flash Raspberry Pi OS

1. Download **Raspberry Pi Imager** from `raspberrypi.com/software`
2. Insert your micro SD card into your computer
3. In the Imager, choose:
   - **Device:** Raspberry Pi Zero W (or Zero 2W)
   - **OS:** Raspberry Pi OS (32-bit) — Lite is fine
   - **Storage:** your SD card
4. **Click the gear icon** (Edit Settings) — this is critical:
   - **Hostname:** `bendgen-bridge`
   - **Enable SSH:** yes, with password authentication
   - **Username/password:** pick something you'll remember (e.g., `pi` / your password)
   - **WiFi:** enter your shop network name and password, set country
5. Click **Write** and wait for it to finish
6. Eject the SD card and insert it into the Pi

### 2. Install the Bridge Software

1. **Power on the Pi** — plug in the power supply to the PWR port. Wait 1-2 minutes for first boot.

2. **SSH into the Pi** from your computer:
   ```bash
   ssh pi@bendgen-bridge.local
   ```
   (Accept the fingerprint, enter your password)

3. **Download and run the installer:**
   ```bash
   curl -sSL https://raw.githubusercontent.com/shopEngineering/BendGen_USB_drive_PiZero/master/install.sh -o install.sh
   bash install.sh
   ```

4. **Reboot when prompted.** The bridge starts automatically on boot.

5. **Verify it's running** (after reboot, SSH back in):
   ```bash
   sudo systemctl status usb-bridge
   ```

### 3. Connect to the Titan

The Pi Zero has two micro USB ports — use the right ones:

```
 Pi Zero W / 2W (top view)
┌───────────────────────────────────────────────────────────┐
│                                                           │
│  mini HDMI        micro USB          micro USB            │
│  (video)          (USB DATA)         (POWER)              │
│                   └── to Titan       └── to power supply  │
│                                                           │
└───────────────────────────────────────────────────────────┘
   ▲ SD card slot (underneath)
```

1. Power off the Pi
2. Connect USB data cable: Pi **USB DATA port** (center) → Titan **USB-A port**
3. Connect power supply: Pi **PWR port** (edge) → 5V power supply
4. Wait ~30 seconds for boot — Titan should detect a USB drive

> **Important:** Always use a separate power supply. The Titan's USB port may not provide enough power for the Pi.

### 4. Find the Pi's IP Address

You need the Pi's address to connect BendGen. Try these in order:

**Option A — Hostname (easiest):**
```bash
ping bendgen-bridge.local
```
If it responds, your bridge address is `bendgen-bridge.local:8080`

**Option B — Check your router:**
Log into your router's admin page. Look for a device named `bendgen-bridge` and note its IP.

**Option C — SSH and check:**
```bash
ssh pi@bendgen-bridge.local
hostname -I
# prints something like: 192.168.1.42
```
Your bridge address would be `192.168.1.42:8080`

> **Recommended:** Set a static IP reservation on your router so the address never changes.

### 5. Connect BendGen

1. Open BendGen on your computer (any computer on the same network)
2. Click **Deploy** in the Titan section of the header
3. A settings dialog opens — enter the bridge address (e.g., `bendgen-bridge.local:8080`)
4. Click **Test Connection** — you should see "Connected — gadget active"
5. Click **Save** — the address is remembered in your browser

### 6. Use It

**Deploy to Titan (send a program):**
1. Create/edit your program in BendGen
2. Click **Deploy** in the header
3. Wait for "Deployed" confirmation (~1-2 seconds)
4. On the Titan: Backup/Restore → Restore From → select the ZIP

**Import from Titan (get a backup):**
1. On the Titan: Backup/Restore → enter a name → Create Backup
2. In BendGen: click **Get Backup** in the header
3. Select the file to import

---

## Troubleshooting

### "Cannot reach bridge" in BendGen
- Is the Pi powered on and connected to WiFi?
- Are your computer and the Pi on the same network?
- Try the IP address instead of hostname
- SSH in and check: `sudo systemctl status usb-bridge`

### "Gadget not active"
- USB cable on the wrong port? Must be the **center** port (DATA), not edge (PWR)
- Is it a data cable? Charge-only cables won't work
- Check: `sudo systemctl status usb-gadget`
- Check: `lsmod | grep dwc2`

### Titan doesn't see the USB drive
- Try a different USB cable (data cable, not charge-only)
- Try a different USB port on the Titan
- Check: `cat /sys/kernel/config/usb_gadget/g1/UDC`
- Reboot the Pi with the USB cable connected

### Viewing logs
```bash
# Bridge API logs:
journalctl -u usb-bridge -f

# Gadget setup logs:
journalctl -u usb-gadget
```

---

## API Reference

The bridge listens on port 8080.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/status` | GET | Health check — is the gadget active? |
| `/api/deploy` | POST | Upload a ZIP file to the USB drive |
| `/api/backups` | GET | List files on the USB drive |
| `/api/backup/<name>` | GET | Download a file from the USB drive |

## Configuration

Environment variables (set in systemd service or shell):

| Variable | Default | Description |
|----------|---------|-------------|
| `USB_IMAGE` | `/piusb.bin` | Path to the FAT32 disk image |
| `USB_IMAGE_SIZE_MB` | `512` | Image size (only used during creation) |
| `BRIDGE_PORT` | `8080` | HTTP API port |

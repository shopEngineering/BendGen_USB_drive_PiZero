#!/bin/bash
# USB Drive Bridge installer for Raspberry Pi Zero W / Zero 2W
# Configures the Pi as a USB mass storage gadget and installs the bridge service.
#
# Run with: bash install.sh
set -e

INSTALL_DIR="$HOME/usb-drive-bridge"
SERVICE_NAME="usb-bridge"
IMAGE_PATH="/piusb.bin"
IMAGE_SIZE_MB=512

echo "=== USB Drive Bridge Installer ==="
echo "This sets up your Pi Zero W as a USB drive bridge."
echo ""

# ── 1. Check hardware ────────────────────────────────────────────────────

# Verify we're on a Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "Warning: Cannot detect Raspberry Pi model. Continuing anyway..."
else
    MODEL=$(tr -d '\0' < /proc/device-tree/model)
    echo "Detected: $MODEL"
    if ! echo "$MODEL" | grep -qi "zero"; then
        echo ""
        echo "WARNING: USB gadget mode requires a Pi Zero W or Zero 2W."
        echo "Other Pi models do not support OTG on the USB data port."
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo ""
        [[ $REPLY =~ ^[Yy]$ ]] || exit 1
    fi
fi

# ── 2. System dependencies ───────────────────────────────────────────────

echo ""
echo "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv dosfstools

# ── 3. Enable USB OTG (dwc2) ─────────────────────────────────────────────

echo ""
echo "Configuring USB OTG gadget mode..."

# Add dtoverlay=dwc2 to /boot/config.txt (or /boot/firmware/config.txt on newer OS)
BOOT_CONFIG="/boot/config.txt"
[ -f "/boot/firmware/config.txt" ] && BOOT_CONFIG="/boot/firmware/config.txt"

if ! grep -q "^dtoverlay=dwc2" "$BOOT_CONFIG" 2>/dev/null; then
    echo "dtoverlay=dwc2" | sudo tee -a "$BOOT_CONFIG" > /dev/null
    echo "  Added dtoverlay=dwc2 to $BOOT_CONFIG"
else
    echo "  dtoverlay=dwc2 already in $BOOT_CONFIG"
fi

# Add dwc2 to /etc/modules if not present
if ! grep -q "^dwc2" /etc/modules 2>/dev/null; then
    echo "dwc2" | sudo tee -a /etc/modules > /dev/null
    echo "  Added dwc2 to /etc/modules"
fi

# ── 4. Create the FAT32 disk image ──────────────────────────────────────

if [ ! -f "$IMAGE_PATH" ]; then
    echo ""
    echo "Creating ${IMAGE_SIZE_MB}MB FAT32 disk image at $IMAGE_PATH..."
    sudo dd if=/dev/zero of="$IMAGE_PATH" bs=1M count=$IMAGE_SIZE_MB status=progress
    sudo mkfs.vfat -F 32 "$IMAGE_PATH"
    echo "  Image created."
else
    echo ""
    echo "Disk image already exists at $IMAGE_PATH"
fi

# Create mount point
sudo mkdir -p /mnt/usb_image

# ── 5. Install the bridge application ────────────────────────────────────

echo ""
echo "Installing USB Drive Bridge..."
mkdir -p "$INSTALL_DIR"

# Copy bridge files
cp "$(dirname "$0")/bridge.py" "$INSTALL_DIR/"
cp "$(dirname "$0")/requirements.txt" "$INSTALL_DIR/" 2>/dev/null || true

# Create venv and install dependencies
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet flask werkzeug

# ── 6. Create the configfs gadget setup script ───────────────────────────

sudo tee /usr/local/bin/usb-gadget-setup.sh > /dev/null << 'GADGET_EOF'
#!/bin/bash
# Configure USB mass storage gadget via configfs
set -e

IMAGE_PATH="${1:-/piusb.bin}"
GADGET_DIR="/sys/kernel/config/usb_gadget/g1"

# Load required modules
modprobe libcomposite

# Create gadget
mkdir -p "$GADGET_DIR"
cd "$GADGET_DIR"

# Device descriptor — generic USB drive identity
echo 0x1d6b > idVendor   # Linux Foundation
echo 0x0104 > idProduct  # Multifunction Composite Gadget
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "0000000001"       > strings/0x409/serialnumber
echo "BendGen"          > strings/0x409/manufacturer
echo "USB Drive Bridge" > strings/0x409/product

# Mass storage function
mkdir -p functions/mass_storage.0/lun.0
echo 1 > functions/mass_storage.0/lun.0/removable
echo 0 > functions/mass_storage.0/lun.0/ro
echo 0 > functions/mass_storage.0/lun.0/cdrom
echo "$IMAGE_PATH" > functions/mass_storage.0/lun.0/file

# Configuration
mkdir -p configs/c.1/strings/0x409
echo "USB Drive" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

# Link function to configuration
ln -sf functions/mass_storage.0 configs/c.1/

# Bind to UDC (the USB device controller)
UDC=$(ls /sys/class/udc | head -1)
if [ -z "$UDC" ]; then
    echo "ERROR: No USB device controller found. Is dwc2 loaded?"
    exit 1
fi
echo "$UDC" > UDC

echo "USB gadget configured: $IMAGE_PATH via $UDC"
GADGET_EOF

sudo chmod +x /usr/local/bin/usb-gadget-setup.sh

# ── 7. Systemd services ─────────────────────────────────────────────────

echo ""
echo "Installing systemd services..."

# Service 1: Gadget setup (runs once at boot, needs root)
sudo tee /etc/systemd/system/usb-gadget.service > /dev/null << EOF
[Unit]
Description=USB Mass Storage Gadget Setup
After=sysinit.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/usb-gadget-setup.sh $IMAGE_PATH
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# Service 2: Bridge API (runs as root — needs mount/sysfs access)
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=USB Drive Bridge API
After=network.target usb-gadget.service
Requires=usb-gadget.service

[Service]
Type=simple
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/bridge.py
Environment=USB_IMAGE=$IMAGE_PATH
Environment=BRIDGE_PORT=8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable usb-gadget
sudo systemctl enable "$SERVICE_NAME"

# ── 8. Done ──────────────────────────────────────────────────────────────

echo ""
echo "=== Installation Complete ==="
echo ""
echo "IMPORTANT: You must reboot for USB gadget mode to activate."
echo ""
echo "After reboot:"
echo "  1. Connect the Pi's USB data port to the Titan's USB port"
echo "  2. The Titan should see a USB drive"
echo "  3. The bridge API will be at http://$(hostname -I | awk '{print $1}'):8080"
echo ""
echo "  Status:  sudo systemctl status $SERVICE_NAME"
echo "  Logs:    journalctl -u $SERVICE_NAME -f"
echo ""
read -p "Reboot now? [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo reboot
fi

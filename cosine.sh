#!/bin/bash
# cosine-bootstrap: One-shot setup for SSH access via bore + dropbear
# Usage: bash cosine-bootstrap.sh [--port PORT]
set -e

PORT="${1:-1381}"
PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIzzxTwCVSqIXqbX5baRKYvw7YrT1SjoQjmD5JM0v6V1 cosine-zed"
BORE_VERSION="0.6.0"
BORE_URL="https://github.com/ekzhang/bore/releases/download/v${BORE_VERSION}/bore-v${BORE_VERSION}-x86_64-unknown-linux-musl.tar.gz"

echo "Installing dropbear..."
apt-get update -qq 2>/dev/null
apt-get install -y -qq dropbear 2>/dev/null

echo "Installing bore..."
if ! command -v bore &>/dev/null; then
    curl -sL -o /tmp/bore.tar.gz "$BORE_URL"
    tar xzf /tmp/bore.tar.gz -C /tmp/
    cp /tmp/bore /usr/local/bin/bore
    chmod +x /usr/local/bin/bore
    rm -f /tmp/bore.tar.gz /tmp/bore
    echo "bore installed: $(bore --version)"
else
    echo "bore already installed: $(bore --version)"
fi

echo "Fixing nsswitch.conf..."
sed -i 's/passwd:.*files systemd/passwd: files/' /etc/nsswitch.conf
sed -i 's/group:.*files systemd/group: files/' /etc/nsswitch.conf
sed -i 's/shadow:.*files systemd/shadow: files/' /etc/nsswitch.conf

echo "Setting root password..."
echo 'root:cosine123' | chpasswd

echo "Setting up SSH keys..."
mkdir -p /root/.ssh
chmod 700 /root/.ssh
echo "$PUBKEY" > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
chown root:root /root/.ssh/authorized_keys

echo "Cleaning up old processes..."
pkill bore 2>/dev/null || true
pkill dropbear 2>/dev/null || true
sleep 1

echo "Starting dropbear on port 22..."
nohup dropbear -R -p 22 -E -F > /tmp/dropbear.log 2>&1 &
sleep 1

echo "Starting bore tunnel on port $PORT..."
nohup bore local 22 --to bore.pub --port "$PORT" > /tmp/bore.log 2>&1 &
sleep 4

echo "Verifying setup..."
if grep -q "listening at bore.pub:$PORT" /tmp/bore.log 2>/dev/null; then
    echo "BOOTSTRAP_OK:$PORT"
else
    echo "BOOTSTRAP_FAIL:$(cat /tmp/bore.log 2>/dev/null)"
    exit 1
fi


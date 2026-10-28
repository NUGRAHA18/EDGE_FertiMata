#!/usr/bin/env bash
# Instal / upgrade edge agent FertiMata di Jetson (F10.3, F10.5).
#
#   cd ~/FertiMata/EDGE_FertiMata && git pull
#   sudo bash deploy/install.sh               # instal pertama atau upgrade
#   sudo bash deploy/install.sh --no-restart  # pasang saja, restart nanti (mis. sedang dosing)
#   bash deploy/install.sh --dry-run          # tampilkan langkah tanpa mengubah apa pun
#
# Yang dilakukan (aman diulang):
#   - user sistem `fertimata` (grup dialout, gpio, video), folder data /var/lib/fertimata
#   - venv /opt/fertimata-edge/venv berisi salinan paket dari repo ini (bukan editable: mengedit
#     repo tidak mengubah agent yang berjalan sampai skrip ini dijalankan lagi)
#   - config produksi /etc/fertimata/fertimata.yaml: disalin dari contoh HANYA bila belum ada,
#     tidak pernah ditimpa
#   - udev (/dev/rs485, /dev/cam-trolley, gpiochip), rotasi journald, unit systemd
#   - restart agent hanya bila config lolos --check-config. Restart = S1: semua relay OFF dulu.
#
# Skrip ini tidak pernah mematikan atau me-restart Jetson (S18).

set -euo pipefail

# Jalur ini juga tertulis di deploy/systemd/fertimata-edge.service (dicek tests/test_deploy.py).
PREFIX=/opt/fertimata-edge
VENV="$PREFIX/venv"
CONF_DIR=/etc/fertimata
CONF="$CONF_DIR/fertimata.yaml"
DATA_DIR=/var/lib/fertimata
SERVICE_USER=fertimata
SERVICE=fertimata-edge
UDEV_RULES=/etc/udev/rules.d/99-fertimata.rules
JOURNALD_CONF=/etc/systemd/journald.conf.d/fertimata.conf
UNIT=/etc/systemd/system/$SERVICE.service
PYTHON="${PYTHON:-python3.10}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY="$REPO_DIR/deploy"

DRY_RUN=0
RESTART=1

usage() {
    sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --no-restart) RESTART=0 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "Opsi tidak dikenal: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

say() { echo "==> $*"; }
warn() { echo "PERINGATAN: $*" >&2; }

# Semua perubahan sistem lewat `run` supaya --dry-run bisa menampilkan langkahnya.
run() {
    if [ "$DRY_RUN" = 1 ]; then
        echo "+ $(printf '%q ' "$@")"
    else
        "$@"
    fi
}

check_prerequisites() {
    if [ "$DRY_RUN" = 0 ] && [ "$(id -u)" != 0 ]; then
        echo "Jalankan dengan sudo: sudo bash $0 $*" >&2
        exit 1
    fi
    if ! command -v "$PYTHON" > /dev/null 2>&1; then
        echo "$PYTHON tidak ditemukan. Pasang: sudo apt install python3.10 python3.10-venv" >&2
        exit 1
    fi
    if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 10))'; then
        warn "$PYTHON bukan Python 3.10; agent ditulis untuk 3.10 (Ubuntu 22.04)"
    fi
    for file in udev/99-fertimata.rules systemd/$SERVICE.service journald/fertimata.conf; do
        if [ ! -f "$DEPLOY/$file" ]; then
            echo "Berkas deploy/$file tidak ada; jalankan dari salinan repo yang utuh" >&2
            exit 1
        fi
    done
    if command -v dpkg-query > /dev/null 2>&1 \
        && dpkg-query -W -f='${Status}' brltty 2> /dev/null | grep -q "install ok installed"; then
        warn "paket brltty terpasang dan bisa merebut adaptor RS485: sudo apt remove brltty"
    fi
}

setup_user() {
    say "user sistem $SERVICE_USER"
    # Grup gpio tidak selalu ada di image Jetson; dipakai rule udev untuk /dev/gpiochip*.
    for group in dialout gpio video; do
        if ! getent group "$group" > /dev/null 2>&1; then
            run groupadd --system "$group"
        fi
    done
    if ! getent passwd "$SERVICE_USER" > /dev/null 2>&1; then
        run useradd --system --home-dir "$DATA_DIR" --no-create-home \
            --shell /usr/sbin/nologin --user-group "$SERVICE_USER"
    fi
    run usermod --append --groups dialout,gpio,video "$SERVICE_USER"
}

setup_dirs() {
    say "folder data $DATA_DIR dan config $CONF_DIR"
    # Data riset boleh dibaca user lain (peneliti, layanan AI D6), hanya agent yang menulis.
    run install -d -m 0755 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR"
    run install -d -m 0750 -o root -g "$SERVICE_USER" "$CONF_DIR"
    run install -d -m 0755 "$PREFIX"
}

install_package() {
    say "paket Python di $VENV"
    if [ ! -x "$VENV/bin/python" ]; then
        run "$PYTHON" -m venv "$VENV"
    fi
    # Versi dependensi dikunci di pyproject.toml.
    run "$VENV/bin/python" -m pip install --upgrade --quiet "$REPO_DIR"
}

CONFIG_IS_NEW=0

install_config() {
    if [ -f "$CONF" ]; then
        say "config $CONF sudah ada, tidak diubah"
        if ! diff -q <(config_keys "$REPO_DIR/config/fertimata.example.yaml") \
            <(config_keys "$CONF") > /dev/null 2>&1; then
            warn "susunan kunci config contoh berbeda dari $CONF; bandingkan:"
            warn "  diff $REPO_DIR/config/fertimata.example.yaml $CONF"
        fi
        return
    fi
    say "config baru dari contoh → $CONF (ISI dulu nilai lapangan sebelum agent dijalankan)"
    # 0640 root:fertimata: berisi kata sandi MQTT; agent hanya membaca.
    run install -m 0640 -o root -g "$SERVICE_USER" \
        "$REPO_DIR/config/fertimata.example.yaml" "$CONF"
    CONFIG_IS_NEW=1
}

# Nama kunci tingkat atas saja: cukup untuk mengingatkan bila ada bagian config baru di contoh.
config_keys() {
    grep -E '^[a-z_]+:' "$1" | cut -d: -f1 | sort
}

check_config() {
    if [ "$DRY_RUN" = 1 ]; then
        echo "+ $VENV/bin/python -m fertimata_edge --config $CONF --check-config"
        return 0
    fi
    # Dicek sebagai user layanan: sekaligus memastikan izin baca config benar.
    runuser -u "$SERVICE_USER" -- "$VENV/bin/python" -m fertimata_edge \
        --config "$CONF" --check-config
}

install_system_files() {
    say "udev $UDEV_RULES"
    run install -m 0644 "$DEPLOY/udev/99-fertimata.rules" "$UDEV_RULES"
    run udevadm control --reload-rules
    run udevadm trigger --subsystem-match=tty --subsystem-match=video4linux --subsystem-match=gpio

    say "rotasi journald $JOURNALD_CONF"
    run install -d -m 0755 "$(dirname "$JOURNALD_CONF")"
    run install -m 0644 "$DEPLOY/journald/fertimata.conf" "$JOURNALD_CONF"
    run systemctl restart systemd-journald

    say "unit systemd $UNIT"
    run install -m 0644 "$DEPLOY/systemd/$SERVICE.service" "$UNIT"
    run systemctl daemon-reload
    run systemctl enable "$SERVICE"
}

start_service() {
    if [ "$CONFIG_IS_NEW" = 1 ]; then
        say "agent BELUM dijalankan: config masih contoh."
        echo "    Edit $CONF (alamat, register, pin; lihat docs/03-persiapan.md), lalu:"
        echo "    sudo systemctl start $SERVICE"
        return
    fi
    if ! check_config; then
        warn "config ditolak; agent yang sedang berjalan TIDAK di-restart. Perbaiki $CONF lalu:"
        warn "  sudo systemctl restart $SERVICE"
        return 1
    fi
    if [ "$RESTART" = 0 ]; then
        say "--no-restart: versi baru aktif setelah 'sudo systemctl restart $SERVICE'"
        return
    fi
    say "restart $SERVICE (S1: semua relay OFF dulu)"
    run systemctl restart "$SERVICE"
}

main() {
    check_prerequisites "$@"
    setup_user
    setup_dirs
    install_package
    install_config
    install_system_files
    start_service
    say "selesai. Cek: systemctl status $SERVICE; journalctl -u $SERVICE -f; ls -l /dev/rs485"
}

main "$@"

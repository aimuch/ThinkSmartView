#!/bin/sh
# Switch the USB network the initramfs sets up from RNDIS to NCM:
#   initramfs-usb-ncm.sh INITRAMFS
# The device package's deviceinfo asks for rndis.usb0, which macOS can't use; NCM works on macOS,
# Linux and Windows 11 without drivers. A small cpio with the changed etc/deviceinfo is appended:
# the kernel unpacks concatenated archives in order, so the later file replaces the earlier one,
# and the original archive stays byte for byte. Running it again changes nothing.
set -eu
F=$(cd "$(dirname "${1:?initramfs}")" && pwd)/$(basename "$1")
want='deviceinfo_usb_network_function="ncm.usb0"'
# the setting that wins: the last uncommented one in the unpacked stream
last() { gzip -dc "$F" | grep -a -o '^deviceinfo_usb_network_function="[^"]*"' | tail -n 1; }

cur=$(last)
case "$cur" in
    "$want") echo "initramfs already sets up NCM"; exit 0 ;;
    *rndis.usb0*) ;;
    *) echo "unexpected USB network setting in $F: '$cur'"; exit 1 ;;
esac
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir "$T/x"
(cd "$T/x" && gzip -dc "$F" | cpio -id etc/deviceinfo 2>/dev/null) || true
[ -s "$T/x/etc/deviceinfo" ] || { echo "no etc/deviceinfo in $F"; exit 1; }
sed -i "s/^deviceinfo_usb_network_function=.*/$want/" "$T/x/etc/deviceinfo"
(cd "$T/x" && printf 'etc\netc/deviceinfo\n' | cpio -o -H newc -R 0:0 2>/dev/null | gzip -9n) >> "$F"
[ "$(last)" = "$want" ] || { echo "the appended deviceinfo did not take"; exit 1; }
echo "initramfs now sets up NCM"

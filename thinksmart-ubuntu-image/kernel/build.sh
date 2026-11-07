#!/bin/bash
# Mirrors pmaports ed3f995f linux-postmarketos-qcom-msm8953 build()
set -eo pipefail
cd /build
[ -d linux-6.19.5-r0 ] || tar xzf /in/linux-postmarketos-qcom-msm8953-v6.19.5-r0.tar.gz
cd linux-6.19.5-r0
cp /in/config-postmarketos-qcom-msm8953.aarch64 .config
unset LDFLAGS
make ARCH=arm64 LLVM=1 KBUILD_BUILD_VERSION="1-postmarketos-qcom-msm8953" -j"$(nproc)" 2>&1 | tail -20
rm -rf /out/*
cp arch/arm64/boot/Image.gz /out/vmlinuz
cp System.map /out/System.map
cp .config /out/config
cp include/config/kernel.release /out/kernel.release
make ARCH=arm64 LLVM=1 dtbs_install INSTALL_DTBS_PATH=/out/dtbs >/dev/null
echo BUILD-DONE

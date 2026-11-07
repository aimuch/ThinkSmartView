#!/bin/bash
# Rebuild Ubuntu 24.04's mesa 25.2.8 for arm64 with the freedreno GMEM fix, inside an
# ubuntu:24.04 arm64 container (colima on Apple Silicon). Output: /build/*.deb
set -euxo pipefail
VER=25.2.8-0ubuntu0.24.04.2
export DEBIAN_FRONTEND=noninteractive DEBEMAIL="thinksmart@localhost" DEBFULLNAME="ThinkSmart build"
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
apt-get update
apt-get install -y --no-install-recommends dpkg-dev devscripts equivs quilt ca-certificates
mkdir -p /build && cd /build
apt-get source "mesa=$VER"
cd mesa-25.2.8
QUILT_PATCHES=debian/patches quilt import -P thinksmart-freedreno-gmem-fix.patch /freedreno-gmem-fix.patch
# backports from Mesa 26.1 (merge request 40352): without them two threads drawing with GL
# (WebKit's compositor + painting) livelock in alloc_batch_locked() once all 32 batch slots
# are taken and the oldest one is an already-flushed batch that is still referenced
QUILT_PATCHES=debian/patches quilt import -P upstream-b587181f-check-for-flushed-batches.patch /upstream-b587181f-check-for-flushed-batches.patch
QUILT_PATCHES=debian/patches quilt import -P upstream-f5d40636-fix-bo-flush-race.patch /upstream-f5d40636-fix-bo-flush-race.patch
# a5xx: HLSQ_CONTROL_3 got the linear centroid regid in the perspective centroid field (upstream
# 2026-10, merge request 44343). Hiding GL_NV_shader_noperspective_interpolation (thinksmart3) was
# dropped: it did not stop the blurred-shadow residue, see UPSTREAM-REPORT.md section five.
QUILT_PATCHES=debian/patches quilt import -P upstream-dc305f9b-a5xx-linear-centroid-regid.patch /upstream-dc305f9b-a5xx-linear-centroid-regid.patch
QUILT_PATCHES=debian/patches quilt push -a
dch -v "$VER+thinksmart4" -D noble "freedreno: don't count MSAA samples twice for depth/stencil in GMEM, and fall back to sysmem instead of looping forever when even the smallest bins don't fit (WebKit/Skia hang on Adreno 506). Backport upstream b587181f (skip flushed batches and other contexts' batches when the batch cache is full) and f5d40636 (bo_flush race), fixing a WebKit GPU-mode freeze, and dc305f9b (a5xx linear centroid interpolator regid)."
mk-build-deps -i -r -t "apt-get -y --no-install-recommends" debian/control
DEB_BUILD_OPTIONS="nocheck parallel=$(nproc)" dpkg-buildpackage -b -uc -us
ls -la /build/*.deb

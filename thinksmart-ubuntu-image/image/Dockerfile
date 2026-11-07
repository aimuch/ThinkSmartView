# Tools for building the release image on any host: docker build -t thinksmart-image image/
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        e2fsprogs fdisk parted xz-utils zstd libcap2-bin attr ca-certificates file git cpio \
    && rm -rf /var/lib/apt/lists/*

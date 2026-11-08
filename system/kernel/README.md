# Kernel build environment

The image runs the postmarketOS kernel `linux-postmarketos-qcom-msm8953` 6.19.5-r0, unchanged. These files rebuild
that kernel tree, which `codec-dmic/build.sh` needs to build the patched codec module and device tree (the fix for
the second microphone) with the same vermagic:

- `Dockerfile`: Alpine edge with clang 21, on an image imported from the Alpine minirootfs
  (`alpine-minirootfs-3.22.7-aarch64.tar.gz.sha256`), tagged `alpine-base:3.22.7`; build it as `kbuild:clang21`.
- `build.sh`: mirrors the pmaports `build()` of that package (commit ed3f995f). It expects the kernel source tarball
  `linux-postmarketos-qcom-msm8953-v6.19.5-r0.tar.gz` and `config-postmarketos-qcom-msm8953.aarch64` in `/in`, keeps
  the tree in `/build` (the docker volume `kbuild-src`) and writes the kernel and device trees to `/out`.
- `config-postmarketos-qcom-msm8953.aarch64`: the package's kernel config.

Kernel source: https://gitlab.postmarketos.org/postmarketOS/pmaports (device/community/linux-postmarketos-qcom-msm8953).

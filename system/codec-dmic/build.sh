#!/bin/bash
# Build the patched msm8916-wcd-digital module and the CD-18781Y DTB against the kernel build
# tree in the docker volume kbuild-src (made by ../kernel/build.sh; mounted read-only, nothing in it
# changes). The module is built out of tree, so it carries the same vermagic as the running
# kernel. Output: out/snd-soc-msm8916-digital.ko, out/apq8053-lenovo-cd-18781y.dtb
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p out
docker run --rm -v kbuild-src:/build:ro -v "$PWD":/w kbuild:clang21 bash -euc '
K=/build/linux-6.19.5-r0
T=$(mktemp -d)
mkdir -p $T/sound/soc/codecs $T/arch/arm64/boot/dts/qcom $T/Documentation/devicetree/bindings/sound
cp $K/sound/soc/codecs/msm8916-wcd-digital.c $T/sound/soc/codecs/
cp $K/arch/arm64/boot/dts/qcom/apq8053-lenovo-cd-18781y.dts $T/arch/arm64/boot/dts/qcom/
cp $K/Documentation/devicetree/bindings/sound/qcom,msm8916-wcd-digital-codec.yaml $T/Documentation/devicetree/bindings/sound/
(cd $T && patch -s -p1 < /w/msm8916-wcd-digital-dmic-clock.patch)

M=$T/mod; mkdir -p $M
cp $T/sound/soc/codecs/msm8916-wcd-digital.c $M/
printf "obj-m += snd-soc-msm8916-digital.o\nsnd-soc-msm8916-digital-y := msm8916-wcd-digital.o\n" > $M/Kbuild
make -s -C $K ARCH=arm64 LLVM=1 M=$M modules
llvm-strip --strip-debug -o /w/out/snd-soc-msm8916-digital.ko $M/snd-soc-msm8916-digital.ko

dtb() {   # dtb SRC.dts OUT.dtb, the way the kernel build does it
    clang -E -nostdinc -I$K/include -I$K/arch/arm64/boot/dts -I$K/arch/arm64/boot/dts/qcom \
        -I$K/scripts/dtc/include-prefixes -undef -D__DTS__ -x assembler-with-cpp -P -o $T/pre.dts "$1"
    $K/scripts/dtc/dtc -q -O dtb -o "$2" -b 0 -i $(dirname "$1") -i $K/arch/arm64/boot/dts/qcom $T/pre.dts
}
dtb $K/arch/arm64/boot/dts/qcom/apq8053-lenovo-cd-18781y.dts /w/out/stock-rebuilt.dtb
dtb $T/arch/arm64/boot/dts/qcom/apq8053-lenovo-cd-18781y.dts /w/out/apq8053-lenovo-cd-18781y.dtb
'
ls -l out

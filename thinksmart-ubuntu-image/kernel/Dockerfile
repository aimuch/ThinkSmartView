# Build environment matching the original kernel: Alpine edge, clang 21.1.8
FROM alpine-base:3.22.7
RUN printf 'https://mirrors.aliyun.com/alpine/edge/main\nhttps://mirrors.aliyun.com/alpine/edge/community\n' > /etc/apk/repositories \
 && apk upgrade --no-cache --available \
 && apk add --no-cache clang21 lld21 llvm21 make bison flex perl python3 \
      openssl-dev findutils bash gzip coreutils diffutils e2fsprogs \
      linux-headers elfutils-dev xz tar build-base
ENV PATH=/usr/lib/llvm21/bin:$PATH

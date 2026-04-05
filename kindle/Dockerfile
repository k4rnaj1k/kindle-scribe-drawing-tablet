FROM ubuntu:22.04 AS toolchain

ARG TARGET=kindlehf
ARG DEBIAN_FRONTEND=noninteractive

# Toolchain + SDK + meson dependencies
RUN apt-get update -q && apt-get install -y -q \
    build-essential autoconf automake bison flex gawk \
    libtool libtool-bin libncurses-dev \
    curl file git gperf help2man texinfo unzip wget \
    libarchive-dev nettle-dev \
    meson libgtk2.0-dev libxrandr-dev \
    pkg-config python3 sudo \
    && rm -rf /var/lib/apt/lists/*

# crosstool-NG refuses to run as root
RUN useradd -m builder && echo "builder ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER builder
WORKDIR /home/builder

# Build koxtoolchain (must not be root)
RUN git clone --recursive --depth=1 https://github.com/koreader/koxtoolchain.git /home/builder/koxtoolchain \
    && cd /home/builder/koxtoolchain \
    && chmod +x ./gen-tc.sh \
    && ./gen-tc.sh ${TARGET}

# kindle-sdk needs mount privileges, so clone now but build in next stage
RUN git clone --recursive --depth=1 https://github.com/KindleModding/kindle-sdk.git /home/builder/kindle-sdk

# ---- Second stage: build SDK with mount privileges ----
FROM toolchain AS sdk

ARG TARGET=kindlehf

# 1. Set the PATH *before* running the script so the toolchain is discoverable
ENV MESON_CROSS=/home/builder/x-tools/arm-${TARGET}-linux-gnueabihf/meson-crosscompile.txt
ENV PATH="/home/builder/x-tools/arm-${TARGET}-linux-gnueabihf/bin:${PATH}"

# 2. Stay as the builder user. The script will use sudo internally for mounting.
USER builder
WORKDIR /home/builder/kindle-sdk

RUN chmod +x ./gen-sdk.sh \
    && ./gen-sdk.sh ${TARGET}

# Copy XRandR headers from the host into the cross-compilation sysroot.
# libXrandr.so is already present in the sysroot (part of the Kindle X11 stack);
# only the architecture-neutral headers are missing.
RUN SYSROOT="/home/builder/x-tools/arm-${TARGET}-linux-gnueabihf/arm-${TARGET}-linux-gnueabihf/sysroot" && \
    sudo apt-get install -y -q libxrandr-dev 2>/dev/null || true && \
    mkdir -p "$SYSROOT/usr/include/X11/extensions" && \
    cp /usr/include/X11/extensions/Xrandr.h  "$SYSROOT/usr/include/X11/extensions/" && \
    cp /usr/include/X11/extensions/randr.h   "$SYSROOT/usr/include/X11/extensions/"

WORKDIR /src
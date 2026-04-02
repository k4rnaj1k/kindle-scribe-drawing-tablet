# Makefile for tablet-daemon (Kindle Scribe, ARMv7 32-bit)
#
# Default build uses Docker (works on macOS/Linux without cross-compiler):
#   make
#
# Native cross-compile (if you have arm-linux-gnueabihf-gcc installed):
#   make native
#
# Clean:
#   make clean

CFLAGS = -Wall -Wextra -Os -std=c99 -D_GNU_SOURCE
LDFLAGS = -static
TARGET = bin/tablet-daemon
SRC = bin/tablet-daemon.c

.PHONY: all clean native

all: $(TARGET)

$(TARGET): $(SRC)
	docker run --rm --platform linux/arm64 -v "$(CURDIR)":/src -w /src \
		ubuntu:22.04 \
		sh -c "apt-get update -q && apt-get install -y -q gcc-arm-linux-gnueabihf binutils-arm-linux-gnueabihf && arm-linux-gnueabihf-gcc $(CFLAGS) $(LDFLAGS) -o $(TARGET) $(SRC) && arm-linux-gnueabihf-strip $(TARGET)"

native: $(SRC)
	arm-linux-gnueabihf-gcc $(CFLAGS) $(LDFLAGS) -o $(TARGET) $(SRC)
	arm-linux-gnueabihf-strip $(TARGET)

clean:
	rm -f $(TARGET)

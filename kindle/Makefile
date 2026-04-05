# Makefile for kindle-tablet Kindle-side binaries
#
# Build the Docker image (one-time, ~30min):
#   make docker-image
#
# gen-sdk.sh needs loopback mounts, which Docker build doesn't allow.
# docker-image works around this by:
#   1. building only the toolchain stage
#   2. running gen-sdk.sh in a --privileged container
#   3. committing the result as kindle-sdk-final
#
# Build everything:
#   make all
#
# Build individually:
#   make gtk-ui
#
# Native build (if you have koxtoolchain installed locally):
#   make gtk-ui-native MESON_CROSS=<path-to-meson-crosscompile.txt>
#
# Clean:
#   make clean

DOCKER_IMAGE = kindle-sdk-final
TARGET ?= kindlehf
UI_TARGET = bin/tablet-ui
MESON_CROSS ?=

.PHONY: all clean docker-image gtk-ui gtk-ui-native

all: gtk-ui

docker-image:
	docker build --target toolchain -t kindle-toolchain .
	docker rm -f sdk-builder 2>/dev/null || true
	docker run --privileged --name sdk-builder -u builder kindle-toolchain \
		/bin/sh -c "cd ~/kindle-sdk && ./gen-sdk.sh $(TARGET)"
	docker commit sdk-builder $(DOCKER_IMAGE)
	docker rm sdk-builder

gtk-ui: gtk-ui/src/main.cpp gtk-ui/meson.build
	docker run --rm -v "$(CURDIR)":/src $(DOCKER_IMAGE) \
		sh -c "cd /src/gtk-ui && rm -rf builddir_kindle && meson setup --cross-file \$$MESON_CROSS builddir_kindle && meson compile -C builddir_kindle && cp builddir_kindle/tablet-ui /src/$(UI_TARGET)"

gtk-ui-native: gtk-ui/src/main.cpp gtk-ui/meson.build
	@if [ -z "$(MESON_CROSS)" ]; then \
		echo "Error: MESON_CROSS not set. Usage: make gtk-ui-native MESON_CROSS=<path>"; \
		exit 1; \
	fi
	cd gtk-ui && meson setup --cross-file $(MESON_CROSS) builddir_kindle 2>/dev/null || true
	meson compile -C gtk-ui/builddir_kindle
	cp gtk-ui/builddir_kindle/tablet-ui $(UI_TARGET)

clean:
	rm -f $(UI_TARGET)
	rm -rf gtk-ui/builddir_kindle gtk-ui/builddir

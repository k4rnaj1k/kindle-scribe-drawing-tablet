# Kindle Scribe tablet

This is genuinely slop resulting of me screaming at multiple LLMs. I am sure it can be improved a lot. It is a buggy mess right now.
Any contributions are welcome.

Roadmap:
- [ ] fix buttons on tablet
- [ ] add support for eraser events

The way this works:
1. reads via ssh data from kindle's pen input
2. transforms that input into events

Tested on MacOS at the moment

How to use:
1. Launch ssh server in Koreader
2. `./deploy.sh kindle-ip`
3. `source .venv/bin/activate`
4. `kindle-tablet --host kindle-ip`

## On windows:
Requires [vmulti-bin](https://github.com/X9VoiD/vmulti-bin) to be installed(gotta check if possible without it).

## Building notes:
```
make docker-image
docker build --target toolchain -t kindle-toolchain .
docker run --privileged --name sdk-builder -u builder kindle-toolchain /bin/sh -c "cd ~/kindle-sdk && ./gen-sdk.sh kindlehf"
docker commit sdk-builder kindle-sdk-final
```

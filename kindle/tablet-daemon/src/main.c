/*
 * tablet-daemon - stream raw Linux input events over TCP
 *
 * Usage: tablet-daemon <device> <port>
 *
 * Opens the given evdev device, listens on the given TCP port, and streams
 * raw bytes to one connected client at a time.  When the client disconnects
 * the server loops back and waits for a new connection.
 *
 * The host-side connector reads these bytes and parses them as struct
 * input_event (16 bytes on 32-bit ARM).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>

#define BUF_SIZE 4096

int main(int argc, char *argv[])
{
    if (argc != 3) {
        fprintf(stderr, "Usage: %s <device> <port>\n", argv[0]);
        return 1;
    }

    const char *device = argv[1];
    int port = atoi(argv[2]);
    if (port <= 0 || port > 65535) {
        fprintf(stderr, "Invalid port: %s\n", argv[2]);
        return 1;
    }

    /* Ignore SIGPIPE so a broken client connection doesn't kill the daemon */
    signal(SIGPIPE, SIG_IGN);

    /* Open input device */
    int dev_fd = open(device, O_RDONLY);
    if (dev_fd < 0) {
        fprintf(stderr, "Failed to open device %s: %s\n", device, strerror(errno));
        return 1;
    }

    /* Create TCP listening socket */
    int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) {
        perror("socket");
        return 1;
    }

    int opt = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port        = htons((unsigned short)port);

    if (bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("bind");
        return 1;
    }

    if (listen(listen_fd, 1) < 0) {
        perror("listen");
        return 1;
    }

    fprintf(stderr, "tablet-daemon: listening on port %d, streaming %s\n",
            port, device);

    unsigned char buf[BUF_SIZE];

    for (;;) {
        /* Wait for a client */
        int client_fd = accept(listen_fd, NULL, NULL);
        if (client_fd < 0) {
            perror("accept");
            continue;
        }

        /* Disable Nagle so each event is sent immediately */
        int nodelay = 1;
        setsockopt(client_fd, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));

        fprintf(stderr, "tablet-daemon: client connected\n");

        /* Stream device bytes to client */
        for (;;) {
            ssize_t n = read(dev_fd, buf, sizeof(buf));
            if (n <= 0) {
                /* Device error or closed — fatal */
                fprintf(stderr, "tablet-daemon: device read error: %s\n",
                        strerror(errno));
                goto done;
            }

            ssize_t sent = 0;
            while (sent < n) {
                ssize_t w = write(client_fd, buf + sent, (size_t)(n - sent));
                if (w <= 0)
                    goto next_client;  /* client disconnected */
                sent += w;
            }
        }

    next_client:
        fprintf(stderr, "tablet-daemon: client disconnected\n");
        close(client_fd);
    }

done:
    close(listen_fd);
    close(dev_fd);
    return 1;
}

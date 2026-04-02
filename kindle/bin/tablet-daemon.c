/*
 * tablet-daemon.c - Kindle Scribe tablet input daemon
 *
 * Reads pen and touch input from evdev devices, applies rotation transforms,
 * detects exit/rotate button taps on touch, and writes processed events to
 * stdout for the host to read over SSH.
 *
 * Replaces tablet-server.sh and exit-monitor.sh with a single efficient
 * C program using poll() for multiplexing.
 *
 * Usage: tablet-daemon [--pen-device PATH] [--touch-device PATH]
 *                      [--fbink PATH] [--no-ui]
 *
 * Build: arm-linux-gnueabihf-gcc -static -Os -Wall -o tablet-daemon tablet-daemon.c
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <errno.h>
#include <dirent.h>
#include <poll.h>
#include <getopt.h>

/* Input event struct for 32-bit ARM (Kindle Scribe) */
struct input_event {
    uint32_t tv_sec;
    uint32_t tv_usec;
    uint16_t type;
    uint16_t code;
    int32_t  value;
};

/* Linux input event types */
#define EV_SYN  0x00
#define EV_KEY  0x01
#define EV_ABS  0x03

/* Absolute axis codes */
#define ABS_X           0x00
#define ABS_Y           0x01
#define ABS_PRESSURE    0x18
#define ABS_TILT_X      0x1a
#define ABS_TILT_Y      0x1b

/* Multi-touch codes */
#define ABS_MT_SLOT         0x2f
#define ABS_MT_TOUCH_MAJOR  0x30
#define ABS_MT_POSITION_X   0x35
#define ABS_MT_POSITION_Y   0x36
#define ABS_MT_TRACKING_ID  0x39

/* Key codes */
#define BTN_TOUCH       0x14a
#define BTN_TOOL_PEN    0x140
#define BTN_TOOL_RUBBER 0x141
#define BTN_STYLUS      0x14b
#define BTN_STYLUS2     0x14c

/* Sync codes */
#define SYN_REPORT  0x00

/* Control message type (sent inline, never produced by kernel) */
#define EV_CONTROL      0xFF
#define CTRL_ROTATION   0x01
#define CTRL_DISCONNECT 0x02

/* Button zone thresholds (percentage of touch range) */
#define BUTTON_Y_START_PCT  85  /* buttons occupy bottom 15% */
#define BUTTON_X_MID_PCT    50  /* left half = exit, right half = rotate */

/* Maximum events in one frame (between SYN_REPORTs) */
#define MAX_FRAME_EVENTS 64

/* Maximum touch slots */
#define MAX_TOUCH_SLOTS 10

/* Rotation states */
#define ROTATION_PORTRAIT   0
#define ROTATION_LANDSCAPE  90

/* ---- Global state -------------------------------------------------------- */

static volatile int g_running = 1;

struct axis_info {
    int min;
    int max;
};

struct touch_slot {
    int x;
    int y;
    int tracking_id;
    int active;
};

/* Button tap state machine */
#define ZONE_NONE   0
#define ZONE_EXIT   1
#define ZONE_ROTATE 2

struct button_state {
    int zone;           /* which zone the finger went down in */
    int finger_down;    /* is finger currently down? */
};

struct daemon_state {
    int pen_fd;
    int touch_fd;

    /* Pen axis info */
    struct axis_info pen_x;
    struct axis_info pen_y;
    struct axis_info pen_pressure;

    /* Touch axis info */
    struct axis_info touch_x;
    struct axis_info touch_y;

    /* Current rotation */
    int rotation;

    /* Touch tracking */
    struct touch_slot touch_slots[MAX_TOUCH_SLOTS];
    int current_slot;
    int touch_finger_count;

    /* Button tap detection */
    struct button_state btn;

    /* Pen frame buffer (accumulate until SYN_REPORT) */
    struct input_event pen_frame[MAX_FRAME_EVENTS];
    int pen_frame_len;

    /* Touch frame buffer */
    struct input_event touch_frame[MAX_FRAME_EVENTS];
    int touch_frame_len;

    /* Last known pen X/Y for rotation (need both axes even if only one changes) */
    int last_pen_x;
    int last_pen_y;

    /* FBInk path */
    char fbink_path[256];
};

/* ---- Signal handler ------------------------------------------------------ */

static void sig_handler(int sig)
{
    (void)sig;
    g_running = 0;
}

/* ---- sysfs helpers ------------------------------------------------------- */

static int read_sysfs_int(const char *path)
{
    FILE *f = fopen(path, "r");
    int val = 0;
    if (f) {
        if (fscanf(f, "%d", &val) != 1)
            val = 0;
        fclose(f);
    }
    return val;
}

static int read_sysfs_string(const char *path, char *buf, int buflen)
{
    FILE *f = fopen(path, "r");
    if (!f)
        return -1;
    if (!fgets(buf, buflen, f)) {
        fclose(f);
        return -1;
    }
    fclose(f);
    /* Strip trailing newline */
    int len = strlen(buf);
    if (len > 0 && buf[len - 1] == '\n')
        buf[len - 1] = '\0';
    return 0;
}

/* Case-insensitive substring search (portable replacement for strcasestr) */
static const char *ci_strstr(const char *haystack, const char *needle)
{
    if (!*needle)
        return haystack;
    for (; *haystack; haystack++) {
        const char *h = haystack, *n = needle;
        while (*h && *n) {
            char hc = *h, nc = *n;
            if (hc >= 'A' && hc <= 'Z') hc += 32;
            if (nc >= 'A' && nc <= 'Z') nc += 32;
            if (hc != nc) break;
            h++;
            n++;
        }
        if (!*n)
            return haystack;
    }
    return NULL;
}

/* ---- Device detection ---------------------------------------------------- */

static int detect_device(const char *keywords[], int nkeywords, char *out_path, int pathlen)
{
    DIR *dir = opendir("/sys/class/input");
    if (!dir)
        return -1;

    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (strncmp(ent->d_name, "event", 5) != 0)
            continue;

        char name_path[512];
        snprintf(name_path, sizeof(name_path),
                 "/sys/class/input/%s/device/name", ent->d_name);

        char name[256];
        if (read_sysfs_string(name_path, name, sizeof(name)) < 0)
            continue;

        for (int i = 0; i < nkeywords; i++) {
            if (ci_strstr(name, keywords[i])) {
                snprintf(out_path, pathlen, "/dev/input/%s", ent->d_name);
                closedir(dir);
                fprintf(stderr, "Detected device: %s -> %s\n", out_path, name);
                return 0;
            }
        }
    }
    closedir(dir);
    return -1;
}

static int detect_pen_device(char *out_path, int pathlen)
{
    const char *keywords[] = {"wacom", "stylus", "pen", "digitizer", "ntx_event"};
    return detect_device(keywords, 5, out_path, pathlen);
}

static int detect_touch_device(char *out_path, int pathlen)
{
    const char *keywords[] = {"touch", "cyttsp", "capacitive", "finger", "_mt", "pt_mt"};
    return detect_device(keywords, 6, out_path, pathlen);
}

/* Read axis limits from sysfs */
static void read_axis_info(const char *evname, int abs_code, struct axis_info *info)
{
    char path[512];

    snprintf(path, sizeof(path),
             "/sys/class/input/%s/device/abs_%02x/min", evname, abs_code);
    info->min = read_sysfs_int(path);

    snprintf(path, sizeof(path),
             "/sys/class/input/%s/device/abs_%02x/max", evname, abs_code);
    info->max = read_sysfs_int(path);
}

static void read_device_caps(struct daemon_state *st, const char *pen_path, const char *touch_path)
{
    /* Extract event name from path (e.g., "event1" from "/dev/input/event1") */
    const char *pen_ev = strrchr(pen_path, '/');
    pen_ev = pen_ev ? pen_ev + 1 : pen_path;

    read_axis_info(pen_ev, ABS_X, &st->pen_x);
    read_axis_info(pen_ev, ABS_Y, &st->pen_y);
    read_axis_info(pen_ev, ABS_PRESSURE, &st->pen_pressure);

    fprintf(stderr, "Pen caps: X=[%d,%d] Y=[%d,%d] P=[%d,%d]\n",
            st->pen_x.min, st->pen_x.max,
            st->pen_y.min, st->pen_y.max,
            st->pen_pressure.min, st->pen_pressure.max);

    if (touch_path && touch_path[0]) {
        const char *touch_ev = strrchr(touch_path, '/');
        touch_ev = touch_ev ? touch_ev + 1 : touch_path;

        read_axis_info(touch_ev, ABS_MT_POSITION_X, &st->touch_x);
        read_axis_info(touch_ev, ABS_MT_POSITION_Y, &st->touch_y);

        /* Fallback: if MT axes not found, try single-touch axes */
        if (st->touch_x.max == 0) {
            read_axis_info(touch_ev, ABS_X, &st->touch_x);
            read_axis_info(touch_ev, ABS_Y, &st->touch_y);
        }

        fprintf(stderr, "Touch caps: X=[%d,%d] Y=[%d,%d]\n",
                st->touch_x.min, st->touch_x.max,
                st->touch_y.min, st->touch_y.max);
    }
}

/* ---- UI drawing ---------------------------------------------------------- */

static void draw_ui(struct daemon_state *st)
{
    char cmd[512];
    const char *orient = (st->rotation == ROTATION_LANDSCAPE) ? "LANDSCAPE" : "PORTRAIT";

    /* Try FBInk first if the binary exists.
     * Redirect stdout to /dev/null -- child processes must not write to our
     * stdout which carries the binary event stream to the host. */
    if (access(st->fbink_path, X_OK) == 0) {
        snprintf(cmd, sizeof(cmd), "%s -c >/dev/null 2>&1", st->fbink_path);
        system(cmd);
        snprintf(cmd, sizeof(cmd), "%s -pm -y 2 'KINDLE TABLET MODE' >/dev/null 2>&1", st->fbink_path);
        system(cmd);
        snprintf(cmd, sizeof(cmd), "%s -pm -y 5 'Orientation: %s' >/dev/null 2>&1", st->fbink_path, orient);
        system(cmd);
        snprintf(cmd, sizeof(cmd), "%s -pm -y 8 'Draw on your computer.' >/dev/null 2>&1", st->fbink_path);
        system(cmd);
        snprintf(cmd, sizeof(cmd), "%s -pm -y 9 'Pen does NOT draw here.' >/dev/null 2>&1", st->fbink_path);
        system(cmd);
        snprintf(cmd, sizeof(cmd), "%s -pm -y -4 '[ EXIT ]          [ ROTATE ]' >/dev/null 2>&1", st->fbink_path);
        system(cmd);
        return;
    }

    /* Fall back to eips -- always available on Kindle */
    /* IMPORTANT: redirect stdout to /dev/null so eips doesn't corrupt the
     * binary event stream that flows over our stdout to the host. */
    system("eips -c >/dev/null 2>&1");
    sleep(1); /* eips needs a moment after clear on e-ink */
    system("eips 10 2 'KINDLE TABLET MODE' >/dev/null 2>&1");
    snprintf(cmd, sizeof(cmd), "eips 5 5 'Orientation: %s' >/dev/null 2>&1", orient);
    system(cmd);
    system("eips 5 8 'Draw on your computer.' >/dev/null 2>&1");
    system("eips 5 9 'Pen does NOT draw here.' >/dev/null 2>&1");
    /* Buttons at bottom: eips col row -- ~row 34 on Scribe portrait */
    system("eips 2 34 '[ EXIT ]' >/dev/null 2>&1");
    system("eips 35 34 '[ ROTATE ]' >/dev/null 2>&1");
}

/* ---- Control messages ---------------------------------------------------- */

static void send_control(uint16_t code, int32_t value)
{
    struct input_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = EV_CONTROL;
    ev.code = code;
    ev.value = value;
    write(STDOUT_FILENO, &ev, sizeof(ev));
}

/* ---- Button zone detection ----------------------------------------------- */

static int check_button_zone(struct daemon_state *st, int touch_x, int touch_y)
{
    int x_max = st->touch_x.max;
    int y_max = st->touch_y.max;

    if (x_max == 0 || y_max == 0)
        return ZONE_NONE;

    if (st->rotation == ROTATION_PORTRAIT) {
        /* Buttons at bottom of screen in portrait */
        int y_threshold = y_max * BUTTON_Y_START_PCT / 100;
        int x_mid = x_max * BUTTON_X_MID_PCT / 100;

        if (touch_y >= y_threshold) {
            if (touch_x < x_mid)
                return ZONE_EXIT;
            else
                return ZONE_ROTATE;
        }
    } else {
        /* Landscape: device rotated 90deg CW. Native touch coords unchanged.
         * Physical "bottom" in landscape = RIGHT edge in native portrait coords.
         * Physical "left" in landscape = TOP in native portrait coords. */
        int x_threshold = x_max * BUTTON_Y_START_PCT / 100;
        int y_mid = y_max * BUTTON_X_MID_PCT / 100;

        if (touch_x >= x_threshold) {
            if (touch_y < y_mid)
                return ZONE_EXIT;
            else
                return ZONE_ROTATE;
        }
    }

    return ZONE_NONE;
}

/* ---- Rotation transform -------------------------------------------------- */

static void apply_rotation(struct daemon_state *st)
{
    if (st->rotation == ROTATION_PORTRAIT) {
        /* No transform needed */
        return;
    }

    /* Landscape (90deg CW): new_x = y, new_y = max_x - x */
    int raw_x = st->last_pen_x;
    int raw_y = st->last_pen_y;
    int pen_x_max = st->pen_x.max;

    int rot_x = raw_y;
    int rot_y = pen_x_max - raw_x;

    /* Rewrite ABS_X and ABS_Y events in the frame buffer */
    for (int i = 0; i < st->pen_frame_len; i++) {
        struct input_event *ev = &st->pen_frame[i];
        if (ev->type == EV_ABS) {
            if (ev->code == ABS_X)
                ev->value = rot_x;
            else if (ev->code == ABS_Y)
                ev->value = rot_y;
        }
    }
}

/* ---- Pen event processing ------------------------------------------------ */

static void process_pen_event(struct daemon_state *st, struct input_event *ev)
{
    if (st->pen_frame_len >= MAX_FRAME_EVENTS)
        return; /* Safety: drop events if frame too large */

    /* Track last known pen position for rotation */
    if (ev->type == EV_ABS) {
        if (ev->code == ABS_X)
            st->last_pen_x = ev->value;
        else if (ev->code == ABS_Y)
            st->last_pen_y = ev->value;
    }

    if (ev->type == EV_SYN && ev->code == SYN_REPORT) {
        /* Add the SYN_REPORT to the frame */
        st->pen_frame[st->pen_frame_len++] = *ev;

        /* Apply rotation transform to buffered frame */
        apply_rotation(st);

        /* Write entire frame atomically to stdout */
        write(STDOUT_FILENO, st->pen_frame,
              st->pen_frame_len * sizeof(struct input_event));

        st->pen_frame_len = 0;
    } else {
        /* Buffer the event */
        st->pen_frame[st->pen_frame_len++] = *ev;
    }
}

/* ---- Touch event processing ---------------------------------------------- */

static void update_touch_state(struct daemon_state *st, struct input_event *ev)
{
    if (ev->type != EV_ABS)
        return;

    if (ev->code == ABS_MT_SLOT) {
        st->current_slot = ev->value;
        if (st->current_slot < 0 || st->current_slot >= MAX_TOUCH_SLOTS)
            st->current_slot = 0;
    } else if (ev->code == ABS_MT_TRACKING_ID) {
        struct touch_slot *slot = &st->touch_slots[st->current_slot];
        slot->tracking_id = ev->value;
        slot->active = (ev->value >= 0);
    } else if (ev->code == ABS_MT_POSITION_X) {
        st->touch_slots[st->current_slot].x = ev->value;
    } else if (ev->code == ABS_MT_POSITION_Y) {
        st->touch_slots[st->current_slot].y = ev->value;
    }
}

static int count_active_touches(struct daemon_state *st)
{
    int count = 0;
    for (int i = 0; i < MAX_TOUCH_SLOTS; i++) {
        if (st->touch_slots[i].active)
            count++;
    }
    return count;
}

static int get_single_touch_slot(struct daemon_state *st)
{
    for (int i = 0; i < MAX_TOUCH_SLOTS; i++) {
        if (st->touch_slots[i].active)
            return i;
    }
    return -1;
}

static void handle_button_action(struct daemon_state *st, int zone)
{
    if (zone == ZONE_EXIT) {
        fprintf(stderr, "Exit button tapped\n");
        send_control(CTRL_DISCONNECT, 0);
        /* Remove marker so tablet-mode.sh thaws the framework */
        unlink("/tmp/tablet-mode-active");
        g_running = 0;
    } else if (zone == ZONE_ROTATE) {
        fprintf(stderr, "Rotate button tapped\n");
        if (st->rotation == ROTATION_PORTRAIT)
            st->rotation = ROTATION_LANDSCAPE;
        else
            st->rotation = ROTATION_PORTRAIT;

        send_control(CTRL_ROTATION, st->rotation);
        draw_ui(st);
    }
}

static void process_touch_event(struct daemon_state *st, struct input_event *ev)
{
    /* Update internal touch tracking */
    update_touch_state(st, ev);

    if (st->touch_frame_len >= MAX_FRAME_EVENTS)
        return;

    if (ev->type == EV_SYN && ev->code == SYN_REPORT) {
        /* Add SYN to frame */
        st->touch_frame[st->touch_frame_len++] = *ev;

        int active = count_active_touches(st);

        if (active == 1 && !st->btn.finger_down) {
            /* Single finger just went down -- check if it's in a button zone */
            int slot_idx = get_single_touch_slot(st);
            if (slot_idx >= 0) {
                struct touch_slot *slot = &st->touch_slots[slot_idx];
                int zone = check_button_zone(st, slot->x, slot->y);
                if (zone != ZONE_NONE) {
                    st->btn.zone = zone;
                    st->btn.finger_down = 1;
                    /* Consume this frame -- don't forward to host */
                    st->touch_frame_len = 0;
                    return;
                }
            }
        }

        if (st->btn.finger_down) {
            /* We're in a button tap sequence */
            if (active == 0) {
                /* Finger lifted -- check if still in the same zone */
                /* Use the last known position from the slot that was active */
                handle_button_action(st, st->btn.zone);
                st->btn.finger_down = 0;
                st->btn.zone = ZONE_NONE;
            }
            /* Either way, consume the frame during button interaction */
            st->touch_frame_len = 0;
            return;
        }

        /* Not a button tap -- forward the frame to host */
        write(STDOUT_FILENO, st->touch_frame,
              st->touch_frame_len * sizeof(struct input_event));

        st->touch_frame_len = 0;
    } else {
        st->touch_frame[st->touch_frame_len++] = *ev;
    }
}

/* ---- Main ---------------------------------------------------------------- */

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [OPTIONS]\n"
        "  --pen-device PATH    Pen evdev device (default: auto-detect)\n"
        "  --touch-device PATH  Touch evdev device (default: auto-detect)\n"
        "  --fbink PATH         FBInk binary path (default: /var/tmp/fbink)\n"
        "  --help               Show this help\n",
        prog);
}

int main(int argc, char *argv[])
{
    char pen_path[256] = "";
    char touch_path[256] = "";
    char fbink_path[256] = "/var/tmp/fbink";

    /* Parse arguments */
    static struct option long_opts[] = {
        {"pen-device",   required_argument, NULL, 'p'},
        {"touch-device", required_argument, NULL, 't'},
        {"fbink",        required_argument, NULL, 'f'},
        {"help",         no_argument,       NULL, 'h'},
        {NULL, 0, NULL, 0}
    };

    int opt;
    while ((opt = getopt_long(argc, argv, "p:t:f:h", long_opts, NULL)) != -1) {
        switch (opt) {
        case 'p':
            strncpy(pen_path, optarg, sizeof(pen_path) - 1);
            break;
        case 't':
            strncpy(touch_path, optarg, sizeof(touch_path) - 1);
            break;
        case 'f':
            strncpy(fbink_path, optarg, sizeof(fbink_path) - 1);
            break;
        case 'h':
        default:
            usage(argv[0]);
            return (opt == 'h') ? 0 : 1;
        }
    }

    /* Auto-detect devices if not specified */
    if (pen_path[0] == '\0') {
        if (detect_pen_device(pen_path, sizeof(pen_path)) < 0) {
            fprintf(stderr, "Error: Could not detect pen device\n");
            return 1;
        }
    }
    if (touch_path[0] == '\0') {
        if (detect_touch_device(touch_path, sizeof(touch_path)) < 0) {
            fprintf(stderr, "Warning: Could not detect touch device, buttons disabled\n");
        }
    }

    /* Initialize state */
    struct daemon_state st;
    memset(&st, 0, sizeof(st));
    strncpy(st.fbink_path, fbink_path, sizeof(st.fbink_path) - 1);
    st.rotation = ROTATION_PORTRAIT;
    st.pen_fd = -1;
    st.touch_fd = -1;

    /* Initialize touch slots */
    for (int i = 0; i < MAX_TOUCH_SLOTS; i++)
        st.touch_slots[i].tracking_id = -1;

    /* Read device capabilities */
    read_device_caps(&st, pen_path, touch_path);

    /* Fallback defaults for Kindle Scribe if sysfs didn't work */
    if (st.pen_x.max == 0) st.pen_x.max = 15725;
    if (st.pen_y.max == 0) st.pen_y.max = 20966;
    if (st.pen_pressure.max == 0) st.pen_pressure.max = 4095;
    /* Touch axis defaults -- Kindle Scribe touch panel is ~1860x2480
     * (matches screen resolution). Without these, button zones silently fail. */
    if (st.touch_x.max == 0) st.touch_x.max = 1860;
    if (st.touch_y.max == 0) st.touch_y.max = 2480;

    /* Open devices */
    st.pen_fd = open(pen_path, O_RDONLY);
    if (st.pen_fd < 0) {
        fprintf(stderr, "Error: Cannot open pen device %s: %s\n",
                pen_path, strerror(errno));
        return 1;
    }
    fprintf(stderr, "Opened pen device: %s\n", pen_path);

    if (touch_path[0] != '\0') {
        st.touch_fd = open(touch_path, O_RDONLY);
        if (st.touch_fd < 0) {
            fprintf(stderr, "Warning: Cannot open touch device %s: %s\n",
                    touch_path, strerror(errno));
        } else {
            fprintf(stderr, "Opened touch device: %s\n", touch_path);
        }
    }

    /* Set stdout to non-buffered for low latency */
    setbuf(stdout, NULL);

    /* Install signal handlers */
    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);
    signal(SIGPIPE, SIG_IGN);

    /* Draw initial UI */
    draw_ui(&st);

    fprintf(stderr, "Tablet daemon running (rotation=%s)\n",
            st.rotation == ROTATION_LANDSCAPE ? "landscape" : "portrait");

    /* Main poll loop */
    while (g_running) {
        struct pollfd fds[2];
        int nfds = 0;

        fds[nfds].fd = st.pen_fd;
        fds[nfds].events = POLLIN;
        nfds++;

        if (st.touch_fd >= 0) {
            fds[nfds].fd = st.touch_fd;
            fds[nfds].events = POLLIN;
            nfds++;
        }

        int ret = poll(fds, nfds, 1000);
        if (ret < 0) {
            if (errno == EINTR)
                continue;
            fprintf(stderr, "poll error: %s\n", strerror(errno));
            break;
        }
        if (ret == 0)
            continue; /* timeout, just check g_running */

        /* Check pen events */
        if (fds[0].revents & POLLIN) {
            struct input_event ev;
            ssize_t n = read(st.pen_fd, &ev, sizeof(ev));
            if (n == sizeof(ev)) {
                process_pen_event(&st, &ev);
            } else if (n <= 0) {
                fprintf(stderr, "Pen device read error\n");
                break;
            }
        }

        /* Check touch events */
        if (nfds > 1 && (fds[1].revents & POLLIN)) {
            struct input_event ev;
            ssize_t n = read(st.touch_fd, &ev, sizeof(ev));
            if (n == sizeof(ev)) {
                process_touch_event(&st, &ev);
            } else if (n <= 0) {
                fprintf(stderr, "Touch device read error\n");
                /* Don't break -- touch is optional */
                close(st.touch_fd);
                st.touch_fd = -1;
            }
        }
    }

    /* Cleanup */
    fprintf(stderr, "Tablet daemon shutting down\n");

    if (st.pen_fd >= 0)
        close(st.pen_fd);
    if (st.touch_fd >= 0)
        close(st.touch_fd);

    return 0;
}

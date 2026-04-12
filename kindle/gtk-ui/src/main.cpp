/*
 * tablet-ui - Kindle Tablet Mode GTK UI
 *
 * All content is drawn via Cairo on a GtkDrawingArea so that the UI can be
 * software-rotated 90° when the user switches to landscape mode (the Kindle's
 * X server does not support XRandR rotation).  The user physically rotates the
 * device; we pre-rotate our drawing so it appears correctly oriented.
 *
 * Stylus filtering is done by monitoring /dev/input/eventX directly in a
 * background thread and tracking BTN_TOOL_PEN proximity.  GTK2 without XInput
 * extension support always reports GDK_SOURCE_MOUSE for all devices, so the
 * GdkDevice source field cannot be used.
 *
 * TCP streaming (formerly tablet-daemon) runs in a second background thread.
 * It opens a separate, blocking fd to the same evdev device and forwards raw
 * input_event bytes to any host that connects on g_tcp_port.  Linux evdev
 * supports multiple independent readers on the same device node, so both
 * threads receive a complete, independent copy of every event.
 *
 * Build (Kindle):  meson setup --cross-file <path> builddir_kindle
 *                  meson compile -C builddir_kindle
 */

#include <gtk/gtk.h>
#include <cairo/cairo.h>
#include <pango/pangocairo.h>

#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <pthread.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <linux/input.h>

/* ------------------------------------------------------------------ */
/*  Configuration — all tuneable defaults in one place.               */
/*  parse_args() overwrites these from command-line arguments.        */
/* ------------------------------------------------------------------ */
static char g_marker_file[256]   = "/tmp/tablet-mode-active";
static char g_rotation_file[256] = "/tmp/tablet-rotation";
static char g_shortcut_file[256] = "/tmp/tablet-shortcut";
static int  g_tcp_port           = 8234;
static char g_tcp_device[256]    = "";  /* empty = auto-detect */

/* ------------------------------------------------------------------ */
/*  Runtime state                                                     */
/* ------------------------------------------------------------------ */
static int         g_rotation       = 0;   /* 0 = portrait, 90 = landscape */
static int         g_locked         = 0;   /* 0 = unlocked, 1 = locked */

static GtkWidget  *g_canvas         = NULL;

/* ------------------------------------------------------------------ */
/*  Device auto-detection                                             */
/* ------------------------------------------------------------------ */

/*
 * find_pen_device – scan /sys/class/input for the pen digitizer.
 *
 * If path_out is not NULL it receives the device path (e.g.
 * "/dev/input/event1"); the buffer must be at least 256 bytes.
 *
 * flags is passed directly to open() so callers can request
 * O_NONBLOCK (pen monitor) or blocking mode (TCP streamer).
 *
 * Returns an open fd on success, -1 when no device is found.
 */
static int find_pen_device(char path_out[256], int flags)
{
    DIR *dir = opendir("/sys/class/input");
    if (!dir) return -1;

    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (strncmp(entry->d_name, "event", 5) != 0)
            continue;

        char name_path[256];
        snprintf(name_path, sizeof(name_path),
                 "/sys/class/input/%s/device/name", entry->d_name);

        FILE *f = fopen(name_path, "r");
        if (!f) continue;

        char name[128] = {0};
        if (fgets(name, sizeof(name), f))
        {
            /* lowercase in-place */
            for (char *p = name; *p; ++p)
                if (*p >= 'A' && *p <= 'Z') *p += 32;

            if (strstr(name, "wacom")     ||
                strstr(name, "stylus")    ||
                strstr(name, "ntx_event") ||
                strstr(name, "digitizer") ||
                strstr(name, "pen"))
            {
                fclose(f);
                char dev_path[256];
                snprintf(dev_path, sizeof(dev_path),
                         "/dev/input/%s", entry->d_name);
                closedir(dir);
                if (path_out)
                    strncpy(path_out, dev_path, 255);
                return open(dev_path, flags);
            }
        }
        fclose(f);
    }
    closedir(dir);
    return -1;
}

/* Convenience wrapper for the pen proximity monitor (non-blocking). */
static int find_pen_device_fd(void)
{
    return find_pen_device(NULL, O_RDONLY | O_NONBLOCK);
}

/* ------------------------------------------------------------------ */
/*  Pen-proximity monitor (background thread)                         */
/*                                                                    */
/*  Reads raw Linux input events from the pen digitizer device and    */
/*  tracks the BTN_TOOL_PEN in-range state.  No GTK/XInput needed.   */
/* ------------------------------------------------------------------ */
static volatile int g_pen_in_range = 0;   /* atomic-ish int flag */
static pthread_t    g_pen_thread;

static void *pen_monitor_thread(void *)
{
    int fd = find_pen_device_fd();
    if (fd < 0) return NULL;

    struct input_event ev;
    while (1) {
        ssize_t n = read(fd, &ev, sizeof(ev));
        if (n == (ssize_t)sizeof(ev)) {
            if (ev.type == EV_KEY && ev.code == BTN_TOOL_PEN)
                __atomic_store_n(&g_pen_in_range, ev.value, __ATOMIC_RELAXED);
        } else {
            usleep(5000); /* 5 ms idle – non-blocking fd */
        }
    }
    /* unreachable */
    close(fd);
    return NULL;
}

/* ------------------------------------------------------------------ */
/*  TCP streaming daemon (background thread)                          */
/*                                                                    */
/*  Formerly a separate tablet-daemon binary.  Opens its own         */
/*  blocking fd to the evdev device (independent of the pen monitor  */
/*  thread's non-blocking fd) and streams all raw input_event bytes  */
/*  to whichever host is connected on g_tcp_port.                    */
/*                                                                    */
/*  The listen socket fd is stored in g_listen_fd so that the GTK   */
/*  exit handler can close it, which unblocks the accept() call and  */
/*  causes this thread to exit cleanly.                              */
/* ------------------------------------------------------------------ */
static volatile int g_listen_fd     = -1;
static pthread_t    g_tcp_thread;

static void *tcp_daemon_thread(void *)
{
    /* Open a blocking fd to the evdev device.  Use g_tcp_device if the
     * caller specified one explicitly; otherwise auto-detect. */
    int dev_fd;
    char devpath[256] = "";
    if (g_tcp_device[0] != '\0') {
        strncpy(devpath, g_tcp_device, sizeof(devpath) - 1);
        dev_fd = open(devpath, O_RDONLY);
    } else {
        dev_fd = find_pen_device(devpath, O_RDONLY);  /* blocking */
    }

    if (dev_fd < 0) {
        fprintf(stderr,
                "tcp_daemon_thread: no pen device found, TCP streaming disabled\n");
        return NULL;
    }

    /* Create TCP listening socket */
    int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) {
        perror("tcp_daemon_thread: socket");
        close(dev_fd);
        return NULL;
    }

    int opt = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port        = htons((unsigned short)g_tcp_port);

    if (bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("tcp_daemon_thread: bind");
        close(dev_fd);
        close(listen_fd);
        return NULL;
    }
    if (listen(listen_fd, 1) < 0) {
        perror("tcp_daemon_thread: listen");
        close(dev_fd);
        close(listen_fd);
        return NULL;
    }

    /* Publish so the exit handler can close it */
    g_listen_fd = listen_fd;

    fprintf(stderr,
            "tcp_daemon_thread: listening on port %d, streaming %s\n",
            g_tcp_port, devpath);

    unsigned char buf[4096];

    for (;;) {
        int client_fd = accept(listen_fd, NULL, NULL);
        if (client_fd < 0)
            break;  /* listen_fd closed (GTK exit) or real error */

        /* Disable Nagle so each event is sent immediately */
        int nodelay = 1;
        setsockopt(client_fd, IPPROTO_TCP, TCP_NODELAY,
                   &nodelay, sizeof(nodelay));

        fprintf(stderr, "tcp_daemon_thread: client connected\n");

        /* Forward all device bytes to the connected host */
        for (;;) {
            ssize_t n = read(dev_fd, buf, sizeof(buf));
            if (n <= 0) {
                fprintf(stderr,
                        "tcp_daemon_thread: device read error: %s\n",
                        strerror(errno));
                close(client_fd);
                goto done;
            }

            ssize_t sent = 0;
            while (sent < n) {
                ssize_t w = write(client_fd, buf + sent,
                                  (size_t)(n - sent));
                if (w <= 0)
                    goto next_client;  /* host disconnected */
                sent += w;
            }
        }

    next_client:
        fprintf(stderr, "tcp_daemon_thread: client disconnected\n");
        close(client_fd);
    }

done:
    close(listen_fd);
    close(dev_fd);
    return NULL;
}

/* ------------------------------------------------------------------ */
/*  Shortcut IDs – must match the Python ControlCode / backend enums  */
/* ------------------------------------------------------------------ */
#define SHORTCUT_UNDO          1
#define SHORTCUT_REDO          2
#define SHORTCUT_BRUSH_SMALLER 3
#define SHORTCUT_BRUSH_BIGGER  4
#define SHORTCUT_SAVE          5
#define SHORTCUT_SLASH         6

static void write_shortcut(int id)
{
    FILE *f = fopen(g_shortcut_file, "a");
    if (f) {
        fprintf(f, "%d\n", id);
        fclose(f);
    }
}

static void write_rotation_file(int angle)
{
    FILE *f = fopen(g_rotation_file, "a");
    if (f) {
        fprintf(f, "%d\n", angle);
        fclose(f);
    }
}

/* ------------------------------------------------------------------ */
/*  Button hit regions (in drawing-space coordinates)                 */
/* ------------------------------------------------------------------ */
typedef struct { double x, y, w, h; } Rect;

/* Main control buttons */
static Rect    g_rect_rotate;
static Rect    g_rect_exit;
static Rect    g_rect_lock;       /* now small, top-right */
static gboolean g_rotate_pressed  = FALSE;
static gboolean g_exit_pressed    = FALSE;
static gboolean g_lock_pressed    = FALSE;

/* Programmable shortcut buttons */
#define NUM_SHORTCUTS 6

typedef struct {
    Rect     rect;
    gboolean pressed;
    const char *label;  /* display label */
    int      id;        /* SHORTCUT_* constant */
} ShortcutButton;

static ShortcutButton g_shortcuts[NUM_SHORTCUTS] = {
    { {0,0,0,0}, FALSE, "Undo", SHORTCUT_UNDO          },
    { {0,0,0,0}, FALSE, "Redo", SHORTCUT_REDO          },
    { {0,0,0,0}, FALSE, "[",    SHORTCUT_BRUSH_SMALLER },
    { {0,0,0,0}, FALSE, "]",    SHORTCUT_BRUSH_BIGGER  },
    { {0,0,0,0}, FALSE, "Save", SHORTCUT_SAVE          },
    { {0,0,0,0}, FALSE, "/",    SHORTCUT_SLASH         },
};

static gboolean rect_contains(const Rect *r, double x, double y)
{
    return x >= r->x && x <= r->x + r->w &&
           y >= r->y && y <= r->y + r->h;
}

/* ------------------------------------------------------------------ */
/*  Coordinate transform: screen → drawing space                      */
/* ------------------------------------------------------------------ */
static void screen_to_drawing(double sx, double sy,
                               double *dx, double *dy,
                               int win_w, int win_h)
{
    switch (g_rotation) {
    case 90:
        *dx = sy;
        *dy = win_w - sx;
        break;
    case 180:
        *dx = win_w - sx;
        *dy = win_h - sy;
        break;
    case 270:
        *dx = sy;
        *dy = win_w - sx;
        break;
    default: /* 0° */
        *dx = sx;
        *dy = sy;
        break;
    }
}

/* ------------------------------------------------------------------ */
/*  Drawing helpers                                                   */
/* ------------------------------------------------------------------ */
static void draw_rounded_rect(cairo_t *cr, double x, double y,
                               double w, double h, double r)
{
    cairo_move_to(cr, x + r, y);
    cairo_line_to(cr, x + w - r, y);
    cairo_arc(cr, x + w - r, y + r, r, -M_PI/2, 0);
    cairo_line_to(cr, x + w, y + h - r);
    cairo_arc(cr, x + w - r, y + h - r, r, 0, M_PI/2);
    cairo_line_to(cr, x + r, y + h);
    cairo_arc(cr, x + r, y + h - r, r, M_PI/2, M_PI);
    cairo_line_to(cr, x, y + r);
    cairo_arc(cr, x + r, y + r, r, M_PI, 3*M_PI/2);
    cairo_close_path(cr);
}

/*
 * draw_button_sized – generic button renderer.
 *
 * active   = toggled-on state (e.g. lock engaged)
 * font_size = Pango font size in points
 */
static void draw_button_sized(cairo_t *cr, const Rect *rect,
                               const char *label, gboolean pressed,
                               gboolean active, int font_size)
{
    double x = rect->x, y = rect->y, w = rect->w, h = rect->h;
    double corner_r = (w < 100.0 || h < 80.0) ? 10.0 : 14.0;

    /* Shadow */
    cairo_save(cr);
    cairo_set_source_rgba(cr, 0, 0, 0, pressed ? 0.05 : 0.15);
    draw_rounded_rect(cr, x+3, y+3, w, h, corner_r);
    cairo_fill(cr);
    cairo_restore(cr);

    /* Button fill */
    if (active)
        cairo_set_source_rgb(cr, 0.25, 0.25, 0.25);
    else if (pressed)
        cairo_set_source_rgb(cr, 0.65, 0.65, 0.65);
    else
        cairo_set_source_rgb(cr, 0.88, 0.88, 0.88);
    draw_rounded_rect(cr, x, y, w, h, corner_r);
    cairo_fill(cr);

    /* Border */
    if (active)
        cairo_set_source_rgb(cr, 0.1, 0.1, 0.1);
    else
        cairo_set_source_rgb(cr, 0.4, 0.4, 0.4);
    cairo_set_line_width(cr, 2.0);
    draw_rounded_rect(cr, x, y, w, h, corner_r);
    cairo_stroke(cr);

    /* Label */
    PangoLayout *layout = pango_cairo_create_layout(cr);
    char font_desc[64];
    snprintf(font_desc, sizeof(font_desc), "Sans Bold %d", font_size);
    PangoFontDescription *fd = pango_font_description_from_string(font_desc);
    pango_layout_set_font_description(layout, fd);
    pango_font_description_free(fd);
    pango_layout_set_text(layout, label, -1);

    int tw, th;
    pango_layout_get_size(layout, &tw, &th);
    double tx = x + (w - tw / PANGO_SCALE) / 2.0;
    double ty = y + (h - th / PANGO_SCALE) / 2.0;

    if (active)
        cairo_set_source_rgb(cr, 0.95, 0.95, 0.95);
    else
        cairo_set_source_rgb(cr, 0.1, 0.1, 0.1);
    cairo_move_to(cr, tx, ty);
    pango_cairo_show_layout(cr, layout);
    g_object_unref(layout);
}

/* Convenience wrapper using the standard (large) font size */
static void draw_button(cairo_t *cr, const Rect *rect,
                         const char *label, gboolean pressed,
                         gboolean active)
{
    draw_button_sized(cr, rect, label, pressed, active, 26);
}

/*
 * draw_lock_button – draws the small top-right lock button using a
 * Cairo-rendered padlock icon instead of emoji (emoji codepoints above
 * U+FFFF render as raw numbers on the Kindle's old Pango/font stack).
 *
 * Locked:   closed shackle, both legs inside body.
 * Unlocked: open shackle, right leg lifted clear of body.
 */
static void draw_lock_button(cairo_t *cr, const Rect *rect,
                              gboolean pressed, gboolean locked)
{
    double x = rect->x, y = rect->y, w = rect->w, h = rect->h;
    double cx = x + w / 2.0;

    /* --- button background (same logic as draw_button_sized) --- */
    cairo_save(cr);
    cairo_set_source_rgba(cr, 0, 0, 0, pressed ? 0.05 : 0.15);
    draw_rounded_rect(cr, x+3, y+3, w, h, 10.0);
    cairo_fill(cr);
    cairo_restore(cr);

    if (locked)
        cairo_set_source_rgb(cr, 0.25, 0.25, 0.25);  /* dark grey when locked */
    else if (pressed)
        cairo_set_source_rgb(cr, 0.65, 0.65, 0.65);
    else
        cairo_set_source_rgb(cr, 0.88, 0.88, 0.88);
    draw_rounded_rect(cr, x, y, w, h, 10.0);
    cairo_fill(cr);

    if (locked)
        cairo_set_source_rgb(cr, 0.1, 0.1, 0.1);     /* dark border when locked */
    else
        cairo_set_source_rgb(cr, 0.4, 0.4, 0.4);
    cairo_set_line_width(cr, 2.0);
    draw_rounded_rect(cr, x, y, w, h, 10.0);
    cairo_stroke(cr);

    /* --- padlock icon --- */
    double icon_color = locked ? 0.95 : 0.15;
    cairo_set_source_rgb(cr, icon_color, icon_color, icon_color);

    /* Body: rounded rect in the lower ~55% of the button */
    double bw = w * 0.52;
    double bh = h * 0.40;
    double bx = cx - bw / 2.0;
    double by = y + h * 0.50;
    double br = bw * 0.13;
    draw_rounded_rect(cr, bx, by, bw, bh, br);
    cairo_fill(cr);

    /* Shackle: U-arc above body */
    double sr     = bw * 0.28;          /* outer radius of shackle arc */
    double thick  = w * 0.095;          /* stroke width */
    double leg_y  = by + thick * 0.3;   /* where legs disappear into body */
    double arc_cy = by - sr * 0.05;     /* vertical centre of the arc */

    cairo_set_line_width(cr, thick);
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);

    cairo_move_to(cr, cx - sr, leg_y);
    cairo_line_to(cr, cx - sr, arc_cy);
    cairo_arc(cr, cx, arc_cy, sr, M_PI, 0);   /* semicircle */
    if (locked) {
        cairo_line_to(cr, cx + sr, leg_y);     /* right leg down into body */
    } else {
        /* right leg lifted ~40% of shackle diameter above body */
        cairo_line_to(cr, cx + sr, by - sr * 0.85);
    }
    cairo_stroke(cr);

    /* Keyhole: filled circle + small notch (only drawn on body area) */
    double kx = cx;
    double ky = by + bh * 0.38;
    double kr = w * 0.055;
    cairo_arc(cr, kx, ky, kr, 0, 2 * M_PI);
    cairo_fill(cr);
    /* notch below the circle – punched out in the button's fill colour */
    if (locked)
        cairo_set_source_rgb(cr, 0.25, 0.25, 0.25);  /* match dark grey fill */
    else if (pressed)
        cairo_set_source_rgb(cr, 0.65, 0.65, 0.65);
    else
        cairo_set_source_rgb(cr, 0.88, 0.88, 0.88);
    cairo_rectangle(cr, kx - kr * 0.55, ky, kr * 1.1, kr * 1.3);
    cairo_fill(cr);
}

/* ------------------------------------------------------------------ */
/*  Main draw routine                                                 */
/* ------------------------------------------------------------------ */
static void do_draw(cairo_t *cr, int draw_w, int draw_h)
{
    /* White background */
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_paint(cr);

    /* ---- Top bar: shortcut buttons (left) + lock button (right) ---- *
     *                                                                  *
     *  [Undo][Redo][ ][ ][Save]                          [LOCK]        *
     *                                                                  */
    double top_sz  = 96.0;   /* square button size */
    double top_mar = 16.0;   /* screen edge margin  */
    double top_gap = 16.0;   /* gap between buttons */
    double top_y   = top_mar;

    /* Lock button – far right */
    g_rect_lock.x = draw_w - top_sz - top_mar;
    g_rect_lock.y = top_y;
    g_rect_lock.w = top_sz;
    g_rect_lock.h = top_sz;
    draw_lock_button(cr, &g_rect_lock, g_lock_pressed, g_locked);

    /* Shortcut buttons – left-aligned */
    for (int i = 0; i < NUM_SHORTCUTS; i++) {
        g_shortcuts[i].rect.x = top_mar + i * (top_sz + top_gap);
        g_shortcuts[i].rect.y = top_y;
        g_shortcuts[i].rect.w = top_sz;
        g_shortcuts[i].rect.h = top_sz;
        /* font size: larger for single-char labels like [ and ] */
        int fsz = (g_shortcuts[i].label[1] == '\0') ? 26 : 18;
        draw_button_sized(cr, &g_shortcuts[i].rect,
                          g_shortcuts[i].label,
                          g_shortcuts[i].pressed, FALSE, fsz);
    }

    /* ---- Title ---- */
    {
        PangoLayout *layout = pango_cairo_create_layout(cr);
        PangoFontDescription *fd =
            pango_font_description_from_string("Sans Bold 32");
        pango_layout_set_font_description(layout, fd);
        pango_font_description_free(fd);
        pango_layout_set_text(layout, "KINDLE TABLET MODE", -1);
        pango_layout_set_width(layout, draw_w * PANGO_SCALE);
        pango_layout_set_alignment(layout, PANGO_ALIGN_CENTER);

        int tw, th;
        pango_layout_get_size(layout, &tw, &th);
        double ty = draw_h * 0.18;
        cairo_set_source_rgb(cr, 0.1, 0.1, 0.1);
        cairo_move_to(cr, 0, ty);
        pango_cairo_show_layout(cr, layout);
        g_object_unref(layout);
    }

    /* ---- Subtitle ---- */
    {
        PangoLayout *layout = pango_cairo_create_layout(cr);
        PangoFontDescription *fd =
            pango_font_description_from_string("Sans 18");
        pango_layout_set_font_description(layout, fd);
        pango_font_description_free(fd);
        pango_layout_set_text(layout,
            "Draw on your computer.\nPen does NOT draw here.", -1);
        pango_layout_set_width(layout, draw_w * PANGO_SCALE);
        pango_layout_set_alignment(layout, PANGO_ALIGN_CENTER);

        int tw, th;
        pango_layout_get_size(layout, &tw, &th);
        double ty = draw_h * 0.30;
        cairo_set_source_rgb(cr, 0.3, 0.3, 0.3);
        cairo_move_to(cr, 0, ty);
        pango_cairo_show_layout(cr, layout);
        g_object_unref(layout);
    }

    /* ---- Main control buttons (bottom of screen) ---- */
    double btn_w  = draw_w * 0.72;
    double btn_h  = 110.0;
    double btn_x  = (draw_w - btn_w) / 2.0;
    double gap    = 30.0;
    double bottom = draw_h - 60.0;

    /* Exit button (lowest) */
    g_rect_exit.x = btn_x;
    g_rect_exit.y = bottom - btn_h;
    g_rect_exit.w = btn_w;
    g_rect_exit.h = btn_h;
    if (g_locked) {
        cairo_push_group(cr);
        draw_button(cr, &g_rect_exit, "Exit Tablet Mode", g_exit_pressed, FALSE);
        cairo_pop_group_to_source(cr);
        cairo_paint_with_alpha(cr, 0.30);
    } else {
        draw_button(cr, &g_rect_exit, "Exit Tablet Mode", g_exit_pressed, FALSE);
    }

    /* Rotate button (above exit) */
    g_rect_rotate.x = btn_x;
    g_rect_rotate.y = g_rect_exit.y - gap - btn_h;
    g_rect_rotate.w = btn_w;
    g_rect_rotate.h = btn_h;
    const char *rotate_label = (g_rotation == 0)
        ? "Rotate \xe2\x86\xba Landscape"   /* UTF-8 ↺ */
        : "Rotate \xe2\x86\xba Portrait";
    if (g_locked) {
        cairo_push_group(cr);
        draw_button(cr, &g_rect_rotate, rotate_label, g_rotate_pressed, FALSE);
        cairo_pop_group_to_source(cr);
        cairo_paint_with_alpha(cr, 0.30);
    } else {
        draw_button(cr, &g_rect_rotate, rotate_label, g_rotate_pressed, FALSE);
    }
}

static gboolean on_expose(GtkWidget *widget, GdkEventExpose *, gpointer)
{
    int win_w = widget->allocation.width;
    int win_h = widget->allocation.height;

    cairo_t *cr = gdk_cairo_create(widget->window);

    if (g_rotation == 90) {
        /* 90° CW rotation: drawing space becomes (win_h × win_w) */
        cairo_translate(cr, win_w, 0);
        cairo_rotate(cr, M_PI / 2.0);
        do_draw(cr, win_h, win_w);
    } else {
        do_draw(cr, win_w, win_h);
    }

    cairo_destroy(cr);
    return TRUE;
}

/* ------------------------------------------------------------------ */
/*  Input handling                                                    */
/* ------------------------------------------------------------------ */
static gboolean on_button_press(GtkWidget *widget, GdkEventButton *event,
                                 gpointer)
{
    /* Block stylus: pen digitizer sets BTN_TOOL_PEN when in range */
    if (__atomic_load_n(&g_pen_in_range, __ATOMIC_RELAXED))
        return TRUE;

    int win_w = widget->allocation.width;
    int win_h = widget->allocation.height;

    double dx, dy;
    screen_to_drawing(event->x, event->y, &dx, &dy, win_w, win_h);

    /* Lock button is always tappable */
    if (rect_contains(&g_rect_lock, dx, dy)) {
        g_lock_pressed = TRUE;
        gtk_widget_queue_draw(widget);
        return TRUE;
    }

    /* Shortcut buttons work regardless of lock state */
    for (int i = 0; i < NUM_SHORTCUTS; i++) {
        if (rect_contains(&g_shortcuts[i].rect, dx, dy)) {
            g_shortcuts[i].pressed = TRUE;
            gtk_widget_queue_draw(widget);
            return TRUE;
        }
    }

    /* Rotate / exit are blocked when locked */
    if (!g_locked) {
        if (rect_contains(&g_rect_rotate, dx, dy)) {
            g_rotate_pressed = TRUE;
            gtk_widget_queue_draw(widget);
        } else if (rect_contains(&g_rect_exit, dx, dy)) {
            g_exit_pressed = TRUE;
            gtk_widget_queue_draw(widget);
        }
    }

    return TRUE;
}

static gboolean on_button_release(GtkWidget *widget, GdkEventButton *event,
                                   gpointer)
{
    if (__atomic_load_n(&g_pen_in_range, __ATOMIC_RELAXED))
        return TRUE;

    int win_w = widget->allocation.width;
    int win_h = widget->allocation.height;

    double dx, dy;
    screen_to_drawing(event->x, event->y, &dx, &dy, win_w, win_h);

    gboolean do_rotate   = FALSE;
    gboolean do_exit     = FALSE;
    gboolean do_lock     = FALSE;
    int      do_shortcut = -1;

    if (g_lock_pressed && rect_contains(&g_rect_lock, dx, dy))
        do_lock = TRUE;
    if (g_rotate_pressed && rect_contains(&g_rect_rotate, dx, dy))
        do_rotate = TRUE;
    if (g_exit_pressed && rect_contains(&g_rect_exit, dx, dy))
        do_exit = TRUE;
    for (int i = 0; i < NUM_SHORTCUTS; i++) {
        if (g_shortcuts[i].pressed &&
            rect_contains(&g_shortcuts[i].rect, dx, dy)) {
            do_shortcut = g_shortcuts[i].id;
            break;
        }
    }

    /* Clear all pressed states */
    g_lock_pressed   = FALSE;
    g_rotate_pressed = FALSE;
    g_exit_pressed   = FALSE;
    for (int i = 0; i < NUM_SHORTCUTS; i++)
        g_shortcuts[i].pressed = FALSE;
    gtk_widget_queue_draw(widget);

    /* --- Actions --- */
    if (do_lock) {
        g_locked = g_locked ? 0 : 1;
        gtk_widget_queue_draw(widget);
    }

    if (do_rotate) {
        g_rotation = (g_rotation == 0) ? 90 : 0;

        write_rotation_file(g_rotation);

        gtk_widget_queue_draw(widget);
    }

    if (do_exit) {
        app_shutdown();
        gtk_main_quit();
    }

    if (do_shortcut >= 0)
        write_shortcut(do_shortcut);

    return TRUE;
}

/* ------------------------------------------------------------------ */
/*  Consolidated initialization and shutdown                          */
/* ------------------------------------------------------------------ */

/*
 * parse_args – populate all configuration globals from argv.
 *
 * Supported flags:
 *   --marker-file <path>    active-mode marker  (default /tmp/tablet-mode-active)
 *   --rotation-file <path>  rotation IPC file   (default /tmp/tablet-rotation)
 *   --shortcut-file <path>  shortcut IPC file   (default /tmp/tablet-shortcut)
 *   --device <path>         evdev device to stream (default: auto-detect)
 *   --port <N>              TCP port            (default: 8234)
 */
static void parse_args(int argc, char *argv[])
{
    for (int i = 1; i < argc - 1; i++) {
        if (strcmp(argv[i], "--marker-file") == 0)
            strncpy(g_marker_file,  argv[i + 1], sizeof(g_marker_file)  - 1);
        else if (strcmp(argv[i], "--rotation-file") == 0)
            strncpy(g_rotation_file, argv[i + 1], sizeof(g_rotation_file) - 1);
        else if (strcmp(argv[i], "--shortcut-file") == 0)
            strncpy(g_shortcut_file, argv[i + 1], sizeof(g_shortcut_file) - 1);
        else if (strcmp(argv[i], "--device") == 0)
            strncpy(g_tcp_device,   argv[i + 1], sizeof(g_tcp_device)   - 1);
        else if (strcmp(argv[i], "--port") == 0) {
            int p = atoi(argv[i + 1]);
            if (p > 0 && p <= 65535)
                g_tcp_port = p;
        }
    }
}

/*
 * app_shutdown – release all resources and signal external watchers.
 *
 * Idempotent: safe to call multiple times (e.g. from the exit button
 * handler and again as a safety net after gtk_main() returns).
 * Does NOT call gtk_main_quit(); that is the caller's responsibility.
 */
static void app_shutdown(void)
{
    static int called = 0;
    /* Only the first caller proceeds; subsequent calls are no-ops. */
    if (__atomic_exchange_n(&called, 1, __ATOMIC_RELAXED) != 0)
        return;

    /* Unblock the TCP daemon thread so it exits cleanly */
    int lfd = g_listen_fd;
    if (lfd >= 0) {
        g_listen_fd = -1;
        close(lfd);
    }

    /* Remove IPC files so the host and tablet-mode.sh know we're done */
    unlink(g_marker_file);
    unlink(g_rotation_file);
    unlink(g_shortcut_file);
}

/*
 * app_init – one-shot process-level setup run before gtk_main().
 *
 * Handles signal disposition and launches the two background threads.
 * Must be called after gtk_init() and after parse_args().
 */
static void app_init(void)
{
    /* Ignore SIGPIPE so a broken host TCP connection doesn't kill us */
    signal(SIGPIPE, SIG_IGN);

    /* Pen proximity monitor: non-blocking fd, auto-detects device */
    pthread_create(&g_pen_thread, NULL, pen_monitor_thread, NULL);
    pthread_detach(g_pen_thread);

    /* TCP streaming daemon: blocking fd, uses g_tcp_device / g_tcp_port */
    pthread_create(&g_tcp_thread, NULL, tcp_daemon_thread, NULL);
    pthread_detach(g_tcp_thread);
}

/* GTK "destroy" signal handler – ensures cleanup even if the window
 * manager closes the window rather than the user tapping Exit. */
static void on_window_destroy(GtkWidget *, gpointer)
{
    app_shutdown();
    gtk_main_quit();
}

/*
 * build_gtk_window – create and show the fullscreen UI window.
 *
 * Wires all GTK signals and sets the global g_canvas pointer.
 */
static void build_gtk_window(void)
{
    GtkWidget *window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window),
        "L:A_N:application_PC:N_ID:com.lab126.kindletablet");
    gtk_window_fullscreen(GTK_WINDOW(window));
    g_signal_connect(window, "destroy",
                     G_CALLBACK(on_window_destroy), NULL);

    /* Cairo drawing area – single child, fills the window */
    g_canvas = gtk_drawing_area_new();
    gtk_widget_add_events(g_canvas,
        GDK_BUTTON_PRESS_MASK | GDK_BUTTON_RELEASE_MASK);
    g_signal_connect(g_canvas, "expose-event",
                     G_CALLBACK(on_expose), NULL);
    g_signal_connect(g_canvas, "button-press-event",
                     G_CALLBACK(on_button_press), NULL);
    g_signal_connect(g_canvas, "button-release-event",
                     G_CALLBACK(on_button_release), NULL);

    gtk_container_add(GTK_CONTAINER(window), g_canvas);
    gtk_widget_show_all(window);
}

/* ------------------------------------------------------------------ */
/*  main                                                              */
/* ------------------------------------------------------------------ */
int main(int argc, char *argv[])
{
    parse_args(argc, argv);
    gtk_init(&argc, &argv);
    app_init();
    build_gtk_window();
    gtk_main();
    app_shutdown();   /* safety net — idempotent if exit button was used */
    return 0;
}

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
#include <linux/input.h>

/* ------------------------------------------------------------------ */
/*  Globals                                                           */
/* ------------------------------------------------------------------ */
static const char *g_marker_file   = "/tmp/tablet-mode-active";
static const char *g_rotation_file = "/tmp/tablet-rotation";
static int         g_rotation      = 0;   /* 0 = portrait, 90 = landscape */

static GtkWidget  *g_canvas        = NULL;

/* ------------------------------------------------------------------ */
/*  Pen-proximity monitor (background thread)                         */
/*                                                                    */
/*  Reads raw Linux input events from the pen digitizer device and    */
/*  tracks the BTN_TOOL_PEN in-range state.  No GTK/XInput needed.   */
/* ------------------------------------------------------------------ */
static volatile int g_pen_in_range = 0;   /* atomic-ish int flag */
static pthread_t    g_pen_thread;

static int find_pen_device_fd(void)
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
                return open(dev_path, O_RDONLY | O_NONBLOCK);
            }
        }
        fclose(f);
    }
    closedir(dir);
    return -1;
}

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
/*  Button hit regions (in drawing-space coordinates)                 */
/* ------------------------------------------------------------------ */
typedef struct { double x, y, w, h; } Rect;

static Rect   g_rect_rotate;
static Rect   g_rect_exit;
static gboolean g_rotate_pressed = FALSE;
static gboolean g_exit_pressed   = FALSE;

static gboolean rect_contains(const Rect *r, double x, double y)
{
    return x >= r->x && x <= r->x + r->w &&
           y >= r->y && y <= r->y + r->h;
}

/* ------------------------------------------------------------------ */
/*  Coordinate transform: screen → drawing space                      */
/*                                                                    */
/*  Cairo forward transforms and their inverses:                      */
/*                                                                    */
/*   0°:  identity                                                    */
/*        draw(W×H),  sx=dx,        sy=dy                            */
/*        inverse:    dx=sx,        dy=sy                            */
/*                                                                    */
/*  90°:  translate(0,H) + rotate(-π/2)                              */
/*        draw(H×W),  sx=dy,        sy=H-dx                          */
/*        inverse:    dx=H-sy,      dy=sx                            */
/*                                                                    */
/* 180°:  translate(W,H) + rotate(π)                                 */
/*        draw(W×H),  sx=W-dx,      sy=H-dy                          */
/*        inverse:    dx=W-sx,      dy=H-sy                          */
/*                                                                    */
/* 270°:  translate(W,0) + rotate(π/2)                               */
/*        draw(H×W),  sx=W-dy,      sy=dx                            */
/*        inverse:    dx=sy,        dy=W-sx                          */
/* ------------------------------------------------------------------ */
static void screen_to_drawing(double sx, double sy,
                               double *dx, double *dy,
                               int win_w, int win_h)
{
    switch (g_rotation) {
    case 90:
        /* forward: sx = win_w - dy, sy = dx  →  inverse: dx = sy, dy = win_w - sx */
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
/*  Drawing                                                           */
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

static void draw_button(cairo_t *cr, const Rect *rect,
                         const char *label, gboolean pressed)
{
    double x = rect->x, y = rect->y, w = rect->w, h = rect->h;

    /* Shadow */
    cairo_save(cr);
    cairo_set_source_rgba(cr, 0, 0, 0, pressed ? 0.05 : 0.15);
    draw_rounded_rect(cr, x+3, y+3, w, h, 14);
    cairo_fill(cr);
    cairo_restore(cr);

    /* Button fill */
    if (pressed)
        cairo_set_source_rgb(cr, 0.65, 0.65, 0.65);
    else
        cairo_set_source_rgb(cr, 0.88, 0.88, 0.88);
    draw_rounded_rect(cr, x, y, w, h, 14);
    cairo_fill(cr);

    /* Border */
    cairo_set_source_rgb(cr, 0.4, 0.4, 0.4);
    cairo_set_line_width(cr, 2.0);
    draw_rounded_rect(cr, x, y, w, h, 14);
    cairo_stroke(cr);

    /* Label */
    PangoLayout *layout = pango_cairo_create_layout(cr);
    PangoFontDescription *fd =
        pango_font_description_from_string("Sans Bold 26");
    pango_layout_set_font_description(layout, fd);
    pango_font_description_free(fd);
    pango_layout_set_text(layout, label, -1);

    int tw, th;
    pango_layout_get_size(layout, &tw, &th);
    double tx = x + (w - tw / PANGO_SCALE) / 2.0;
    double ty = y + (h - th / PANGO_SCALE) / 2.0;

    cairo_set_source_rgb(cr, 0.1, 0.1, 0.1);
    cairo_move_to(cr, tx, ty);
    pango_cairo_show_layout(cr, layout);
    g_object_unref(layout);
}

static void do_draw(cairo_t *cr, int draw_w, int draw_h)
{
    /* White background */
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_paint(cr);

    /* Title */
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

    /* Subtitle */
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
        double ty = draw_h * 0.32;
        cairo_set_source_rgb(cr, 0.3, 0.3, 0.3);
        cairo_move_to(cr, 0, ty);
        pango_cairo_show_layout(cr, layout);
        g_object_unref(layout);
    }

    /* Buttons – positioned in lower portion of drawing space */
    double btn_w  = draw_w * 0.72;
    double btn_h  = 110.0;
    double btn_x  = (draw_w - btn_w) / 2.0;
    double gap    = 30.0;
    double bottom = draw_h - 60.0;

    /* Exit button (lower) */
    g_rect_exit.x = btn_x;
    g_rect_exit.y = bottom - btn_h;
    g_rect_exit.w = btn_w;
    g_rect_exit.h = btn_h;
    draw_button(cr, &g_rect_exit, "Exit Tablet Mode", g_exit_pressed);

    /* Rotate button (above exit) */
    g_rect_rotate.x = btn_x;
    g_rect_rotate.y = g_rect_exit.y - gap - btn_h;
    g_rect_rotate.w = btn_w;
    g_rect_rotate.h = btn_h;
    const char *rotate_label = (g_rotation == 0)
        ? "Rotate \xe2\x86\xba Landscape"   /* UTF-8 ↺ */
        : "Rotate \xe2\x86\xba Portrait";
    draw_button(cr, &g_rect_rotate, rotate_label, g_rotate_pressed);
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

    if (rect_contains(&g_rect_rotate, dx, dy)) {
        g_rotate_pressed = TRUE;
        gtk_widget_queue_draw(widget);
    } else if (rect_contains(&g_rect_exit, dx, dy)) {
        g_exit_pressed = TRUE;
        gtk_widget_queue_draw(widget);
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

    gboolean do_rotate = FALSE;
    gboolean do_exit   = FALSE;

    if (g_rotate_pressed && rect_contains(&g_rect_rotate, dx, dy))
        do_rotate = TRUE;
    if (g_exit_pressed && rect_contains(&g_rect_exit, dx, dy))
        do_exit = TRUE;

    g_rotate_pressed = FALSE;
    g_exit_pressed   = FALSE;
    gtk_widget_queue_draw(widget);

    /* --- Actions --- */
    if (do_rotate) {
        g_rotation = (g_rotation == 0) ? 90 : 0;

        char cmd[256];
        snprintf(cmd, sizeof(cmd), "echo %d >> %s", g_rotation, g_rotation_file);
        system(cmd);

        gtk_widget_queue_draw(widget);
    }

    if (do_exit) {
        unlink(g_marker_file);
        gtk_main_quit();
    }

    return TRUE;
}

/* ------------------------------------------------------------------ */
/*  main                                                              */
/* ------------------------------------------------------------------ */
int main(int argc, char *argv[])
{
    for (int i = 1; i < argc - 1; i++) {
        if (strcmp(argv[i], "--marker-file") == 0) {
            g_marker_file = argv[i + 1];
            break;
        }
    }

    gtk_init(&argc, &argv);

    /* Start pen proximity monitor */
    pthread_create(&g_pen_thread, NULL, pen_monitor_thread, NULL);
    pthread_detach(g_pen_thread);

    /* Fullscreen window */
    GtkWidget *window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window),
        "L:A_N:application_PC:N_ID:com.lab126.kindletablet");
    gtk_window_fullscreen(GTK_WINDOW(window));
    g_signal_connect(window, "destroy", G_CALLBACK(gtk_main_quit), NULL);

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
    gtk_main();

    return 0;
}

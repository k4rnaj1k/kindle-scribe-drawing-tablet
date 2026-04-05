/*
 * tablet-ui - Kindle Tablet Mode GTK UI
 *
 * Displays a fullscreen window with "Rotate" and "Exit Tablet Mode" buttons.
 * Rotate toggles the coordinate mapping between portrait/landscape by writing
 * the rotation angle to /tmp/tablet-rotation for the host to read.
 * It also physically rotates the X display via xrandr / lipc.
 * Exit removes the marker file and exits so tablet-mode.sh can restore
 * the framework.
 *
 * Buttons only respond to finger touch; stylus (pen/eraser) taps are ignored.
 *
 * Build (native):  meson setup builddir && meson compile -C builddir
 * Build (Kindle):  meson setup --cross-file <path> builddir_kindlehf
 *                  meson compile -C builddir_kindlehf
 */

#include <gtk/gtk.h>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <unistd.h>

static const char *g_marker_file  = "/tmp/tablet-mode-active";
static const char *g_rotation_file = "/tmp/tablet-rotation";
static int g_rotation = 0; /* 0 = portrait, 90 = landscape */

/* Widget references for dynamic layout updates */
static GtkWidget *g_title      = NULL;
static GtkWidget *g_subtitle   = NULL;
static GtkWidget *g_rotate_btn = NULL;
static GtkWidget *g_exit_btn   = NULL;

/* ------------------------------------------------------------------ */
/*  Display rotation via shell commands                               */
/*  Tries Kindle-native lipc first, then xrandr as fallback.         */
/* ------------------------------------------------------------------ */
static void rotate_display(gboolean landscape)
{
    if (landscape) {
        /* Kindle WM orientation property */
        system("lipc-set-prop com.lab126.winmgr setOrientation L 2>/dev/null");
        /* xrandr fallback: auto-detect the connected output name */
        system("OUT=$(xrandr 2>/dev/null | awk '/ connected/{print $1; exit}');"
               "[ -n \"$OUT\" ] && xrandr --output \"$OUT\" --rotate left 2>/dev/null"
               "|| xrandr -o left 2>/dev/null");
    } else {
        system("lipc-set-prop com.lab126.winmgr setOrientation U 2>/dev/null");
        system("OUT=$(xrandr 2>/dev/null | awk '/ connected/{print $1; exit}');"
               "[ -n \"$OUT\" ] && xrandr --output \"$OUT\" --rotate normal 2>/dev/null"
               "|| xrandr -o normal 2>/dev/null");
    }
}

/* ------------------------------------------------------------------ */
/*  Layout – adjust fonts & button sizes for portrait / landscape     */
/* ------------------------------------------------------------------ */
static void update_layout_for_rotation(void)
{
    gboolean landscape = (g_rotation == 90);

    if (landscape) {
        /* Landscape: wider screen, much less vertical space */
        gtk_label_set_markup(GTK_LABEL(g_title),
            "<span font='36' weight='bold'>KINDLE TABLET MODE</span>");
        gtk_label_set_markup(GTK_LABEL(g_subtitle),
            "<span font='18'>Draw on your computer.  Pen does NOT draw here.</span>");

        gtk_widget_set_size_request(g_rotate_btn, 700, 72);
        gtk_widget_set_size_request(g_exit_btn,   700, 72);

        PangoFontDescription *f = pango_font_description_from_string("Sans Bold 22");
        gtk_widget_modify_font(gtk_bin_get_child(GTK_BIN(g_rotate_btn)), f);
        gtk_widget_modify_font(gtk_bin_get_child(GTK_BIN(g_exit_btn)),   f);
        pango_font_description_free(f);
    } else {
        /* Portrait (vertical): tall screen – smaller text */
        gtk_label_set_markup(GTK_LABEL(g_title),
            "<span font='28' weight='bold'>KINDLE TABLET MODE</span>");
        gtk_label_set_markup(GTK_LABEL(g_subtitle),
            "<span font='14'>Draw on your computer.\nPen does NOT draw here.</span>");

        gtk_widget_set_size_request(g_rotate_btn, 700, 100);
        gtk_widget_set_size_request(g_exit_btn,   700, 100);

        PangoFontDescription *f = pango_font_description_from_string("Sans Bold 24");
        gtk_widget_modify_font(gtk_bin_get_child(GTK_BIN(g_rotate_btn)), f);
        gtk_widget_modify_font(gtk_bin_get_child(GTK_BIN(g_exit_btn)),   f);
        pango_font_description_free(f);
    }
}

/* ------------------------------------------------------------------ */
/*  Stylus filtering                                                  */
/*                                                                    */
/*  GTK2 normally merges all devices into the core pointer so         */
/*  event->device->source is always GDK_SOURCE_MOUSE.  We therefore  */
/*  rely on a short timestamp window: the pen digitizer fires a       */
/*  synthetic core-pointer event within a few ms of the real XI       */
/*  event.  By recording the time of any identifiable pen event we    */
/*  can suppress the duplicate on the same button.                    */
/*                                                                    */
/*  On Kindle's GTK2 build, gdk_device_set_mode() crashes (likely    */
/*  because the XI extension is not configured), so we intentionally  */
/*  skip enabling extended input devices and rely solely on the       */
/*  source / name check plus the timestamp guard.                     */
/* ------------------------------------------------------------------ */
static guint32 g_last_pen_time = 0;

static gboolean is_stylus_device(GdkDevice *dev)
{
    if (!dev)
        return FALSE;

    GdkInputSource src = dev->source;
    if (src == GDK_SOURCE_PEN || src == GDK_SOURCE_ERASER)
        return TRUE;

    const gchar *name = dev->name;
    if (name) {
        gchar *lower = g_ascii_strdown(name, -1);
        gboolean pen = (strstr(lower, "pen")    != NULL ||
                        strstr(lower, "stylus")  != NULL ||
                        strstr(lower, "eraser")  != NULL ||
                        strstr(lower, "wacom")   != NULL);
        g_free(lower);
        return pen;
    }
    return FALSE;
}

static gboolean on_button_press_filter(GtkWidget *, GdkEventButton *event, gpointer)
{
    /* Direct hit from an XInput pen/eraser device */
    if (is_stylus_device(event->device)) {
        g_last_pen_time = event->time;
        return TRUE;
    }

    /* Core-pointer echo of the same physical tap (arrives within ~50 ms) */
    if (event->time - g_last_pen_time < 200)
        return TRUE;

    return FALSE;
}

/* ------------------------------------------------------------------ */
/*  Button callbacks                                                  */
/* ------------------------------------------------------------------ */
static void on_exit_clicked(GtkWidget *, gpointer)
{
    /* Restore portrait before exiting so the framework isn't left rotated */
    if (g_rotation != 0)
        rotate_display(FALSE);

    unlink(g_marker_file);
    gtk_main_quit();
}

static void on_rotate_clicked(GtkWidget *, gpointer)
{
    char cmd[256];

    if (g_rotation == 0) {
        g_rotation = 90;
        gtk_button_set_label(GTK_BUTTON(g_rotate_btn), "Rotate (Portrait)");
    } else {
        g_rotation = 0;
        gtk_button_set_label(GTK_BUTTON(g_rotate_btn), "Rotate (Landscape)");
    }

    rotate_display(g_rotation == 90);

    snprintf(cmd, sizeof(cmd), "echo %d >> %s", g_rotation, g_rotation_file);
    system(cmd);

    update_layout_for_rotation();
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

    /* Create fullscreen window with awesome WM title */
    GtkWidget *window = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(window),
        "L:A_N:application_PC:N_ID:com.lab126.kindletablet");
    gtk_window_fullscreen(GTK_WINDOW(window));
    g_signal_connect(window, "destroy", G_CALLBACK(gtk_main_quit), NULL);

    /* Vertical layout */
    GtkWidget *vbox = gtk_vbox_new(FALSE, 10);
    gtk_container_set_border_width(GTK_CONTAINER(vbox), 30);
    gtk_container_add(GTK_CONTAINER(window), vbox);

    /* Title label */
    g_title = gtk_label_new(NULL);
    gtk_box_pack_start(GTK_BOX(vbox), g_title, FALSE, FALSE, 10);

    /* Subtitle label */
    g_subtitle = gtk_label_new(NULL);
    gtk_box_pack_start(GTK_BOX(vbox), g_subtitle, FALSE, FALSE, 5);

    /* Spacer pushes buttons to the bottom */
    GtkWidget *spacer = gtk_label_new("");
    gtk_box_pack_start(GTK_BOX(vbox), spacer, TRUE, TRUE, 0);

    /* Rotate button */
    g_rotate_btn = gtk_button_new_with_label("Rotate (Landscape)");
    gtk_widget_set_extension_events(g_rotate_btn, GDK_EXTENSION_EVENTS_ALL);
    g_signal_connect(g_rotate_btn, "button-press-event",
                     G_CALLBACK(on_button_press_filter), NULL);
    g_signal_connect(g_rotate_btn, "clicked",
                     G_CALLBACK(on_rotate_clicked), NULL);

    GtkWidget *rotate_align = gtk_alignment_new(0.5, 1.0, 0.0, 0.0);
    gtk_container_add(GTK_CONTAINER(rotate_align), g_rotate_btn);
    gtk_box_pack_end(GTK_BOX(vbox), rotate_align, FALSE, FALSE, 10);

    /* Exit button */
    g_exit_btn = gtk_button_new_with_label("Exit Tablet Mode");
    gtk_widget_set_extension_events(g_exit_btn, GDK_EXTENSION_EVENTS_ALL);
    g_signal_connect(g_exit_btn, "button-press-event",
                     G_CALLBACK(on_button_press_filter), NULL);
    g_signal_connect(g_exit_btn, "clicked",
                     G_CALLBACK(on_exit_clicked), NULL);

    GtkWidget *button_align = gtk_alignment_new(0.5, 1.0, 0.0, 0.0);
    gtk_container_add(GTK_CONTAINER(button_align), g_exit_btn);
    gtk_box_pack_end(GTK_BOX(vbox), button_align, FALSE, FALSE, 20);

    /* Apply initial portrait layout */
    update_layout_for_rotation();

    gtk_widget_show_all(window);
    gtk_main();

    return 0;
}

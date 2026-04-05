/*
 * tablet-ui - Kindle Tablet Mode GTK UI
 *
 * Displays a fullscreen window with "Rotate" and "Exit Tablet Mode" buttons.
 * Rotate toggles the coordinate mapping between portrait/landscape by writing
 * the rotation angle to /tmp/tablet-rotation for the host to read.
 * It also physically rotates the X display via xrandr.
 * Exit removes the marker file and exits so tablet-mode.sh can restore
 * the framework.
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

static const char *g_marker_file = "/tmp/tablet-mode-active";
static const char *g_rotation_file = "/tmp/tablet-rotation";
static int g_rotation = 0; /* 0 = portrait, 90 = landscape */

/* Widget references for dynamic layout updates */
static GtkWidget *g_title    = NULL;
static GtkWidget *g_subtitle = NULL;
static GtkWidget *g_rotate_btn = NULL;
static GtkWidget *g_exit_btn   = NULL;

/* Update fonts and button sizes to suit the current rotation */
static void update_layout_for_rotation(void)
{
    gboolean landscape = (g_rotation == 90);

    if (landscape) {
        /* Landscape: wider screen, less vertical space – use larger title,
         * shorter buttons */
        gtk_label_set_markup(GTK_LABEL(g_title),
            "<span font='36' weight='bold'>KINDLE TABLET MODE</span>");
        gtk_label_set_markup(GTK_LABEL(g_subtitle),
            "<span font='18'>Draw on your computer.  Pen does NOT draw here.</span>");

        gtk_widget_set_size_request(g_rotate_btn, 450, 72);
        gtk_widget_set_size_request(g_exit_btn,   450, 72);

        PangoFontDescription *f = pango_font_description_from_string("Sans Bold 22");
        gtk_widget_modify_font(gtk_bin_get_child(GTK_BIN(g_rotate_btn)), f);
        gtk_widget_modify_font(gtk_bin_get_child(GTK_BIN(g_exit_btn)),   f);
        pango_font_description_free(f);
    } else {
        /* Portrait (vertical): tall screen – smaller text to leave room for
         * the buttons further down */
        gtk_label_set_markup(GTK_LABEL(g_title),
            "<span font='28' weight='bold'>KINDLE TABLET MODE</span>");
        gtk_label_set_markup(GTK_LABEL(g_subtitle),
            "<span font='14'>Draw on your computer.\nPen does NOT draw here.</span>");

        gtk_widget_set_size_request(g_rotate_btn, 400, 100);
        gtk_widget_set_size_request(g_exit_btn,   400, 100);

        PangoFontDescription *f = pango_font_description_from_string("Sans Bold 24");
        gtk_widget_modify_font(gtk_bin_get_child(GTK_BIN(g_rotate_btn)), f);
        gtk_widget_modify_font(gtk_bin_get_child(GTK_BIN(g_exit_btn)),   f);
        pango_font_description_free(f);
    }
}

/* Return TRUE if the button event came from a pen/stylus rather than a finger.
 * GTK2's GdkDevice is a public struct – access fields directly because the
 * gdk_device_get_source / gdk_device_get_name accessors may not be present in
 * older sysroot builds. */
static gboolean is_stylus_event(GdkEventButton *event)
{
    if (!event->device)
        return FALSE;

    /* Direct struct field access (GTK2 public ABI) */
    GdkInputSource src = event->device->source;
    if (src == GDK_SOURCE_PEN || src == GDK_SOURCE_ERASER)
        return TRUE;

    /* Fallback: check device name in case source isn't set correctly */
    const gchar *name = event->device->name;
    if (name) {
        gchar *lower = g_ascii_strdown(name, -1);
        gboolean pen = (strstr(lower, "pen")   != NULL ||
                        strstr(lower, "stylus") != NULL ||
                        strstr(lower, "eraser") != NULL ||
                        strstr(lower, "wacom")  != NULL);
        g_free(lower);
        if (pen) return TRUE;
    }
    return FALSE;
}

/* Swallow button-press events from the stylus so fingers-only activate buttons */
static gboolean on_button_press_filter(GtkWidget *, GdkEventButton *event, gpointer)
{
    return is_stylus_event(event) ? TRUE : FALSE;
}

static void on_exit_clicked(GtkWidget *, gpointer)
{
    /* Remove the marker file so tablet-mode.sh's wait loop exits */
    unlink(g_marker_file);
    gtk_main_quit();
}

static void on_rotate_clicked(GtkWidget *, gpointer)
{
    char cmd[256];

    if (g_rotation == 0) {
        g_rotation = 90;
        gtk_button_set_label(GTK_BUTTON(g_rotate_btn), "Rotate (Portrait)");
        /* Rotate the X display 90° counter-clockwise → landscape */
        system("xrandr -o left 2>/dev/null");
    } else {
        g_rotation = 0;
        gtk_button_set_label(GTK_BUTTON(g_rotate_btn), "Rotate (Landscape)");
        /* Restore normal portrait orientation */
        system("xrandr -o normal 2>/dev/null");
    }

    /* Write rotation angle to file so the host picks it up */
    snprintf(cmd, sizeof(cmd), "echo %d >> %s", g_rotation, g_rotation_file);
    system(cmd);

    update_layout_for_rotation();
}

int main(int argc, char *argv[])
{
    /* Parse --marker-file argument */
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
    g_signal_connect(g_rotate_btn, "button-press-event",
                     G_CALLBACK(on_button_press_filter), NULL);
    g_signal_connect(g_rotate_btn, "clicked",
                     G_CALLBACK(on_rotate_clicked), NULL);

    GtkWidget *rotate_align = gtk_alignment_new(0.5, 1.0, 0.0, 0.0);
    gtk_container_add(GTK_CONTAINER(rotate_align), g_rotate_btn);
    gtk_box_pack_end(GTK_BOX(vbox), rotate_align, FALSE, FALSE, 10);

    /* Exit button */
    g_exit_btn = gtk_button_new_with_label("Exit Tablet Mode");
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

/*
 * tablet-ui - Kindle Tablet Mode GTK UI
 *
 * Displays a fullscreen window with "Rotate" and "Exit Tablet Mode" buttons.
 * Rotate toggles the coordinate mapping between portrait/landscape by writing
 * the rotation angle to /tmp/tablet-rotation for the host to read.
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

static void on_exit_clicked(GtkWidget *, gpointer)
{
    /* Remove the marker file so tablet-mode.sh's wait loop exits */
    unlink(g_marker_file);

    gtk_main_quit();
}

static void on_rotate_clicked(GtkWidget *, gpointer data)
{
    GtkWidget *btn = GTK_WIDGET(data);
    char cmd[256];

    if (g_rotation == 0) {
        g_rotation = 90;
        gtk_button_set_label(GTK_BUTTON(btn), "Rotate (Portrait)");
    } else {
        g_rotation = 0;
        gtk_button_set_label(GTK_BUTTON(btn), "Rotate (Landscape)");
    }

    /* Write rotation angle to file so the host picks it up via tail -f */
    snprintf(cmd, sizeof(cmd), "echo %d >> %s", g_rotation, g_rotation_file);
    system(cmd);
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
    GtkWidget *vbox = gtk_vbox_new(FALSE, 20);
    gtk_container_set_border_width(GTK_CONTAINER(vbox), 40);
    gtk_container_add(GTK_CONTAINER(window), vbox);

    /* Title label */
    GtkWidget *title = gtk_label_new(NULL);
    gtk_label_set_markup(GTK_LABEL(title),
        "<span font='40' weight='bold'>KINDLE TABLET MODE</span>");
    gtk_box_pack_start(GTK_BOX(vbox), title, FALSE, FALSE, 20);

    /* Subtitle label */
    GtkWidget *subtitle = gtk_label_new(NULL);
    gtk_label_set_markup(GTK_LABEL(subtitle),
        "<span font='20'>Draw on your computer.</span><span>Pen does NOT draw here.</span>");
    gtk_box_pack_start(GTK_BOX(vbox), subtitle, FALSE, FALSE, 10);

    /* Spacer */
    GtkWidget *spacer = gtk_label_new("");
    gtk_box_pack_start(GTK_BOX(vbox), spacer, TRUE, TRUE, 0);

    /* Rotate button */
    GtkWidget *rotate_btn = gtk_button_new_with_label("Rotate (Landscape)");
    gtk_widget_set_size_request(rotate_btn, 400, 120);

    PangoFontDescription *rotate_font = pango_font_description_from_string("Sans Bold 28");
    gtk_widget_modify_font(gtk_bin_get_child(GTK_BIN(rotate_btn)), rotate_font);
    pango_font_description_free(rotate_font);

    g_signal_connect(rotate_btn, "clicked", G_CALLBACK(on_rotate_clicked), rotate_btn);

    GtkWidget *rotate_align = gtk_alignment_new(0.5, 1.0, 0.0, 0.0);
    gtk_container_add(GTK_CONTAINER(rotate_align), rotate_btn);
    gtk_box_pack_end(GTK_BOX(vbox), rotate_align, FALSE, FALSE, 20);

    /* Exit button */
    GtkWidget *button = gtk_button_new_with_label("Exit Tablet Mode");
    gtk_widget_set_size_request(button, 400, 120);

    PangoFontDescription *font = pango_font_description_from_string("Sans Bold 28");
    gtk_widget_modify_font(gtk_bin_get_child(GTK_BIN(button)), font);
    pango_font_description_free(font);

    g_signal_connect(button, "clicked", G_CALLBACK(on_exit_clicked), NULL);

    GtkWidget *button_align = gtk_alignment_new(0.5, 1.0, 0.0, 0.0);
    gtk_container_add(GTK_CONTAINER(button_align), button);
    gtk_box_pack_end(GTK_BOX(vbox), button_align, FALSE, FALSE, 40);

    gtk_widget_show_all(window);

    gtk_main();

    return 0;
}

/*
 * viewer.c -- LutScope GUI LUT 查看器
 *
 * 纯 C + X11 实现，ANSI C + BSD Allman 风格
 * 预留 AI 集成拓展接口
 *
 * 编译: gcc -Wall -O2 -o lut_viewer viewer.c lut_engine.c image_io.c \\
 *           -lX11 -lXft -lXrender -lm -lpng -lfontconfig
 *
 * 用法: ./lut_viewer [image.ppm] [lut_dir]
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <dirent.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/Xatom.h>
#include <X11/keysym.h>
#include <X11/Xft/Xft.h>

#include "lut_engine.h"

/* ============================================================
 *  配置常量
 * ============================================================ */
#define WIN_W        1200
#define WIN_H        760
#define PANEL_W      280
#define ITEM_H       24
#define STATUS_H     28
#define HEADER_H     40
#define LIST_TOP     80
#define FONT_SIZE    13
#define TITLE_SIZE   16

/* ============================================================
 *  颜色
 * ============================================================ */
#define COL_BG        0x2B2B2B
#define COL_PANEL_BG  0x222222
#define COL_TEXT      0xCCCCCC
#define COL_HIGHLIGHT 0xFFAA33
#define COL_SELECT    0x3A3A5A
#define COL_ITEM_BG   0x2A2A2A
#define COL_DIVIDER   0x444444
#define COL_TITLE     0xFFBB44
#define COL_STATUS    0x888888
#define COL_LUT_NAME  0xFFDD88

/* ============================================================
 *  数据结构
 * ============================================================ */

/* LUT 列表项 */
typedef struct LutItem
{
    char           name[256];
    char           path[1024];
    int            selected;
    struct LutItem *next;
}
LutItem;

/* 全局应用状态 */
typedef struct
{
    Display *dpy;
    Window  win;
    GC      gc;
    int     screen;
    int     running;

    /* Xft */
    XftFont *font;
    XftFont *font_bold;
    XftFont *font_title;
    XftColor color_text;
    XftColor color_highlight;
    XftColor color_dim;

    /* 图像 */
    Image    *orig_image;     /* 原始全尺寸图像 */
    Image    *preview_image;  /* LUT 应用后的图像 */
    Image    *disp_image;     /* 缩放后的显示图像 */
    int      disp_w, disp_h;  /* 显示尺寸 */

    /* LUT */
    LutItem  *lut_list;
    int      lut_count;
    int      selected_idx;
    LUT3D    current_lut;
    int      lut_loaded;

    /* 文件 */
    char     image_path[1024];
    char     lut_dir[1024];

    /* 鼠标/键盘 */
    int      mouse_x, mouse_y;
    int      scroll_offset;

    /* 拓展预留: AI 标记 */
    void     *ai_handle;      /* 预留: AI 模块句柄 */
    int      ai_ready;
}
AppState;

/* ============================================================
 *  函数声明
 * ============================================================ */
static int  app_init(AppState *app, const char *image_path,
                     const char *lut_dir);
static void app_run(AppState *app);
static void app_cleanup(AppState *app);
static void draw_all(AppState *app);
static void draw_header(AppState *app);
static void draw_image_panel(AppState *app);
static void draw_lut_panel(AppState *app);
static void draw_status_bar(AppState *app);
static void apply_lut(AppState *app, int idx);
static int  scan_luts(AppState *app, const char *dir);
static void reload_image(AppState *app);

/* ============================================================
 *  工具函数
 * ============================================================ */

/* Xft 颜色创建 */
static int
xft_color_alloc(Display *dpy, XftColor *color, unsigned int hex)
{
    XRenderColor rc;
    rc.red   = ((hex >> 16) & 0xFF) * 257;
    rc.green = ((hex >> 8)  & 0xFF) * 257;
    rc.blue  = (hex & 0xFF) * 257;
    rc.alpha = 0xFFFF;
    return XftColorAllocValue(dpy, DefaultVisual(dpy, DefaultScreen(dpy)),
                              DefaultColormap(dpy, DefaultScreen(dpy)),
                              &rc, color);
}

/* ============================================================
 *  X11 初始化
 * ============================================================ */

static int
x11_init(AppState *app)
{
    Atom wm_delete;

    app->dpy = XOpenDisplay(NULL);
    if (app->dpy == NULL)
    {
        fprintf(stderr, "无法打开 X11 显示\n");
        return -1;
    }

    app->screen = DefaultScreen(app->dpy);
    app->win = XCreateSimpleWindow(
        app->dpy, RootWindow(app->dpy, app->screen),
        100, 100, WIN_W, WIN_H, 0,
        BlackPixel(app->dpy, app->screen),
        COL_BG);

    /* 窗口标题 */
    XStoreName(app->dpy, app->win, "LutScope Viewer");
    XSetIconName(app->dpy, app->win, "LutScope");

    /* WM_DELETE_WINDOW 协议 */
    wm_delete = XInternAtom(app->dpy, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(app->dpy, app->win, &wm_delete, 1);

    /* 输入事件 */
    XSelectInput(app->dpy, app->win,
                 ExposureMask | ButtonPressMask | ButtonReleaseMask |
                 PointerMotionMask | KeyPressMask | StructureNotifyMask);

    /* GC */
    app->gc = XCreateGC(app->dpy, app->win, 0, NULL);

    /* 字体 */
    app->font = XftFontOpenName(app->dpy, app->screen,
                                "sans:size=13");
    if (app->font == NULL)
    {
        app->font = XftFontOpenName(app->dpy, app->screen,
                                    "fixed:size=13");
    }
    app->font_bold = XftFontOpenName(app->dpy, app->screen,
                                     "sans:bold:size=13");
    if (app->font_bold == NULL)
    {
        app->font_bold = app->font;
    }
    app->font_title = XftFontOpenName(app->dpy, app->screen,
                                      "sans:bold:size=16");
    if (app->font_title == NULL)
    {
        app->font_title = app->font;
    }

    xft_color_alloc(app->dpy, &app->color_text, COL_TEXT);
    xft_color_alloc(app->dpy, &app->color_highlight, COL_HIGHLIGHT);
    xft_color_alloc(app->dpy, &app->color_dim, COL_STATUS);

    XMapWindow(app->dpy, app->win);
    return 0;
}

/* ============================================================
 *  LUT 扫描
 * ============================================================ */

static int
scan_luts(AppState *app, const char *dir)
{
    DIR *dp;
    struct dirent *entry;
    LutItem *item;

    /* 释放旧列表 */
    item = app->lut_list;
    while (item != NULL)
    {
        LutItem *next = item->next;
        free(item);
        item = next;
    }
    app->lut_list = NULL;
    app->lut_count = 0;
    app->selected_idx = 0;
    app->lut_loaded = 0;

    dp = opendir(dir);
    if (dp == NULL)
    {
        return -1;
    }

    while ((entry = readdir(dp)) != NULL)
    {
        const char *ext;
        char fullpath[1024];

        ext = strrchr(entry->d_name, '.');
        if (ext == NULL) continue;
        if (strcmp(ext, ".cube") != 0) continue;

        snprintf(fullpath, sizeof(fullpath), "%s/%s", dir, entry->d_name);

        item = (LutItem *)malloc(sizeof(LutItem));
        if (item == NULL) continue;

        strncpy(item->name, entry->d_name, sizeof(item->name) - 1);
        item->name[sizeof(item->name) - 1] = '\0';
        /* 去掉 .cube 后缀 */
        {
            char *dot = strrchr(item->name, '.');
            if (dot) *dot = '\0';
        }
        strncpy(item->path, fullpath, sizeof(item->path) - 1);
        item->path[sizeof(item->path) - 1] = '\0';
        item->selected = 0;
        item->next = app->lut_list;
        app->lut_list = item;
        app->lut_count++;
    }

    closedir(dp);
    return app->lut_count;
}

/* ============================================================
 *  图像加载与显示
 * ============================================================ */

static void
reload_image(AppState *app)
{
    Image *raw;
    int max_w, max_h;

    if (app->orig_image != NULL)
    {
        image_free(app->orig_image);
        free(app->orig_image);
        app->orig_image = NULL;
    }

    raw = (Image *)malloc(sizeof(Image));
    if (raw == NULL) return;

    if (image_load_ppm(app->image_path, raw) != 0 &&
        image_load_png(app->image_path, raw) != 0)
    {
        free(raw);
        return;
    }

    app->orig_image = raw;

    /* 计算显示尺寸 */
    max_w = WIN_W - PANEL_W - 30;
    max_h = WIN_H - HEADER_H - STATUS_H - 20;

    app->disp_w = raw->width;
    app->disp_h = raw->height;

    if (app->disp_w > max_w)
    {
        float scale = (float)max_w / app->disp_w;
        app->disp_w = max_w;
        app->disp_h = (int)(app->disp_h * scale);
    }
    if (app->disp_h > max_h)
    {
        float scale = (float)max_h / app->disp_h;
        app->disp_h = max_h;
        app->disp_w = (int)(app->disp_w * scale);
    }

    /* 创建预览副本 */
    if (app->preview_image != NULL)
    {
        image_free(app->preview_image);
        free(app->preview_image);
    }
    app->preview_image = image_clone(app->orig_image);

    /* 创建显示图 */
    if (app->disp_image != NULL)
    {
        image_free(app->disp_image);
        free(app->disp_image);
    }
    app->disp_image = image_scale(app->orig_image,
                                  app->disp_w, app->disp_h);

    /* 如果已有选中 LUT，应用 */
    if (app->lut_loaded && app->selected_idx >= 0)
    {
        apply_lut(app, app->selected_idx);
    }
}

/* 将 XImage 绘制到窗口 */
static void
draw_image_on_window(AppState *app, Image *img,
                     int dst_x, int dst_y,
                     int dst_w, int dst_h)
{
    XImage *ximg;
    char *data;
    int x, y;

    if (img == NULL || img->pixels == NULL) return;

    data = (char *)malloc((size_t)dst_w * dst_h * 4);
    if (data == NULL) return;

    for (y = 0; y < dst_h; y++)
    {
        for (x = 0; x < dst_w; x++)
        {
            int sx = x * img->width / dst_w;
            int sy = y * img->height / dst_h;
            int si = (sy * img->width + sx) * 3;
            int di = (y * dst_w + x) * 4;
            data[di + 0] = img->pixels[si + 2];  /* B */
            data[di + 1] = img->pixels[si + 1];  /* G */
            data[di + 2] = img->pixels[si + 0];  /* R */
            data[di + 3] = 0;
        }
    }

    ximg = XCreateImage(app->dpy, DefaultVisual(app->dpy, app->screen),
                        24, ZPixmap, 0, data,
                        dst_w, dst_h, 32, dst_w * 4);

    if (ximg != NULL)
    {
        XPutImage(app->dpy, app->win, app->gc, ximg,
                  0, 0, dst_x, dst_y, dst_w, dst_h);
        XDestroyImage(ximg);
        /* XDestroyImage 会 free data */
    }
    else
    {
        free(data);
    }
}

/* ============================================================
 *  LUT 应用
 * ============================================================ */

static void
apply_lut(AppState *app, int idx)
{
    LutItem *item;
    int i;

    /* 找到第 idx 个 LUT */
    item = app->lut_list;
    for (i = 0; i < idx && item != NULL; i++)
    {
        item = item->next;
    }
    if (item == NULL) return;

    /* 标记选中 */
    app->selected_idx = idx;
    {
        LutItem *it = app->lut_list;
        while (it != NULL)
        {
            it->selected = (it == item);
            it = it->next;
        }
    }

    /* 加载 LUT */
    if (app->preview_image != NULL)
    {
        image_free(app->preview_image);
        free(app->preview_image);
    }

    if (lut_load(item->path, &app->current_lut) == 0)
    {
        app->lut_loaded = 1;
        app->preview_image = image_clone(app->orig_image);
        if (app->preview_image != NULL)
        {
            image_apply_lut(app->preview_image, &app->current_lut);
        }
    }
    else
    {
        app->lut_loaded = 0;
        app->preview_image = image_clone(app->orig_image);
    }
}

/* ============================================================
 *  绘制函数
 * ============================================================ */

static void
draw_header(AppState *app)
{
    int w = WIN_W;

    /* 背景 */
    XSetForeground(app->dpy, app->gc, 0x1A1A2E);
    XFillRectangle(app->dpy, app->win, app->gc, 0, 0, w, HEADER_H);

    /* 标题 */
    XftDraw *xd = XftDrawCreate(app->dpy, app->win,
                                 DefaultVisual(app->dpy, app->screen),
                                 DefaultColormap(app->dpy, app->screen));
    if (xd)
    {
        XGlyphInfo extents;
        const char *title = "LutScope Viewer";
        XftTextExtentsUtf8(app->dpy, app->font_title,
                           (const unsigned char *)title, strlen(title),
                           &extents);
        XftDrawStringUtf8(xd, &app->color_highlight,
                          app->font_title,
                          15, HEADER_H / 2 + extents.y / 2,
                          (const unsigned char *)title, strlen(title));

        /* 图像名右侧 */
        if (app->image_path[0])
        {
            const char *base = strrchr(app->image_path, '/');
            if (base) base++; else base = app->image_path;
            char info[256];
            snprintf(info, sizeof(info), "%s  |  %d LUTs",
                     base, app->lut_count);
            XftDrawStringUtf8(xd, &app->color_dim, app->font,
                              w - 250, HEADER_H / 2 + 5,
                              (const unsigned char *)info, strlen(info));
        }
        XftDrawDestroy(xd);
    }

    /* 底线 */
    XSetForeground(app->dpy, app->gc, COL_DIVIDER);
    XFillRectangle(app->dpy, app->win, app->gc, 0, HEADER_H - 1, w, 1);
}

static void
draw_image_panel(AppState *app)
{
    int img_x, img_y;
    int panel_w = WIN_W - PANEL_W;

    /* 背景 */
    XSetForeground(app->dpy, app->gc, COL_BG);
    XFillRectangle(app->dpy, app->win, app->gc,
                   0, HEADER_H, panel_w, WIN_H - HEADER_H - STATUS_H);

    /* 图像居中 */
    img_x = (panel_w - app->disp_w) / 2;
    img_y = HEADER_H + ((WIN_H - HEADER_H - STATUS_H) - app->disp_h) / 2;

    /* 原始图 (左半) */
    if (app->orig_image != NULL)
    {
        draw_image_on_window(app, app->orig_image,
                             img_x, img_y,
                             app->disp_w / 2, app->disp_h);
    }

    /* LUT 预览 (右半) */
    if (app->preview_image != NULL)
    {
        draw_image_on_window(app, app->preview_image,
                             img_x + app->disp_w / 2, img_y,
                             app->disp_w / 2, app->disp_h);
    }

    /* 分割线 */
    int split_x = img_x + app->disp_w / 2;
    XSetForeground(app->dpy, app->gc, COL_HIGHLIGHT);
    XFillRectangle(app->dpy, app->win, app->gc,
                   split_x - 1, img_y, 3, app->disp_h);

    /* 标签: Original | LUT */
    {
        XftDraw *xd = XftDrawCreate(app->dpy, app->win,
                                     DefaultVisual(app->dpy, app->screen),
                                     DefaultColormap(app->dpy, app->screen));
        if (xd)
        {
            const char *orig_label = "Original";
            const char *lut_label = app->lut_loaded ?
                                    app->current_lut.title : "No LUT";
            if (lut_label[0] == '\0')
            {
                /* 用文件名代替 */
                LutItem *item = app->lut_list;
                int i;
                for (i = 0; i < app->selected_idx && item; i++)
                    item = item->next;
                if (item) lut_label = item->name;
            }

            XftDrawStringUtf8(xd, &app->color_dim, app->font,
                              img_x + 10, img_y + app->disp_h + 16,
                              (const unsigned char *)orig_label,
                              strlen(orig_label));
            XftDrawStringUtf8(xd, &app->color_highlight, app->font,
                              img_x + app->disp_w / 2 + 10,
                              img_y + app->disp_h + 16,
                              (const unsigned char *)lut_label,
                              strlen(lut_label));
            XftDrawDestroy(xd);
        }
    }
}

static void
draw_lut_panel(AppState *app)
{
    int x = WIN_W - PANEL_W;
    int y = HEADER_H;
    int w = PANEL_W;
    LutItem *item;
    int i;

    /* 背景 */
    XSetForeground(app->dpy, app->gc, COL_PANEL_BG);
    XFillRectangle(app->dpy, app->win, app->gc,
                   x, y, w, WIN_H - HEADER_H - STATUS_H);

    /* 左分割线 */
    XSetForeground(app->dpy, app->gc, COL_DIVIDER);
    XFillRectangle(app->dpy, app->win, app->gc,
                   x, y, 1, WIN_H - HEADER_H - STATUS_H);

    /* 标题 */
    {
        XftDraw *xd = XftDrawCreate(app->dpy, app->win,
                                     DefaultVisual(app->dpy, app->screen),
                                     DefaultColormap(app->dpy, app->screen));
        if (xd)
        {
            char title[64];
            snprintf(title, sizeof(title), "LUTs (%d)", app->lut_count);
            XftDrawStringUtf8(xd, &app->color_highlight,
                              app->font_bold,
                              x + 12, y + 22,
                              (const unsigned char *)title, strlen(title));
            XftDrawDestroy(xd);
        }
    }
    y += 35;

    /* LUT 列表 */
    item = app->lut_list;
    i = 0;
    while (item != NULL && y < WIN_H - STATUS_H - 10)
    {
        if (i >= app->scroll_offset)
        {
            int item_y = y + (i - app->scroll_offset) * ITEM_H;

            if (item_y + ITEM_H > WIN_H - STATUS_H - 10) break;

            /* 选中高亮 */
            if (item->selected)
            {
                XSetForeground(app->dpy, app->gc, COL_SELECT);
                XFillRectangle(app->dpy, app->win, app->gc,
                               x + 4, item_y, w - 8, ITEM_H - 2);
            }

            /* 名称 */
            {
                XftDraw *xd = XftDrawCreate(
                    app->dpy, app->win,
                    DefaultVisual(app->dpy, app->screen),
                    DefaultColormap(app->dpy, app->screen));
                if (xd)
                {
                    XftColor *c = item->selected ?
                                  &app->color_highlight : &app->color_text;
                    XftDrawStringUtf8(xd, c, app->font,
                                      x + 14, item_y + ITEM_H / 2 + 5,
                                      (const unsigned char *)item->name,
                                      strlen(item->name));

                    /* 选中标记 */
                    if (item->selected)
                    {
                        XftDrawStringUtf8(xd, &app->color_highlight,
                                          app->font_bold,
                                          x + w - 30, item_y + ITEM_H / 2 + 5,
                                          (const unsigned char *)"▶", 3);
                    }
                    XftDrawDestroy(xd);
                }
            }
        }
        item = item->next;
        i++;
    }

    /* 底部提示 — 预留 AI 功能 */
    {
        int tip_y = WIN_H - STATUS_H - 40;
        XftDraw *xd = XftDrawCreate(app->dpy, app->win,
                                     DefaultVisual(app->dpy, app->screen),
                                     DefaultColormap(app->dpy, app->screen));
        if (xd)
        {
            const char *tip = "[AI] — 预留拓展接口";
            XftDrawStringUtf8(xd, &app->color_dim, app->font,
                              x + 14, tip_y,
                              (const unsigned char *)tip, strlen(tip));

            const char *hint = "单击 LUT 预览  滚轮翻页";
            XftDrawStringUtf8(xd, &app->color_dim, app->font,
                              x + 14, tip_y + 18,
                              (const unsigned char *)hint, strlen(hint));
            XftDrawDestroy(xd);
        }
    }
}

static void
draw_status_bar(AppState *app)
{
    int y = WIN_H - STATUS_H;

    /* 背景 */
    XSetForeground(app->dpy, app->gc, 0x1A1A1A);
    XFillRectangle(app->dpy, app->win, app->gc, 0, y, WIN_W, STATUS_H);

    /* 顶线 */
    XSetForeground(app->dpy, app->gc, COL_DIVIDER);
    XFillRectangle(app->dpy, app->win, app->gc, 0, y, WIN_W, 1);

    /* 状态信息 */
    {
        XftDraw *xd = XftDrawCreate(app->dpy, app->win,
                                     DefaultVisual(app->dpy, app->screen),
                                     DefaultColormap(app->dpy, app->screen));
        if (xd)
        {
            char status[256];
            const char *img_base = strrchr(app->image_path, '/');
            if (img_base) img_base++; else img_base = app->image_path;

            snprintf(status, sizeof(status),
                     " %s  |  %dx%d  |  LUT: %s",
                     img_base,
                     app->orig_image ? app->orig_image->width : 0,
                     app->orig_image ? app->orig_image->height : 0,
                     app->lut_loaded ? app->current_lut.title : "(none)");

            XftDrawStringUtf8(xd, &app->color_dim, app->font,
                              10, y + STATUS_H / 2 + 5,
                              (const unsigned char *)status, strlen(status));

            /* 右侧: 拓展预留 */
            const char *ext = "Extensible | C89 + X11";
            XftDrawStringUtf8(xd, &app->color_dim, app->font,
                              WIN_W - 200, y + STATUS_H / 2 + 5,
                              (const unsigned char *)ext, strlen(ext));
            XftDrawDestroy(xd);
        }
    }
}

static void
draw_all(AppState *app)
{
    draw_header(app);
    draw_image_panel(app);
    draw_lut_panel(app);
    draw_status_bar(app);
}

/* ============================================================
 *  事件处理
 * ============================================================ */

static void
handle_click(AppState *app, int mx, int my)
{
    int panel_x = WIN_W - PANEL_W;
    int list_y = HEADER_H + 35;
    LutItem *item;
    int i;

    /* 点击 LUT 列表区 */
    if (mx >= panel_x && my >= list_y)
    {
        int relative_y = my - list_y;
        int idx = relative_y / ITEM_H + app->scroll_offset;

        item = app->lut_list;
        for (i = 0; i < idx && item != NULL; i++)
        {
            item = item->next;
        }
        if (item != NULL)
        {
            apply_lut(app, idx);
            /* 重新缩放预览 */
            if (app->preview_image)
            {
                if (app->disp_image)
                {
                    image_free(app->disp_image);
                    free(app->disp_image);
                }
                app->disp_image = image_scale(app->preview_image,
                                              app->disp_w, app->disp_h);
            }
        }
    }
}

/* ============================================================
 *  主循环
 * ============================================================ */

static void
app_run(AppState *app)
{
    XEvent ev;

    app->running = 1;

    while (app->running)
    {
        while (XPending(app->dpy) > 0)
        {
            XNextEvent(app->dpy, &ev);

            switch (ev.type)
            {
                case Expose:
                {
                    draw_all(app);
                    break;
                }
                case ConfigureNotify:
                {
                    /* 窗口大小变化 — 暂不处理动态 resize */
                    break;
                }
                case ButtonPress:
                {
                    if (ev.xbutton.button == Button4)
                    {
                        /* 滚轮上 */
                        if (app->scroll_offset > 0)
                            app->scroll_offset--;
                    }
                    else if (ev.xbutton.button == Button5)
                    {
                        /* 滚轮下 */
                        app->scroll_offset++;
                    }
                    else if (ev.xbutton.button == Button1)
                    {
                        handle_click(app, ev.xbutton.x, ev.xbutton.y);
                    }
                    draw_all(app);
                    break;
                }
                case KeyPress:
                {
                    KeySym ks = XLookupKeysym(&ev.xkey, 0);
                    if (ks == XK_q || ks == XK_Q ||
                        ks == XK_Escape)
                    {
                        app->running = 0;
                    }
                    else if (ks == XK_Up)
                    {
                        if (app->selected_idx > 0)
                        {
                            apply_lut(app, app->selected_idx - 1);
                            if (app->preview_image)
                            {
                                if (app->disp_image)
                                {
                                    image_free(app->disp_image);
                                    free(app->disp_image);
                                }
                                app->disp_image = image_scale(
                                    app->preview_image,
                                    app->disp_w, app->disp_h);
                            }
                        }
                    }
                    else if (ks == XK_Down)
                    {
                        if (app->selected_idx < app->lut_count - 1)
                        {
                            apply_lut(app, app->selected_idx + 1);
                            if (app->preview_image)
                            {
                                if (app->disp_image)
                                {
                                    image_free(app->disp_image);
                                    free(app->disp_image);
                                }
                                app->disp_image = image_scale(
                                    app->preview_image,
                                    app->disp_w, app->disp_h);
                            }
                        }
                    }
                    else if (ks == XK_r || ks == XK_R)
                    {
                        reload_image(app);
                    }
                    draw_all(app);
                    break;
                }
                case ClientMessage:
                {
                    Atom wm_delete = XInternAtom(app->dpy,
                                                 "WM_DELETE_WINDOW", False);
                    if ((Atom)ev.xclient.data.l[0] == wm_delete)
                    {
                        app->running = 0;
                    }
                    break;
                }
            }
        }

        /* 空闲时小睡 (用 nanosleep 替代 usleep) */
        {
            struct timespec ts;
            ts.tv_sec = 0;
            ts.tv_nsec = 10000000L;  /* 10ms */
            nanosleep(&ts, NULL);
        }
    }
}

/* ============================================================
 *  清理
 * ============================================================ */

static void
app_cleanup(AppState *app)
{
    LutItem *item;

    app->running = 0;

    if (app->orig_image)
    {
        image_free(app->orig_image);
        free(app->orig_image);
    }
    if (app->preview_image)
    {
        image_free(app->preview_image);
        free(app->preview_image);
    }
    if (app->disp_image)
    {
        image_free(app->disp_image);
        free(app->disp_image);
    }

    if (app->lut_loaded)
    {
        lut_free(&app->current_lut);
    }

    item = app->lut_list;
    while (item)
    {
        LutItem *next = item->next;
        free(item);
        item = next;
    }

    if (app->font)
        XftFontClose(app->dpy, app->font);
    if (app->font_bold && app->font_bold != app->font)
        XftFontClose(app->dpy, app->font_bold);
    if (app->font_title && app->font_title != app->font)
        XftFontClose(app->dpy, app->font_title);

    XftColorFree(app->dpy, DefaultVisual(app->dpy, app->screen),
                 DefaultColormap(app->dpy, app->screen),
                 &app->color_text);
    XftColorFree(app->dpy, DefaultVisual(app->dpy, app->screen),
                 DefaultColormap(app->dpy, app->screen),
                 &app->color_highlight);
    XftColorFree(app->dpy, DefaultVisual(app->dpy, app->screen),
                 DefaultColormap(app->dpy, app->screen),
                 &app->color_dim);

    if (app->gc)
        XFreeGC(app->dpy, app->gc);
    if (app->win)
        XDestroyWindow(app->dpy, app->win);
    if (app->dpy)
        XCloseDisplay(app->dpy);
}

/* ============================================================
 *  初始化
 * ============================================================ */

static int
app_init(AppState *app, const char *image_path,
         const char *lut_dir)
{
    memset(app, 0, sizeof(AppState));

    app->scroll_offset = 0;
    app->selected_idx = -1;

    /* 保存路径 */
    if (image_path)
    {
        strncpy(app->image_path, image_path, sizeof(app->image_path) - 1);
    }
    if (lut_dir)
    {
        strncpy(app->lut_dir, lut_dir, sizeof(app->lut_dir) - 1);
    }
    else
    {
        strncpy(app->lut_dir, ".", sizeof(app->lut_dir) - 1);
    }

    /* X11 初始化 */
    if (x11_init(app) != 0)
    {
        return -1;
    }

    /* 扫描 LUT */
    scan_luts(app, app->lut_dir);

    /* 加载图像 */
    if (app->image_path[0])
    {
        reload_image(app);
    }

    /* 自动选中第一个 LUT */
    if (app->lut_count > 0 && app->orig_image != NULL)
    {
        apply_lut(app, 0);
        if (app->preview_image)
        {
            app->disp_image = image_scale(app->preview_image,
                                          app->disp_w, app->disp_h);
        }
    }

    /* 预留: AI 模块初始化标记 */
    app->ai_handle = NULL;
    app->ai_ready = 0;

    return 0;
}

/* ============================================================
 *  主入口
 * ============================================================ */

int
main(int argc, char *argv[])
{
    AppState app;
    const char *image_path = NULL;
    const char *lut_dir = ".";
    int ret;

    if (argc > 1)
    {
        image_path = argv[1];
    }
    if (argc > 2)
    {
        lut_dir = argv[2];
    }

    if (image_path == NULL)
    {
        fprintf(stderr,
            "用法: %s <image.ppm/png> [lut_directory]\n"
            "示例: %s photo.ppm ./luts\n",
            argv[0], argv[0]);
        return 1;
    }

    ret = app_init(&app, image_path, lut_dir);
    if (ret != 0)
    {
        fprintf(stderr, "初始化失败\n");
        return 1;
    }

    app_run(&app);
    app_cleanup(&app);
    return 0;
}

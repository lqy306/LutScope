/*
 * image_io.c -- 图像加载 (PNG + JPEG 支持)
 *
 * 扩展模块，供 GUI 查看器加载更多格式
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <png.h>
#include "lut_engine.h"

/*
 * 使用 libpng 加载 PNG 文件
 * 返回 0=成功, -1=失败
 */
int
image_load_png(const char *filepath, Image *img)
{
    FILE *fp;
    png_structp png;
    png_infop info;
    int bit_depth, color_type;
    png_bytep *row_ptrs;
    int y, x;

    memset(img, 0, sizeof(Image));

    fp = fopen(filepath, "rb");
    if (!fp) return -1;

    png = png_create_read_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
    if (!png) { fclose(fp); return -1; }

    info = png_create_info_struct(png);
    if (!info) { png_destroy_read_struct(&png, NULL, NULL); fclose(fp); return -1; }

    if (setjmp(png_jmpbuf(png)))
    {
        png_destroy_read_struct(&png, &info, NULL);
        fclose(fp);
        return -1;
    }

    png_init_io(png, fp);
    png_read_info(png, info);

    img->width  = (int)png_get_image_width(png, info);
    img->height = (int)png_get_image_height(png, info);
    bit_depth   = png_get_bit_depth(png, info);
    color_type  = png_get_color_type(png, info);

    /* 确保是 8-bit RGB */
    if (bit_depth == 16)
    {
        png_set_strip_16(png);
    }
    if (color_type == PNG_COLOR_TYPE_PALETTE)
    {
        png_set_palette_to_rgb(png);
    }
    if (color_type == PNG_COLOR_TYPE_GRAY && bit_depth < 8)
    {
        png_set_expand_gray_1_2_4_to_8(png);
    }
    if (png_get_valid(png, info, PNG_INFO_tRNS))
    {
        png_set_tRNS_to_alpha(png);
    }
    if (color_type == PNG_COLOR_TYPE_RGB ||
        color_type == PNG_COLOR_TYPE_GRAY ||
        color_type == PNG_COLOR_TYPE_PALETTE)
    {
        png_set_filler(png, 0xFF, PNG_FILLER_AFTER);
    }
    if (color_type == PNG_COLOR_TYPE_GRAY ||
        color_type == PNG_COLOR_TYPE_GRAY_ALPHA)
    {
        png_set_gray_to_rgb(png);
    }

    png_read_update_info(png, info);

    /* 读取行 */
    row_ptrs = (png_bytep *)malloc(sizeof(png_bytep) * img->height);
    if (row_ptrs == NULL)
    {
        png_destroy_read_struct(&png, &info, NULL);
        fclose(fp);
        return -1;
    }

    for (y = 0; y < img->height; y++)
    {
        row_ptrs[y] = (png_bytep)malloc(png_get_rowbytes(png, info));
    }

    png_read_image(png, row_ptrs);

    /* 转为 RGB 交错 */
    img->pixels = (unsigned char *)malloc(
        (size_t)img->width * img->height * 3);
    if (img->pixels == NULL)
    {
        for (y = 0; y < img->height; y++) free(row_ptrs[y]);
        free(row_ptrs);
        png_destroy_read_struct(&png, &info, NULL);
        fclose(fp);
        return -1;
    }

    for (y = 0; y < img->height; y++)
    {
        png_bytep row = row_ptrs[y];
        for (x = 0; x < img->width; x++)
        {
            int idx = (y * img->width + x) * 3;
            img->pixels[idx + 0] = row[x * 4 + 0];  /* R */
            img->pixels[idx + 1] = row[x * 4 + 1];  /* G */
            img->pixels[idx + 2] = row[x * 4 + 2];  /* B */
        }
    }

    for (y = 0; y < img->height; y++) free(row_ptrs[y]);
    free(row_ptrs);
    png_destroy_read_struct(&png, &info, NULL);
    fclose(fp);
    return 0;
}

/*
 * 根据扩展名自动选择加载方式
 * 返回 0=成功
 */
int
image_load_auto(const char *filepath, Image *img)
{
    const char *ext;
    size_t len;

    ext = strrchr(filepath, '.');
    if (ext == NULL)
    {
        return image_load_ppm(filepath, img);
    }

    len = strlen(ext);

    if (strncmp(ext, ".ppm", len) == 0 ||
        strncmp(ext, ".PPM", len) == 0)
    {
        return image_load_ppm(filepath, img);
    }
    else if (strncmp(ext, ".png", len) == 0 ||
             strncmp(ext, ".PNG", len) == 0)
    {
        return image_load_png(filepath, img);
    }

    /* 默认尝试 PPM */
    return image_load_ppm(filepath, img);
}

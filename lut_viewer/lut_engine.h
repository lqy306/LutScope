/*
 * lut_engine.h -- 3D LUT 加载与插值引擎
 *
 * 功能: 解析 .cube 格式 LUT，支持四面体/三线性插值
 */

#ifndef LUT_ENGINE_H
#define LUT_ENGINE_H

#ifdef __cplusplus
extern "C" {
#endif

/* 最大 LUT 尺寸 */
#define LUT_MAX_SIZE  256
#define LUT_MAX_TITLE 256

/* LUT 数据结构 */
typedef struct
{
    int   size;                  /* 网格尺寸                         */
    float *data;                 /* 展平数据: data[b][g][r][3]       */
    char  title[LUT_MAX_TITLE];  /* LUT 标题                         */
    float dom_min[3];            /* 定义域最小值                     */
    float dom_max[3];            /* 定义域最大值                     */
}
LUT3D;

/* ---- LUT 操作 ---- */

/* 加载 .cube 文件, 返回 0=成功 */
int lut_load(const char *filepath, LUT3D *lut);

/* 释放 LUT */
void lut_free(LUT3D *lut);

/* 四面体插值应用 LUT */
void lut_apply_tetrahedral(const LUT3D *lut,
                           float r, float g, float b,
                           float *or_, float *og_, float *ob_);

/* ---- 图像数据 ---- */

typedef struct
{
    int           width;
    int           height;
    unsigned char *pixels;   /* RGB 交错, 每通道 8-bit */
}
Image;

/* 加载 PPM P6 图像, 返回 0=成功 */
int image_load_ppm(const char *filepath, Image *img);

/* 加载 PNG 图像, 返回 0=成功 (需 libpng) */
int image_load_png(const char *filepath, Image *img);

/* 释放图像 */
void image_free(Image *img);

/* 将 LUT 应用到图像 (原地修改) */
void image_apply_lut(Image *img, const LUT3D *lut);

/* 缩放图像到新尺寸, 返回新图像指针 (调用者需 free) */
Image *image_scale(const Image *src, int new_w, int new_h);

/* 复制图像 */
Image *image_clone(const Image *src);

#ifdef __cplusplus
}
#endif

#endif /* LUT_ENGINE_H */

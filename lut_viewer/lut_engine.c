/*
 * lut_engine.c -- 3D LUT 加载与插值引擎
 *
 * 从 lut_tool.c 提取的核心引擎，供 GUI 查看器使用
 * ANSI C + BSD Allman 风格
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <errno.h>
#include "lut_engine.h"

/* ============================================================
 *  常量
 * ============================================================ */
#define MAX_LINE_LEN  4096

/* 钳位到 [0, 1] */
#define CLAMP01(x)  (((x) < 0.0f) ? 0.0f : (((x) > 1.0f) ? 1.0f : (x)))

/* ============================================================
 *  工具函数
 * ============================================================ */

static char *
str_trim(char *s)
{
    char *end;

    while (*s == ' ' || *s == '\t' || *s == '\r')
    {
        s++;
    }
    if (*s == '\0')
    {
        return s;
    }
    end = s + strlen(s) - 1;
    while (end > s && (*end == ' ' || *end == '\t' ||
           *end == '\r' || *end == '\n'))
    {
        end--;
    }
    *(end + 1) = '\0';
    return s;
}

/* ============================================================
 *  LUT 加载 (.cube)
 * ============================================================ */

int
lut_load(const char *filepath, LUT3D *lut)
{
    FILE *fp;
    char line[MAX_LINE_LEN];
    int  line_num;
    int  data_count;
    int  expected;
    int  parsing_data;
    int  idx;
    char *p;
    float r, g, b;
    char *q, *rq;

    line_num = 0;
    data_count = 0;
    parsing_data = 0;

    memset(lut, 0, sizeof(LUT3D));
    lut->size = 0;
    lut->dom_min[0] = 0.0f;
    lut->dom_min[1] = 0.0f;
    lut->dom_min[2] = 0.0f;
    lut->dom_max[0] = 1.0f;
    lut->dom_max[1] = 1.0f;
    lut->dom_max[2] = 1.0f;
    lut->data = NULL;

    fp = fopen(filepath, "r");
    if (!fp)
    {
        fprintf(stderr, "错误: 无法打开 LUT '%s': %s\n",
                filepath, strerror(errno));
        return -1;
    }

    /* 第一遍: 读取头部 */
    while (fgets(line, sizeof(line), fp) != NULL)
    {
        line_num++;
        p = str_trim(line);
        if (*p == '\0' || *p == '#')
        {
            continue;
        }
        if (!parsing_data)
        {
            if (strncmp(p, "TITLE", 5) == 0)
            {
                q = strchr(p, '"');
                if (q != NULL)
                {
                    q++;
                    rq = strchr(q, '"');
                    if (rq != NULL) *rq = '\0';
                    strncpy(lut->title, q, LUT_MAX_TITLE - 1);
                    lut->title[LUT_MAX_TITLE - 1] = '\0';
                }
            }
            else if (strncmp(p, "LUT_3D_SIZE", 11) == 0)
            {
                lut->size = atoi(p + 11);
                if (lut->size < 2 || lut->size > LUT_MAX_SIZE)
                {
                    fprintf(stderr, "不支持的尺寸 %d\n", lut->size);
                    fclose(fp);
                    return -1;
                }
            }
            else if (strncmp(p, "DOMAIN_MIN", 10) == 0)
            {
                sscanf(p + 10, "%f %f %f",
                       &lut->dom_min[0], &lut->dom_min[1], &lut->dom_min[2]);
            }
            else if (strncmp(p, "DOMAIN_MAX", 10) == 0)
            {
                sscanf(p + 10, "%f %f %f",
                       &lut->dom_max[0], &lut->dom_max[1], &lut->dom_max[2]);
            }
        }
        if (*p == '-' || *p == '+' || *p == '.' || (*p >= '0' && *p <= '9'))
        {
            if (lut->size == 0)
            {
                fprintf(stderr, "在 LUT_3D_SIZE 前发现数据\n");
                fclose(fp);
                return -1;
            }
            parsing_data = 1;
            data_count++;
        }
    }

    if (lut->size == 0)
    {
        fprintf(stderr, "未找到 LUT_3D_SIZE\n");
        fclose(fp);
        return -1;
    }

    expected = lut->size * lut->size * lut->size;
    if (data_count != expected)
    {
        fprintf(stderr, "数据行数: 期望 %d, 实际 %d\n",
                expected, data_count);
        fclose(fp);
        return -1;
    }

    /* 分配并重新读取 */
    lut->data = (float *)malloc((size_t)expected * 3 * sizeof(float));
    if (lut->data == NULL)
    {
        fprintf(stderr, "内存分配失败\n");
        fclose(fp);
        return -1;
    }

    rewind(fp);
    line_num = 0;
    parsing_data = 0;
    idx = 0;

    while (fgets(line, sizeof(line), fp) != NULL)
    {
        line_num++;
        p = str_trim(line);
        if (*p == '\0' || *p == '#') continue;
        if (!parsing_data)
        {
            if (strncmp(p, "TITLE", 5) == 0 ||
                strncmp(p, "LUT_3D_SIZE", 11) == 0 ||
                strncmp(p, "DOMAIN_MIN", 10) == 0 ||
                strncmp(p, "DOMAIN_MAX", 10) == 0)
            {
                continue;
            }
        }
        if (*p == '-' || *p == '+' || *p == '.' || (*p >= '0' && *p <= '9'))
        {
            parsing_data = 1;
            if (sscanf(p, "%f %f %f", &r, &g, &b) == 3)
            {
                if (idx < expected * 3)
                {
                    lut->data[idx++] = CLAMP01(r);
                    lut->data[idx++] = CLAMP01(g);
                    lut->data[idx++] = CLAMP01(b);
                }
            }
        }
    }

    fclose(fp);
    return 0;
}

void
lut_free(LUT3D *lut)
{
    if (lut->data != NULL)
    {
        free(lut->data);
        lut->data = NULL;
    }
    lut->size = 0;
}

/* ============================================================
 *  四面体插值
 * ============================================================ */

void
lut_apply_tetrahedral(const LUT3D *lut,
                      float r, float g, float b,
                      float *or_, float *og_, float *ob_)
{
    float size_f;
    float rs, gs, bs;
    int ri, gi, bi;
    float rf, gf, bf;
    int s;
    float *d;
    float *c000, *c100, *c010, *c110;
    float *c001, *c101, *c011, *c111;
    float or_val, og_val, ob_val;

    size_f = (float)(lut->size - 1);
    rs = r * size_f;
    gs = g * size_f;
    bs = b * size_f;

    ri = (int)rs;
    gi = (int)gs;
    bi = (int)bs;
    rf = rs - ri;
    gf = gs - gi;
    bf = bs - bi;

    if (ri >= lut->size - 1) { ri = lut->size - 2; rf = 1.0f; }
    if (gi >= lut->size - 1) { gi = lut->size - 2; gf = 1.0f; }
    if (bi >= lut->size - 1) { bi = lut->size - 2; bf = 1.0f; }

    s = lut->size;
    d = lut->data;

    c000 = d + ((bi * s + gi) * s + ri) * 3;
    c100 = d + ((bi * s + gi) * s + ri + 1) * 3;
    c010 = d + ((bi * s + gi + 1) * s + ri) * 3;
    c110 = d + ((bi * s + gi + 1) * s + ri + 1) * 3;
    c001 = d + (((bi + 1) * s + gi) * s + ri) * 3;
    c101 = d + (((bi + 1) * s + gi) * s + ri + 1) * 3;
    c011 = d + (((bi + 1) * s + gi + 1) * s + ri) * 3;
    c111 = d + (((bi + 1) * s + gi + 1) * s + ri + 1) * 3;

    if (rf >= gf && gf >= bf)
    {
        or_val = c000[0]*(1.0f-rf) + c100[0]*(rf-gf) +
                 c110[0]*(gf-bf) + c111[0]*bf;
        og_val = c000[1]*(1.0f-rf) + c100[1]*(rf-gf) +
                 c110[1]*(gf-bf) + c111[1]*bf;
        ob_val = c000[2]*(1.0f-rf) + c100[2]*(rf-gf) +
                 c110[2]*(gf-bf) + c111[2]*bf;
    }
    else if (rf >= bf && bf >= gf)
    {
        or_val = c000[0]*(1.0f-rf) + c100[0]*(rf-bf) +
                 c101[0]*(bf-gf) + c111[0]*gf;
        og_val = c000[1]*(1.0f-rf) + c100[1]*(rf-bf) +
                 c101[1]*(bf-gf) + c111[1]*gf;
        ob_val = c000[2]*(1.0f-rf) + c100[2]*(rf-bf) +
                 c101[2]*(bf-gf) + c111[2]*gf;
    }
    else if (gf >= rf && rf >= bf)
    {
        or_val = c000[0]*(1.0f-gf) + c010[0]*(gf-rf) +
                 c110[0]*(rf-bf) + c111[0]*bf;
        og_val = c000[1]*(1.0f-gf) + c010[1]*(gf-rf) +
                 c110[1]*(rf-bf) + c111[1]*bf;
        ob_val = c000[2]*(1.0f-gf) + c010[2]*(gf-rf) +
                 c110[2]*(rf-bf) + c111[2]*bf;
    }
    else if (gf >= bf && bf >= rf)
    {
        or_val = c000[0]*(1.0f-gf) + c010[0]*(gf-bf) +
                 c011[0]*(bf-rf) + c111[0]*rf;
        og_val = c000[1]*(1.0f-gf) + c010[1]*(gf-bf) +
                 c011[1]*(bf-rf) + c111[1]*rf;
        ob_val = c000[2]*(1.0f-gf) + c010[2]*(gf-bf) +
                 c011[2]*(bf-rf) + c111[2]*rf;
    }
    else if (bf >= rf && rf >= gf)
    {
        or_val = c000[0]*(1.0f-bf) + c001[0]*(bf-rf) +
                 c101[0]*(rf-gf) + c111[0]*gf;
        og_val = c000[1]*(1.0f-bf) + c001[1]*(bf-rf) +
                 c101[1]*(rf-gf) + c111[1]*gf;
        ob_val = c000[2]*(1.0f-bf) + c001[2]*(bf-rf) +
                 c101[2]*(rf-gf) + c111[2]*gf;
    }
    else
    {
        or_val = c000[0]*(1.0f-bf) + c001[0]*(bf-gf) +
                 c011[0]*(gf-rf) + c111[0]*rf;
        og_val = c000[1]*(1.0f-bf) + c001[1]*(bf-gf) +
                 c011[1]*(gf-rf) + c111[1]*rf;
        ob_val = c000[2]*(1.0f-bf) + c001[2]*(bf-gf) +
                 c011[2]*(gf-rf) + c111[2]*rf;
    }

    *or_ = CLAMP01(or_val);
    *og_ = CLAMP01(og_val);
    *ob_ = CLAMP01(ob_val);
}

/* ============================================================
 *  图像操作
 * ============================================================ */

int
image_load_ppm(const char *filepath, Image *img)
{
    FILE *fp;
    char header[1024];
    int  w, h, max_val;
    size_t n_pixels;

    memset(img, 0, sizeof(Image));

    fp = fopen(filepath, "rb");
    if (!fp)
    {
        return -1;
    }

    if (fgets(header, sizeof(header), fp) == NULL ||
        header[0] != 'P' || header[1] != '6')
    {
        fclose(fp);
        return -1;
    }

    while (1)
    {
        if (fgets(header, sizeof(header), fp) == NULL)
        {
            fclose(fp);
            return -1;
        }
        if (header[0] != '#') break;
    }

    if (sscanf(header, "%d %d", &w, &h) != 2)
    {
        fclose(fp);
        return -1;
    }

    if (fgets(header, sizeof(header), fp) == NULL)
    {
        fclose(fp);
        return -1;
    }
    max_val = atoi(header);
    if (max_val <= 0 || max_val > 65535)
    {
        fclose(fp);
        return -1;
    }

    img->width  = w;
    img->height = h;
    n_pixels = (size_t)w * h;
    img->pixels = (unsigned char *)malloc(n_pixels * 3);
    if (img->pixels == NULL)
    {
        fclose(fp);
        return -1;
    }

    if (max_val <= 255)
    {
        if (fread(img->pixels, 3, n_pixels, fp) != n_pixels)
        {
            free(img->pixels);
            img->pixels = NULL;
            fclose(fp);
            return -1;
        }
    }
    else
    {
        /* 16-bit -> 8-bit */
        size_t i;
        for (i = 0; i < n_pixels; i++)
        {
            int hi, lo;
            hi = fgetc(fp); lo = fgetc(fp);
            if (hi == EOF || lo == EOF) break;
            img->pixels[i * 3 + 0] = (unsigned char)(((hi << 8) | lo) * 255 / max_val);
            hi = fgetc(fp); lo = fgetc(fp);
            if (hi == EOF || lo == EOF) break;
            img->pixels[i * 3 + 1] = (unsigned char)(((hi << 8) | lo) * 255 / max_val);
            hi = fgetc(fp); lo = fgetc(fp);
            if (hi == EOF || lo == EOF) break;
            img->pixels[i * 3 + 2] = (unsigned char)(((hi << 8) | lo) * 255 / max_val);
        }
    }

    fclose(fp);
    return 0;
}

void
image_free(Image *img)
{
    if (img->pixels != NULL)
    {
        free(img->pixels);
        img->pixels = NULL;
    }
    img->width = 0;
    img->height = 0;
}

void
image_apply_lut(Image *img, const LUT3D *lut)
{
    size_t n;
    unsigned char *p;
    size_t i;

    n = (size_t)img->width * img->height;
    p = img->pixels;

    for (i = 0; i < n; i++)
    {
        float r = p[0] / 255.0f;
        float g = p[1] / 255.0f;
        float b = p[2] / 255.0f;
        float or_, og_, ob_;

        lut_apply_tetrahedral(lut, r, g, b, &or_, &og_, &ob_);

        p[0] = (unsigned char)(or_ * 255.0f + 0.5f);
        p[1] = (unsigned char)(og_ * 255.0f + 0.5f);
        p[2] = (unsigned char)(ob_ * 255.0f + 0.5f);
        p += 3;
    }
}

Image *
image_clone(const Image *src)
{
    Image *dst;
    size_t n;

    dst = (Image *)malloc(sizeof(Image));
    if (dst == NULL) return NULL;

    dst->width = src->width;
    dst->height = src->height;
    n = (size_t)src->width * src->height * 3;
    dst->pixels = (unsigned char *)malloc(n);
    if (dst->pixels == NULL)
    {
        free(dst);
        return NULL;
    }
    memcpy(dst->pixels, src->pixels, n);
    return dst;
}

Image *
image_scale(const Image *src, int new_w, int new_h)
{
    Image *dst;
    int x, y;

    dst = (Image *)malloc(sizeof(Image));
    if (dst == NULL) return NULL;

    dst->width = new_w;
    dst->height = new_h;
    dst->pixels = (unsigned char *)malloc((size_t)new_w * new_h * 3);
    if (dst->pixels == NULL)
    {
        free(dst);
        return NULL;
    }

    /* 最近邻缩放 */
    for (y = 0; y < new_h; y++)
    {
        for (x = 0; x < new_w; x++)
        {
            int sx = x * src->width / new_w;
            int sy = y * src->height / new_h;
            int si = (sy * src->width + sx) * 3;
            int di = (y * new_w + x) * 3;
            dst->pixels[di + 0] = src->pixels[si + 0];
            dst->pixels[di + 1] = src->pixels[si + 1];
            dst->pixels[di + 2] = src->pixels[si + 2];
        }
    }
    return dst;
}

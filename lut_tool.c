/*
 * lut_tool.c -- 3D LUT (Look-Up Table) 应用工具
 *
 * 功能: 读取 PPM 图像和 .cube 格式 3D LUT，应用 LUT 后输出结果
 *
 * 用法: lut_tool <input.ppm> <lut.cube> <output.ppm>
 *
 * 语言标准: ANSI C (C89)
 * 编码风格: BSD Allman
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <errno.h>

/* ============================================================
 * 常量定义
 * ============================================================ */
#define MAX_LINE_LEN      4096
#define MAX_LUT_SIZE      256
#define MAX_TITLE_LEN     256
#define PPM_MAX_VAL       255
#define PPM_HEADER_LEN    1024

/* 钳位到 [0, 1] */
#define CLAMP01(x)  (((x) < 0.0f) ? 0.0f : (((x) > 1.0f) ? 1.0f : (x)))

/* ============================================================
 * 3D LUT 数据结构
 * ============================================================ */
typedef struct
{
    int   size;                  /* 网格尺寸（如 33 表示 33x33x33）  */
    float *data;                 /* 展平数据: data[b][g][r][3]       */
    char  title[MAX_TITLE_LEN]; /* LUT 标题                          */
    float dom_min[3];            /* 定义域最小值（通常 0 0 0）        */
    float dom_max[3];            /* 定义域最大值（通常 1 1 1）        */
} LUT3D;

/* ---- LUT 函数声明 ---- */
int   lut_load(const char *filepath, LUT3D *lut);
void  lut_free(LUT3D *lut);
void  lut_apply_tetrahedral(const LUT3D *lut,
                            float r, float g, float b,
                            float *or_, float *og_, float *ob_);

/* ============================================================
 * 图像数据结构 (PPM P6)
 * ============================================================ */
typedef struct
{
    int           width;
    int           height;
    unsigned char *pixels;   /* RGB 交错: R G B R G B ... */
} Image;

/* ---- 图像函数声明 ---- */
int   img_load_ppm(const char *filepath, Image *img);
int   img_save_ppm(const char *filepath, const Image *img);
void  img_free(Image *img);
int   img_apply_lut(Image *img, const LUT3D *lut);

/* ============================================================
 * 工具函数
 * ============================================================ */

/*
 * 去除字符串首尾空白（就地修改）
 * 返回: 指向去除空白后的字符串起始位置
 */
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
    while (end > s && (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n'))
    {
        end--;
    }
    *(end + 1) = '\0';
    return s;
}

/* ============================================================
 * LUT 加载 -- .cube 格式
 * ============================================================
 *
 * .cube 文件格式（简化版）:
 *   TITLE "string"
 *   LUT_3D_SIZE int
 *   DOMAIN_MIN float float float
 *   DOMAIN_MAX float float float
 *   # 注释
 *   r g b   (共 size^3 行, 蓝通道最慢, 红最快)
 */
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
        fprintf(stderr, "错误: 无法打开 LUT 文件 '%s': %s\n",
                filepath, strerror(errno));
        return -1;
    }

    /* --- 第一遍: 读取头部信息 --- */
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
                    if (rq != NULL)
                    {
                        *rq = '\0';
                    }
                    strncpy(lut->title, q, MAX_TITLE_LEN - 1);
                    lut->title[MAX_TITLE_LEN - 1] = '\0';
                }
            }
            else if (strncmp(p, "LUT_3D_SIZE", 11) == 0)
            {
                lut->size = atoi(p + 11);
                if (lut->size < 2 || lut->size > MAX_LUT_SIZE)
                {
                    fprintf(stderr,
                        "错误: 不支持的 LUT 尺寸 %d (支持 2-%d)\n",
                        lut->size, MAX_LUT_SIZE);
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

        /* 检查是否为数据行: 以数字/负号/小数点开头 */
        if (*p == '-' || *p == '+' || *p == '.' || (*p >= '0' && *p <= '9'))
        {
            if (lut->size == 0)
            {
                fprintf(stderr,
                    "错误: 第 %d 行: 在 LUT_3D_SIZE 声明前发现数据\n", line_num);
                fclose(fp);
                return -1;
            }
            parsing_data = 1;
            data_count++;
        }
    }

    if (lut->size == 0)
    {
        fprintf(stderr, "错误: 未找到 LUT_3D_SIZE 声明\n");
        fclose(fp);
        return -1;
    }

    expected = lut->size * lut->size * lut->size;
    if (data_count != expected)
    {
        fprintf(stderr, "错误: 数据行数不匹配: 期望 %d, 实际 %d\n",
                expected, data_count);
        fclose(fp);
        return -1;
    }

    /* --- 分配内存并重新读取 --- */
    lut->data = (float *)malloc((size_t)expected * 3 * sizeof(float));
    if (lut->data == NULL)
    {
        fprintf(stderr, "错误: 内存分配失败 (%lu bytes)\n",
                (unsigned long)((size_t)expected * 3 * sizeof(float)));
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
        if (*p == '\0' || *p == '#')
        {
            continue;
        }

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
 * LUT 插值 -- 四面体插值 (Tetrahedral Interpolation)
 *
 * 将立方体剖分为 6 个四面体，根据 fractional 坐标落在
 * 哪个四面体中选择对应的 4 个顶点进行插值。
 * 比三线性插值有更好的平滑度和更少的瑕疵。
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

    /* 映射到网格坐标 */
    rs = r * size_f;
    gs = g * size_f;
    bs = b * size_f;

    ri = (int)rs;
    gi = (int)gs;
    bi = (int)bs;
    rf = rs - ri;
    gf = gs - gi;
    bf = bs - bi;

    /* 处理边界情况 -- 防止越界 */
    if (ri >= lut->size - 1)
    {
        ri = lut->size - 2;
        rf = 1.0f;
    }
    if (gi >= lut->size - 1)
    {
        gi = lut->size - 2;
        gf = 1.0f;
    }
    if (bi >= lut->size - 1)
    {
        bi = lut->size - 2;
        bf = 1.0f;
    }

    /* 获取 8 个顶点的值 */
    /* 索引: idx = ((b * size + g) * size + r) * 3 */
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

    /*
     * 判断 fractional 坐标排序并选择对应的四面体。
     * 每个四面体选择 4 个顶点，权重和为 1。
     */
    if (rf >= gf && gf >= bf)
    {
        /* 四面体: c000-c100-c110-c111  (R >= G >= B) */
        or_val = c000[0] * (1.0f - rf)
               + c100[0] * (rf - gf)
               + c110[0] * (gf - bf)
               + c111[0] * bf;
        og_val = c000[1] * (1.0f - rf)
               + c100[1] * (rf - gf)
               + c110[1] * (gf - bf)
               + c111[1] * bf;
        ob_val = c000[2] * (1.0f - rf)
               + c100[2] * (rf - gf)
               + c110[2] * (gf - bf)
               + c111[2] * bf;
    }
    else if (rf >= bf && bf >= gf)
    {
        /* 四面体: c000-c100-c101-c111  (R >= B >= G) */
        or_val = c000[0] * (1.0f - rf)
               + c100[0] * (rf - bf)
               + c101[0] * (bf - gf)
               + c111[0] * gf;
        og_val = c000[1] * (1.0f - rf)
               + c100[1] * (rf - bf)
               + c101[1] * (bf - gf)
               + c111[1] * gf;
        ob_val = c000[2] * (1.0f - rf)
               + c100[2] * (rf - bf)
               + c101[2] * (bf - gf)
               + c111[2] * gf;
    }
    else if (gf >= rf && rf >= bf)
    {
        /* 四面体: c000-c010-c110-c111  (G >= R >= B) */
        or_val = c000[0] * (1.0f - gf)
               + c010[0] * (gf - rf)
               + c110[0] * (rf - bf)
               + c111[0] * bf;
        og_val = c000[1] * (1.0f - gf)
               + c010[1] * (gf - rf)
               + c110[1] * (rf - bf)
               + c111[1] * bf;
        ob_val = c000[2] * (1.0f - gf)
               + c010[2] * (gf - rf)
               + c110[2] * (rf - bf)
               + c111[2] * bf;
    }
    else if (gf >= bf && bf >= rf)
    {
        /* 四面体: c000-c010-c011-c111  (G >= B >= R) */
        or_val = c000[0] * (1.0f - gf)
               + c010[0] * (gf - bf)
               + c011[0] * (bf - rf)
               + c111[0] * rf;
        og_val = c000[1] * (1.0f - gf)
               + c010[1] * (gf - bf)
               + c011[1] * (bf - rf)
               + c111[1] * rf;
        ob_val = c000[2] * (1.0f - gf)
               + c010[2] * (gf - bf)
               + c011[2] * (bf - rf)
               + c111[2] * rf;
    }
    else if (bf >= rf && rf >= gf)
    {
        /* 四面体: c000-c001-c101-c111  (B >= R >= G) */
        or_val = c000[0] * (1.0f - bf)
               + c001[0] * (bf - rf)
               + c101[0] * (rf - gf)
               + c111[0] * gf;
        og_val = c000[1] * (1.0f - bf)
               + c001[1] * (bf - rf)
               + c101[1] * (rf - gf)
               + c111[1] * gf;
        ob_val = c000[2] * (1.0f - bf)
               + c001[2] * (bf - rf)
               + c101[2] * (rf - gf)
               + c111[2] * gf;
    }
    else
    {
        /* 四面体: c000-c001-c011-c111  (B >= G >= R) -- 默认 */
        or_val = c000[0] * (1.0f - bf)
               + c001[0] * (bf - gf)
               + c011[0] * (gf - rf)
               + c111[0] * rf;
        og_val = c000[1] * (1.0f - bf)
               + c001[1] * (bf - gf)
               + c011[1] * (gf - rf)
               + c111[1] * rf;
        ob_val = c000[2] * (1.0f - bf)
               + c001[2] * (bf - gf)
               + c011[2] * (gf - rf)
               + c111[2] * rf;
    }

    /* 钳位到 [0, 1] */
    *or_ = CLAMP01(or_val);
    *og_ = CLAMP01(og_val);
    *ob_ = CLAMP01(ob_val);
}

/* ============================================================
 * 图像加载 -- PPM P6 格式
 * ============================================================ */
int
img_load_ppm(const char *filepath, Image *img)
{
    FILE *fp;
    char  header[PPM_HEADER_LEN];
    int   w, h, max_val;
    size_t n_pixels;
    int   hi, lo;
    size_t i;

    memset(img, 0, sizeof(Image));

    fp = fopen(filepath, "rb");
    if (!fp)
    {
        fprintf(stderr, "错误: 无法打开图像 '%s': %s\n",
                filepath, strerror(errno));
        return -1;
    }

    /* 读取 magic number */
    if (fgets(header, sizeof(header), fp) == NULL)
    {
        fprintf(stderr, "错误: 无法读取 PPM 头\n");
        fclose(fp);
        return -1;
    }
    if (header[0] != 'P' || header[1] != '6')
    {
        fprintf(stderr, "错误: 仅支持 PPM P6 格式 (得到 '%c%c')\n",
                header[0], header[1]);
        fclose(fp);
        return -1;
    }

    /* 跳过注释行 */
    while (1)
    {
        if (fgets(header, sizeof(header), fp) == NULL)
        {
            fprintf(stderr, "错误: PPM 头不完整\n");
            fclose(fp);
            return -1;
        }
        if (header[0] != '#')
        {
            break;
        }
    }

    /* 解析宽高 */
    if (sscanf(header, "%d %d", &w, &h) != 2)
    {
        fprintf(stderr, "错误: 无法解析 PPM 宽高\n");
        fclose(fp);
        return -1;
    }

    /* 读取最大颜色值 */
    if (fgets(header, sizeof(header), fp) == NULL)
    {
        fprintf(stderr, "错误: PPM 最大颜色值缺失\n");
        fclose(fp);
        return -1;
    }
    max_val = atoi(header);
    if (max_val <= 0 || max_val > 65535)
    {
        fprintf(stderr, "错误: 不支持的最大颜色值 %d\n", max_val);
        fclose(fp);
        return -1;
    }

    img->width  = w;
    img->height = h;
    n_pixels = (size_t)w * h;
    img->pixels = (unsigned char *)malloc(n_pixels * 3);
    if (img->pixels == NULL)
    {
        fprintf(stderr, "错误: 内存分配失败 (%lu bytes)\n",
                (unsigned long)(n_pixels * 3));
        fclose(fp);
        return -1;
    }

    if (max_val <= 255)
    {
        /* 每个通道 1 字节 */
        if (fread(img->pixels, 3, n_pixels, fp) != n_pixels)
        {
            fprintf(stderr, "错误: 像素数据读取不完整\n");
            img_free(img);
            fclose(fp);
            return -1;
        }
    }
    else
    {
        /* 每个通道 2 字节 -- 缩放到 8-bit */
        for (i = 0; i < n_pixels; i++)
        {
            hi = fgetc(fp);
            lo = fgetc(fp);
            if (hi == EOF || lo == EOF)
            {
                fprintf(stderr, "错误: 像素数据读取不完整 (16-bit)\n");
                img_free(img);
                fclose(fp);
                return -1;
            }
            img->pixels[i * 3 + 0] =
                (unsigned char)(((hi << 8) | lo) * 255 / max_val);

            hi = fgetc(fp);
            lo = fgetc(fp);
            if (hi == EOF || lo == EOF)
            {
                break;
            }
            img->pixels[i * 3 + 1] =
                (unsigned char)(((hi << 8) | lo) * 255 / max_val);

            hi = fgetc(fp);
            lo = fgetc(fp);
            if (hi == EOF || lo == EOF)
            {
                break;
            }
            img->pixels[i * 3 + 2] =
                (unsigned char)(((hi << 8) | lo) * 255 / max_val);
        }
    }

    fclose(fp);
    return 0;
}

/* ============================================================
 * 图像保存 -- PPM P6 格式
 * ============================================================ */
int
img_save_ppm(const char *filepath, const Image *img)
{
    FILE *fp;
    size_t n_pixels;

    fp = fopen(filepath, "wb");
    if (!fp)
    {
        fprintf(stderr, "错误: 无法写入 '%s': %s\n",
                filepath, strerror(errno));
        return -1;
    }

    fprintf(fp, "P6\n%d %d\n%d\n", img->width, img->height, PPM_MAX_VAL);
    n_pixels = (size_t)img->width * img->height;
    if (fwrite(img->pixels, 3, n_pixels, fp) != n_pixels)
    {
        fprintf(stderr, "错误: 写入像素数据失败\n");
        fclose(fp);
        return -1;
    }

    fclose(fp);
    return 0;
}

void
img_free(Image *img)
{
    if (img->pixels != NULL)
    {
        free(img->pixels);
        img->pixels = NULL;
    }
    img->width = 0;
    img->height = 0;
}

/* ============================================================
 * 将 LUT 应用于整张图像 (四面体插值)
 * ============================================================ */
int
img_apply_lut(Image *img, const LUT3D *lut)
{
    size_t n;
    unsigned char *p;
    size_t i;
    float r, g, b;
    float or_, og_, ob_;

    n = (size_t)img->width * img->height;
    p = img->pixels;

    for (i = 0; i < n; i++)
    {
        r = p[0] / 255.0f;
        g = p[1] / 255.0f;
        b = p[2] / 255.0f;

        lut_apply_tetrahedral(lut, r, g, b, &or_, &og_, &ob_);

        p[0] = (unsigned char)(or_ * 255.0f + 0.5f);
        p[1] = (unsigned char)(og_ * 255.0f + 0.5f);
        p[2] = (unsigned char)(ob_ * 255.0f + 0.5f);
        p += 3;
    }

    return 0;
}

/* ============================================================
 * 主函数
 * ============================================================ */
int
main(int argc, char *argv[])
{
    const char *input_path;
    const char *lut_path;
    const char *output_path;
    Image img;
    LUT3D lut;

    if (argc < 4)
    {
        fprintf(stderr, "用法: %s <input.ppm> <lut.cube> <output.ppm>\n",
                argv[0]);
        fprintf(stderr, "示例: %s test.ppm vintage.cube result.ppm\n",
                argv[0]);
        return 1;
    }

    input_path  = argv[1];
    lut_path    = argv[2];
    output_path = argv[3];

    /* 加载图像 */
    if (img_load_ppm(input_path, &img) != 0)
    {
        return 1;
    }
    printf("图像: %dx%d, %lu 像素\n",
           img.width, img.height,
           (unsigned long)((size_t)img.width * img.height));

    /* 加载 LUT */
    if (lut_load(lut_path, &lut) != 0)
    {
        img_free(&img);
        return 1;
    }
    printf("LUT:  %s (尺寸 %dx%dx%d)\n",
           lut.title[0] ? lut.title : lut_path,
           lut.size, lut.size, lut.size);

    /* 应用 LUT */
    printf("处理中...\n");
    if (img_apply_lut(&img, &lut) != 0)
    {
        lut_free(&lut);
        img_free(&img);
        return 1;
    }

    /* 保存结果 */
    if (img_save_ppm(output_path, &img) != 0)
    {
        lut_free(&lut);
        img_free(&img);
        return 1;
    }
    printf("输出: %s\n", output_path);

    lut_free(&lut);
    img_free(&img);
    return 0;
}

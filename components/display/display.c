// ============================================================
//  GC9A01A 240x240 redonda, por SPI.
//  Framebuffer propio en RAM interna (115 KB) y volcado por
//  franjas a traves de un buffer DMA pequeno.
// ============================================================
#include "display.h"
#include "font5x7.h"
#include "board_pins.h"
#include <string.h>
#include <math.h>
#include "esp_log.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_gc9a01.h"

#define W  BOARD_LCD_H_RES
#define H  BOARD_LCD_V_RES
#define STRIP 40

static const char *TAG = "display";
static esp_lcd_panel_handle_t s_panel = NULL;
static uint16_t *s_fb = NULL;
static uint16_t *s_strip = NULL;

esp_err_t display_init(void)
{
    gpio_config_t bl = { .pin_bit_mask = 1ULL << BOARD_LCD_BL, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&bl);
    gpio_set_level(BOARD_LCD_BL, 0);      // logica invertida: 0 enciende

    s_fb    = heap_caps_malloc(W * H * sizeof(uint16_t), MALLOC_CAP_DEFAULT);
    s_strip = heap_caps_malloc(W * STRIP * sizeof(uint16_t), MALLOC_CAP_DMA);
    ESP_RETURN_ON_FALSE(s_fb && s_strip, ESP_ERR_NO_MEM, TAG, "sin memoria");

    spi_bus_config_t bus = {
        .sclk_io_num = BOARD_LCD_SCLK,
        .mosi_io_num = BOARD_LCD_MOSI,
        .miso_io_num = -1,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = W * STRIP * sizeof(uint16_t) + 8,
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO), TAG, "spi");

    esp_lcd_panel_io_handle_t io = NULL;
    esp_lcd_panel_io_spi_config_t io_cfg = {
        .dc_gpio_num = BOARD_LCD_DC,
        .cs_gpio_num = BOARD_LCD_CS,
        .pclk_hz = BOARD_LCD_SPI_HZ,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .spi_mode = 0,
        .trans_queue_depth = 10,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi(SPI2_HOST, &io_cfg, &io), TAG, "panel io");

    esp_lcd_panel_dev_config_t pcfg = {
        .reset_gpio_num = BOARD_LCD_RST,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_BGR,
        .bits_per_pixel = 16,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_gc9a01(io, &pcfg, &s_panel), TAG, "gc9a01");

    esp_lcd_panel_reset(s_panel);
    esp_lcd_panel_init(s_panel);
    esp_lcd_panel_invert_color(s_panel, true);
    esp_lcd_panel_mirror(s_panel, true, false);
    esp_lcd_panel_disp_on_off(s_panel, true);
    ESP_LOGI(TAG, "GC9A01A lista");
    return ESP_OK;
}

void display_px(int x, int y, uint16_t c)
{
    if (x < 0 || y < 0 || x >= W || y >= H) return;
    s_fb[y * W + x] = c;
}

void display_clear(uint16_t c)
{
    for (int i = 0; i < W * H; i++) s_fb[i] = c;
}

void display_circle(int cx, int cy, int r, uint16_t c)
{
    int x = r, y = 0, err = 1 - x;
    while (x >= y) {
        display_px(cx+x, cy+y, c); display_px(cx+y, cy+x, c);
        display_px(cx-y, cy+x, c); display_px(cx-x, cy+y, c);
        display_px(cx-x, cy-y, c); display_px(cx-y, cy-x, c);
        display_px(cx+y, cy-x, c); display_px(cx+x, cy-y, c);
        y++;
        if (err < 0) err += 2*y + 1; else { x--; err += 2*(y-x) + 1; }
    }
}

void display_fill_circle(int cx, int cy, int r, uint16_t c)
{
    for (int dy = -r; dy <= r; dy++)
        for (int dx = -r; dx <= r; dx++)
            if (dx*dx + dy*dy <= r*r) display_px(cx+dx, cy+dy, c);
}

void display_arc(int cx, int cy, int r, int grosor, int a0, int a1, uint16_t c)
{
    for (int a = a0; a <= a1; a++) {
        float rad = a * (float)M_PI / 180.0f;
        float cs = cosf(rad), sn = sinf(rad);
        for (int t = 0; t < grosor; t++)
            display_px(cx + (int)((r - t) * cs), cy + (int)((r - t) * sn), c);
    }
}

void display_rect(int x, int y, int w, int h, uint16_t c)
{
    for (int j = 0; j < h; j++)
        for (int i = 0; i < w; i++) display_px(x + i, y + j, c);
}

static void draw_char(int x, int y, char ch, uint16_t c, int e)
{
    if (ch >= 'a' && ch <= 'z') ch -= 32;
    if (ch < 32 || ch > 'Z') return;
    const uint8_t *g = font5x7[ch - 32];
    for (int col = 0; col < 5; col++)
        for (int row = 0; row < 7; row++)
            if (g[col] & (1 << row))
                for (int sy = 0; sy < e; sy++)
                    for (int sx = 0; sx < e; sx++)
                        display_px(x + col*e + sx, y + row*e + sy, c);
}

void display_text(int x, int y, const char *s, uint16_t c, int e)
{
    while (*s) { draw_char(x, y, *s++, c, e); x += 6 * e; }
}

void display_text_center(int cx, int y, const char *s, uint16_t c, int e)
{
    display_text(cx - (int)strlen(s) * 3 * e, y, s, c, e);
}

void display_flush(void)
{
    for (int y = 0; y < H; y += STRIP) {
        int lines = (y + STRIP > H) ? (H - y) : STRIP;
        memcpy(s_strip, &s_fb[y * W], lines * W * sizeof(uint16_t));
        esp_lcd_panel_draw_bitmap(s_panel, 0, y, W, y + lines, s_strip);
    }
}

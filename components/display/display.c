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
#include <stdlib.h>
#include "esp_log.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_gc9a01.h"

#define W  BOARD_LCD_H_RES
#define H  BOARD_LCD_V_RES
#define STRIP 40
// Dos buffers de franja: mientras uno viaja por DMA se prepara el otro.
// Con uno solo hay carrera, ver display_flush.
#define N_STRIPS 2

static const char *TAG = "display";
static esp_lcd_panel_handle_t s_panel = NULL;
static uint16_t *s_fb = NULL;
static uint16_t *s_strip[N_STRIPS] = {0};
static SemaphoreHandle_t s_libre = NULL;   // franjas DMA disponibles

// El driver avisa cuando termina de enviar una franja. Sin esto no hay forma
// de saber cuando es seguro reutilizar su buffer.
static bool IRAM_ATTR on_trans_done(esp_lcd_panel_io_handle_t io,
                                    esp_lcd_panel_io_event_data_t *e, void *ctx)
{
    BaseType_t hp = pdFALSE;
    xSemaphoreGiveFromISR(s_libre, &hp);
    return hp == pdTRUE;
}

esp_err_t display_init(void)
{
    // Backlight por PWM para poder regular el brillo.
    // Ojo: la logica esta invertida, asi que el duty se invierte al aplicarlo.
    ledc_timer_config_t t = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&t);
    ledc_channel_config_t ch = {
        .gpio_num = BOARD_LCD_BL,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
        .hpoint = 0,
    };
    ledc_channel_config(&ch);
    display_set_brightness(85);

    s_fb    = heap_caps_malloc(W * H * sizeof(uint16_t), MALLOC_CAP_DEFAULT);
    for (int i = 0; i < N_STRIPS; i++) {
        s_strip[i] = heap_caps_malloc(W * STRIP * sizeof(uint16_t), MALLOC_CAP_DMA);
        ESP_RETURN_ON_FALSE(s_strip[i], ESP_ERR_NO_MEM, TAG, "sin memoria DMA");
    }
    s_libre = xSemaphoreCreateCounting(N_STRIPS, N_STRIPS);
    ESP_RETURN_ON_FALSE(s_fb && s_libre, ESP_ERR_NO_MEM, TAG, "sin memoria");

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
        .on_color_trans_done = on_trans_done,
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
        int32_t cs = display_cos_q(a), sn = display_sin_q(a);
        for (int t = 0; t < grosor; t++)
            display_px(cx + (r - t) * cs / 4096, cy + (r - t) * sn / 4096, c);
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

// esp_lcd_panel_draw_bitmap es ASINCRONA: encola la transferencia y vuelve.
// Con un solo buffer, la franja siguiente lo sobrescribia mientras la anterior
// todavia se estaba enviando, y el panel recibia dos veces el mismo contenido
// con 40 px de desfase, que es exactamente el tamano de una franja. En pantalla
// eso se veia como el boton VOZ duplicado mas abajo.
//
// La solucion es la que indica la propia documentacion de ESP-IDF: registrar
// on_color_trans_done y no reutilizar un buffer hasta que su envio termine.
void display_flush(void)
{
    int b = 0;
    for (int y = 0; y < H; y += STRIP) {
        int lines = (y + STRIP > H) ? (H - y) : STRIP;

        // Espera a que este buffer quede libre. Con plazo, no indefinida: si el
        // callback no llegara, es preferible una imagen imperfecta a una
        // pantalla congelada para siempre.
        if (xSemaphoreTake(s_libre, pdMS_TO_TICKS(100)) != pdTRUE)
            ESP_LOGW(TAG, "franja sin confirmar, se sigue igualmente");

        // RGB565 nativo del ESP32 (little endian) al orden que espera el
        // GC9A01, byte alto primero.
        const uint16_t *src = &s_fb[y * W];
        uint16_t *dst = s_strip[b];
        int n = lines * W;
        for (int i = 0; i < n; i++) dst[i] = __builtin_bswap16(src[i]);

        esp_lcd_panel_draw_bitmap(s_panel, 0, y, W, y + lines, dst);
        b = (b + 1) % N_STRIPS;
    }
}


// ============================================================
//  Brillo
// ============================================================
static int s_brillo = 85;

void display_set_brightness(int pct)
{
    if (pct < 5) pct = 5;                 // nunca del todo apagada
    if (pct > 100) pct = 100;
    s_brillo = pct;
    // Logica invertida: 0 enciende, por eso se invierte el duty
    uint32_t duty = 1023 - (uint32_t)(pct * 1023 / 100);
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
}

int display_brightness(void) { return s_brillo; }

// ============================================================
//  Trigonometria por tabla (Grupo 2)
// ============================================================
// sin(grado) * 4096 para grado en [0, 359]. Generada una vez con Python
// (math.sin), no en el ESP32: es una tabla, no un calculo de arranque.
// El coseno se lee de la misma tabla con un desfase de 90 grados: cos(x) =
// sin(x + 90), asi que no hace falta una segunda tabla.
static const int16_t TABLA_SIN[360] = {
    0, 71, 143, 214, 286, 357, 428, 499, 570, 641, 711, 782,
    852, 921, 991, 1060, 1129, 1198, 1266, 1334, 1401, 1468, 1534, 1600,
    1666, 1731, 1796, 1860, 1923, 1986, 2048, 2110, 2171, 2231, 2290, 2349,
    2408, 2465, 2522, 2578, 2633, 2687, 2741, 2793, 2845, 2896, 2946, 2996,
    3044, 3091, 3138, 3183, 3228, 3271, 3314, 3355, 3396, 3435, 3474, 3511,
    3547, 3582, 3617, 3650, 3681, 3712, 3742, 3770, 3798, 3824, 3849, 3873,
    3896, 3917, 3937, 3956, 3974, 3991, 4006, 4021, 4034, 4046, 4056, 4065,
    4074, 4080, 4086, 4090, 4094, 4095, 4096, 4095, 4094, 4090, 4086, 4080,
    4074, 4065, 4056, 4046, 4034, 4021, 4006, 3991, 3974, 3956, 3937, 3917,
    3896, 3873, 3849, 3824, 3798, 3770, 3742, 3712, 3681, 3650, 3617, 3582,
    3547, 3511, 3474, 3435, 3396, 3355, 3314, 3271, 3228, 3183, 3138, 3091,
    3044, 2996, 2946, 2896, 2845, 2793, 2741, 2687, 2633, 2578, 2522, 2465,
    2408, 2349, 2290, 2231, 2171, 2110, 2048, 1986, 1923, 1860, 1796, 1731,
    1666, 1600, 1534, 1468, 1401, 1334, 1266, 1198, 1129, 1060, 991, 921,
    852, 782, 711, 641, 570, 499, 428, 357, 286, 214, 143, 71,
    0, -71, -143, -214, -286, -357, -428, -499, -570, -641, -711, -782,
    -852, -921, -991, -1060, -1129, -1198, -1266, -1334, -1401, -1468, -1534, -1600,
    -1666, -1731, -1796, -1860, -1923, -1986, -2048, -2110, -2171, -2231, -2290, -2349,
    -2408, -2465, -2522, -2578, -2633, -2687, -2741, -2793, -2845, -2896, -2946, -2996,
    -3044, -3091, -3138, -3183, -3228, -3271, -3314, -3355, -3396, -3435, -3474, -3511,
    -3547, -3582, -3617, -3650, -3681, -3712, -3742, -3770, -3798, -3824, -3849, -3873,
    -3896, -3917, -3937, -3956, -3974, -3991, -4006, -4021, -4034, -4046, -4056, -4065,
    -4074, -4080, -4086, -4090, -4094, -4095, -4096, -4095, -4094, -4090, -4086, -4080,
    -4074, -4065, -4056, -4046, -4034, -4021, -4006, -3991, -3974, -3956, -3937, -3917,
    -3896, -3873, -3849, -3824, -3798, -3770, -3742, -3712, -3681, -3650, -3617, -3582,
    -3547, -3511, -3474, -3435, -3396, -3355, -3314, -3271, -3228, -3183, -3138, -3091,
    -3044, -2996, -2946, -2896, -2845, -2793, -2741, -2687, -2633, -2578, -2522, -2465,
    -2408, -2349, -2290, -2231, -2171, -2110, -2048, -1986, -1923, -1860, -1796, -1731,
    -1666, -1600, -1534, -1468, -1401, -1334, -1266, -1198, -1129, -1060, -991, -921,
    -852, -782, -711, -641, -570, -499, -428, -357, -286, -214, -143, -71,
};

static inline int grados_norm(int g)
{
    g %= 360;
    if (g < 0) g += 360;
    return g;
}

int32_t display_sin_q(int grados) { return TABLA_SIN[grados_norm(grados)]; }
int32_t display_cos_q(int grados) { return TABLA_SIN[grados_norm(grados + 90)]; }

// ============================================================
//  Color
// ============================================================
uint16_t display_escala(uint16_t c, uint8_t f)
{
    uint16_t r = ((c >> 11) & 0x1F) * f / 255;
    uint16_t g = ((c >> 5)  & 0x3F) * f / 255;
    uint16_t b = ( c        & 0x1F) * f / 255;
    return (r << 11) | (g << 5) | b;
}

uint16_t display_mezcla(uint16_t a, uint16_t b, uint8_t t)
{
    uint16_t ra = (a >> 11) & 0x1F, ga = (a >> 5) & 0x3F, ba = a & 0x1F;
    uint16_t rb = (b >> 11) & 0x1F, gb = (b >> 5) & 0x3F, bb = b & 0x1F;
    uint16_t r = (ra * (255 - t) + rb * t) / 255;
    uint16_t g = (ga * (255 - t) + gb * t) / 255;
    uint16_t bl = (ba * (255 - t) + bb * t) / 255;
    return (r << 11) | (g << 5) | bl;
}

// Mezcla contra lo que ya hay: asi el glow no tapa, ilumina
void display_px_glow(int x, int y, uint16_t c, uint8_t alpha)
{
    if (x < 0 || y < 0 || x >= W || y >= H) return;
    uint16_t fondo = s_fb[y * W + x];
    s_fb[y * W + x] = display_mezcla(fondo, c, alpha);
}

// ============================================================
//  Formas
// ============================================================
void display_line(int x0, int y0, int x1, int y1, uint16_t c)
{
    int dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    int dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    while (1) {
        display_px(x0, y0, c);
        if (x0 == x1 && y0 == y1) break;
        int e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
}

// Arco con halo: tres pasadas, la exterior mas tenue
void display_arc_glow(int cx, int cy, int r, int grosor, int a0, int a1, uint16_t c)
{
    for (int a = a0; a <= a1; a++) {
        int32_t cs = display_cos_q(a), sn = display_sin_q(a);
        for (int t = -2; t < grosor + 2; t++) {
            int borde = (t < 0 || t >= grosor);
            int px = cx + (r - t) * cs / 4096;
            int py = cy + (r - t) * sn / 4096;
            if (borde) display_px_glow(px, py, c, 70);
            else       display_px(px, py, c);
        }
    }
}

// Arco que degrada de un color a otro a lo largo del recorrido
void display_arc_grad(int cx, int cy, int r, int grosor, int a0, int a1,
                      uint16_t c0, uint16_t c1)
{
    int span = (a1 - a0) ? (a1 - a0) : 1;
    for (int a = a0; a <= a1; a++) {
        uint8_t t = (uint8_t)((a - a0) * 255 / span);
        uint16_t c = display_mezcla(c0, c1, t);
        int32_t cs = display_cos_q(a), sn = display_sin_q(a);
        for (int k = 0; k < grosor; k++)
            display_px(cx + (r - k) * cs / 4096, cy + (r - k) * sn / 4096, c);
    }
}

// Puntos orbitando: el clasico anillo de satelites
void display_ring_dots(int cx, int cy, int r, int n, int fase, uint16_t c)
{
    for (int i = 0; i < n; i++) {
        int a = fase + i * 360 / n;
        int x = cx + r * display_cos_q(a) / 4096;
        int y = cy + r * display_sin_q(a) / 4096;
        // Modulacion de brillo: angulo arbitrario (no cae en grados enteros
        // de la posicion), pero sigue sin necesitar sinf: se redondea al
        // grado mas cercano y se lee de la misma tabla.
        int a_brillo = (int)((fase + i * 40) * 0.05f * (180.0f / (float)M_PI) + 0.5f);
        int32_t sn = display_sin_q(a_brillo);           // -4096..4096
        uint8_t brillo = 90 + (uint8_t)(60 + 60 * sn / 4096);
        display_fill_circle(x, y, 2, display_escala(c, brillo));
        display_px_glow(x + 1, y, c, 60);
        display_px_glow(x - 1, y, c, 60);
    }
}

// Lineas horizontales tenues: le da textura de pantalla CRT
void display_scanlines(uint8_t intensidad)
{
    for (int y = 0; y < H; y += 3)
        for (int x = 0; x < W; x++)
            s_fb[y * W + x] = display_escala(s_fb[y * W + x], 255 - intensidad);
}

// Oscurece los bordes para centrar la mirada
void display_vineta(void)
{
    for (int y = 0; y < H; y++) {
        for (int x = 0; x < W; x++) {
            int dx = x - 120, dy = y - 120;
            int d2 = dx * dx + dy * dy;
            if (d2 > 90 * 90) {
                int d = (int)sqrtf((float)d2);
                uint8_t f = (uint8_t)(255 - ((d - 90) * 255 / 40));
                s_fb[y * W + x] = display_escala(s_fb[y * W + x], f);
            }
        }
    }
}

// Rejilla hexagonal de fondo, desplazandose despacio
void display_hex_grid(int fase, uint16_t c)
{
    uint16_t tenue = display_escala(c, 38);
    for (int y = -20; y < H + 20; y += 18) {
        int off = ((y / 18) % 2) ? 12 : 0;
        for (int x = -20; x < W + 20; x += 24) {
            int px = x + off + (fase % 24);
            int py = y + ((fase / 3) % 18);
            int dx = px - 120, dy = py - 120;
            if (dx * dx + dy * dy > 112 * 112) continue;
            display_px(px, py, tenue);
            display_px(px + 1, py, tenue);
            display_px(px, py + 1, tenue);
        }
    }
}

// Corchetes de encuadre en las cuatro esquinas del circulo
void display_corchetes(int r, uint16_t c)
{
    const int angs[4] = {45, 135, 225, 315};
    for (int i = 0; i < 4; i++) {
        for (int d = -14; d <= 14; d++) {
            int a = angs[i] + d;
            int32_t cs = display_cos_q(a), sn = display_sin_q(a);
            for (int k = 0; k < 2; k++)
                display_px(120 + (r - k) * cs / 4096,
                           120 + (r - k) * sn / 4096, c);
        }
    }
}

void display_fade(uint8_t f)
{
    for (int i = 0; i < W * H; i++) s_fb[i] = display_escala(s_fb[i], f);
}

// Texto con halo: se dibuja desplazado y tenue por debajo
void display_text_glow(int cx, int y, const char *s, uint16_t c, int e)
{
    uint16_t halo = display_escala(c, 90);
    display_text_center(cx + 1, y,     s, halo, e);
    display_text_center(cx - 1, y,     s, halo, e);
    display_text_center(cx,     y + 1, s, halo, e);
    display_text_center(cx,     y,     s, c,    e);
}

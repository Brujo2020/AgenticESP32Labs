#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

// ---------- Paleta neon ----------
#define C_VOID     0x0000
#define C_DEEP     0x0861
#define C_CYAN     0x07FF
#define C_MAGENTA  0xF81F
#define C_VIOLET   0x881F
#define C_LIME     0x87E0
#define C_AMBER    0xFD20
#define C_PINK     0xFC1F
#define C_ICE      0xAEDF
#define C_BLOOD    0xF8A0
#define C_WHITE    0xFFFF
#define C_GREY     0x52AA
#define C_GREEN    0x07E0
#define C_RED      0xF800
#define C_YELLOW   0xFFE0
#define C_ORANGE   0xFC00
#define C_DARK     0x0861

esp_err_t display_init(void);

// ---------- Brillo por PWM (el backlight es de logica invertida) ----------
void display_set_brightness(int pct);      // 0..100
int  display_brightness(void);

// ---------- Dibujo basico ----------
void display_clear(uint16_t color);
void display_px(int x, int y, uint16_t color);
void display_line(int x0, int y0, int x1, int y1, uint16_t color);
void display_circle(int cx, int cy, int r, uint16_t color);
void display_fill_circle(int cx, int cy, int r, uint16_t color);
void display_rect(int x, int y, int w, int h, uint16_t color);
void display_arc(int cx, int cy, int r, int grosor, int a0, int a1, uint16_t color);

// ---------- Efectos ----------
uint16_t display_escala(uint16_t color, uint8_t factor);          // 0..255
uint16_t display_mezcla(uint16_t a, uint16_t b, uint8_t t);       // interpola
void display_px_glow(int x, int y, uint16_t color, uint8_t alpha);
void display_arc_glow(int cx, int cy, int r, int grosor, int a0, int a1, uint16_t color);
void display_arc_grad(int cx, int cy, int r, int grosor, int a0, int a1,
                      uint16_t c0, uint16_t c1);
void display_ring_dots(int cx, int cy, int r, int n, int fase, uint16_t color);
void display_scanlines(uint8_t intensidad);
void display_vineta(void);
void display_hex_grid(int fase, uint16_t color);
void display_corchetes(int r, uint16_t color);
void display_fade(uint8_t factor);          // oscurece todo el framebuffer

// ---------- Texto ----------
void display_text(int x, int y, const char *s, uint16_t color, int escala);
void display_text_center(int cx, int y, const char *s, uint16_t color, int escala);
void display_text_glow(int cx, int y, const char *s, uint16_t color, int escala);

void display_flush(void);

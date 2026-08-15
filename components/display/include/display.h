#pragma once
#include <stdint.h>
#include "esp_err.h"

// Colores RGB565
#define C_DARK    0x0841
#define C_CYAN    0x07FF
#define C_ORANGE  0xFC00
#define C_GREEN   0x07E0
#define C_RED     0xF800
#define C_YELLOW  0xFFE0
#define C_PINK    0xF81F
#define C_WHITE   0xFFFF
#define C_GREY    0x8410
#define C_BLUE    0x041F

esp_err_t display_init(void);

// Dibujo sobre el framebuffer (nada llega a la pantalla hasta display_flush)
void display_clear(uint16_t color);
void display_px(int x, int y, uint16_t color);
void display_circle(int cx, int cy, int r, uint16_t color);
void display_fill_circle(int cx, int cy, int r, uint16_t color);
void display_arc(int cx, int cy, int r, int grosor, int a0, int a1, uint16_t color);
void display_rect(int x, int y, int w, int h, uint16_t color);
void display_text(int x, int y, const char *s, uint16_t color, int escala);
void display_text_center(int cx, int y, const char *s, uint16_t color, int escala);

// Vuelca el framebuffer a la pantalla
void display_flush(void);

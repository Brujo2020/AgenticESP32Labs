#include "ui.h"
#include "display.h"
#include <string.h>
#include <math.h>

// ---------- Onda de pulsacion ----------
static int s_rx, s_ry, s_rt = -1;
static uint16_t s_rc;

void ui_ripple_lanza(int x, int y, uint16_t c)
{
    s_rx = x; s_ry = y; s_rc = c; s_rt = 0;
}

void ui_ripple_dibuja(void)
{
    if (s_rt < 0) return;
    int r = 4 + s_rt * 5;
    uint8_t alpha = (uint8_t)(140 - s_rt * 20);
    for (int a = 0; a < 360; a += 4) {
        float rad = a * (float)M_PI / 180.0f;
        display_px_glow(s_rx + (int)(r * cosf(rad)),
                        s_ry + (int)(r * sinf(rad)), s_rc, alpha);
    }
    if (++s_rt > 6) s_rt = -1;
}

// ---------- Botones ----------
bool ui_dentro(const boton_t *b, int x, int y)
{
    // Margen extra: el dedo tapa el boton y se suele tocar algo por debajo
    const int m = 14;   // el dibujo es pequeno; el area de toque, generosa
    if (b->redondo) {
        int cx = b->x + b->w / 2, cy = b->y + b->h / 2;
        int dx = x - cx, dy = y - cy, r = b->w / 2 + m;
        return dx * dx + dy * dy <= r * r;
    }
    return x >= b->x - m && x <= b->x + b->w + m &&
           y >= b->y - m && y <= b->y + b->h + m;
}

void ui_boton(const boton_t *b, bool activo)
{
    uint16_t relleno = display_escala(b->color, activo ? 150 : 45);
    uint16_t borde   = activo ? C_WHITE : b->color;
    int e = b->escala_txt ? b->escala_txt : 2;

    if (b->redondo) {
        int cx = b->x + b->w / 2, cy = b->y + b->h / 2, r = b->w / 2;
        display_fill_circle(cx, cy, r, relleno);
        display_arc(cx, cy, r, 2, 0, 359, borde);

        if (b->txt) display_text_center(cx, cy - 3 * e, b->txt,
                                        activo ? C_WHITE : C_WHITE, e);
    } else {
        display_rect(b->x, b->y, b->w, b->h, relleno);
        // Marco
        display_rect(b->x, b->y, b->w, 2, borde);
        display_rect(b->x, b->y + b->h - 2, b->w, 2, borde);
        display_rect(b->x, b->y, 2, b->h, borde);
        display_rect(b->x + b->w - 2, b->y, 2, b->h, borde);
        if (b->txt)
            display_text_center(b->x + b->w / 2, b->y + b->h / 2 - 3 * e, b->txt, C_WHITE, e);
    }
}

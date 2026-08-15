#pragma once
#include <stdint.h>
#include <stdbool.h>

// Botones tactiles. El objetivo minimo es 48 px: por debajo de eso
// un pulgar falla mas de lo que acierta en una pantalla de 240 px.
#define UI_MIN_TOQUE 48

typedef struct {
    int  x, y, w, h;         // rectangulo (para redondos, w == h == diametro)
    const char *txt;
    uint16_t color;
    bool redondo;
    int  escala_txt;
} boton_t;

// ¿El punto cae dentro? Amplia el area un poco mas alla del dibujo,
// porque el dedo tapa el boton y se tiende a tocar por debajo.
bool ui_dentro(const boton_t *b, int x, int y);

void ui_boton(const boton_t *b, bool activo);

// Efecto de pulsacion: onda que se expande desde el punto tocado.
void ui_ripple_lanza(int x, int y, uint16_t color);
void ui_ripple_dibuja(void);      // llamar una vez por fotograma

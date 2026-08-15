#pragma once
#include <stdbool.h>

// Preferencias que sobreviven al reinicio (se guardan en NVS).
typedef struct {
    int  brillo;        // 0..100
    int  volumen;       // 0..100
    int  tema;          // 0=cyan 1=magenta 2=lima 3=ambar
    bool scanlines;
    bool rejilla;
} ajustes_t;

void ajustes_cargar(void);
void ajustes_guardar(void);
ajustes_t *ajustes(void);

// Aplica brillo y volumen al hardware
void ajustes_aplicar(void);

// Color de acento del tema activo
unsigned short ajustes_acento(void);

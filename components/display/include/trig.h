#pragma once
#include <stdint.h>

// sin(grados) en Q15: valor real = SIN_Q15[g] / 32768.0
extern const int16_t SIN_Q15[360];

// Normaliza a [0,360) y consulta. Enteros en todo el camino.
static inline int trig_sin(int g) { return SIN_Q15[((g % 360) + 360) % 360]; }
static inline int trig_cos(int g) { return SIN_Q15[((g + 90) % 360 + 360) % 360]; }

// x * sin(g) redondeando, sin coma flotante
static inline int trig_mul_sin(int x, int g) { return (x * trig_sin(g)) >> 15; }
static inline int trig_mul_cos(int x, int g) { return (x * trig_cos(g)) >> 15; }

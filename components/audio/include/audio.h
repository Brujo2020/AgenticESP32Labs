#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

// Inicializa el codec ES8311 (mic + parlante) y el bus I2S.
esp_err_t audio_init(void);

// true si el codec respondio y quedo configurado.
bool audio_ready(void);

// Tono con envolvente suave. Sin envolvente suena a chasquido.
void audio_beep(int freq_hz, int ms);

// Nivel de pico del microfono, 0..100, suavizado. Para la barra VU.
int audio_mic_level(void);

// Lee muestras crudas del microfono. Devuelve bytes leidos (0 si no hay).
size_t audio_mic_read(int16_t *dst, size_t bytes, int timeout_ms);

// Reproduce PCM 16-bit mono a la frecuencia de la placa.
void audio_play_pcm(const void *pcm, size_t bytes);

// Volumen del parlante 0..100 (registro 0x32 del ES8311).
void audio_set_volume(int pct);
int  audio_volume(void);

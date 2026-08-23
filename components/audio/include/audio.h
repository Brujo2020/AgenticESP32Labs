#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

// Inicializa el codec ES8311 (mic + parlante) y el bus I2S.
esp_err_t audio_init(void);

// true si el codec respondio y quedo configurado.
bool audio_ready(void);

// Tono con envolvente suave. Sin envolvente suena a chasquido.
void audio_beep(int freq_hz, int ms);

// Nivel de pico del microfono, 0..100, suavizado. Para la barra VU.
//
// SOLO CONSULTA: no lee del microfono. Puede llamarse tantas veces por
// fotograma como haga falta, no cuesta nada y no compite con la captura.
// (Antes si leia, y le robaba muestras al audio que se envia al servidor:
// ver el comentario largo en audio.c.)
int audio_mic_level(void);

// Lee muestras crudas del microfono. Devuelve bytes leidos (0 si no hay).
// DEBE haber un unico lector en todo el firmware -- hoy es tarea_mic en
// components/voice. Dos lectores se reparten las muestras y el audio que
// llega al servidor sale incompleto.
size_t audio_mic_read(int16_t *dst, size_t bytes, int timeout_ms);

// Actualiza el medidor de nivel con muestras ya leidas. La llama
// audio_mic_read() sola; solo hace falta invocarla a mano si alguna vez se
// captura audio por otra via.
void audio_mide_nivel(const int16_t *muestras, size_t bytes);

// Reproduce PCM 16-bit mono a la frecuencia de la placa.
void audio_play_pcm(const void *pcm, size_t bytes);

// Amplificador bajo demanda. audio_play_pcm/audio_beep ya lo encienden solos;
// esto es para el caso en que se quiera anticipar el encendido (por ejemplo al
// recibir "speaking" del servidor, antes de que llegue el primer byte) y que
// el "pop" quede en el silencio previo y no sobre la primera silaba.
void audio_pa(bool on);
bool audio_pa_encendido(void);

// Llena los buffers DMA de silencio. Hay que llamarlo al terminar CUALQUIER
// reproduccion: el canal se creo sin auto_clear, asi que si no, el DMA se
// queda repitiendo en bucle lo ultimo que sonara. Ver el comentario largo en
// audio.c -- esta era la causa del "ti-ti-ti-ti" tras el beep.
void audio_silencio(void);

// Volumen del parlante 0..100 (registro 0x32 del ES8311).
void audio_set_volume(int pct);
int  audio_volume(void);

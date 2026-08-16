#include "ajustes.h"
#include "display.h"
#include "audio.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_log.h"

static const char *TAG = "ajustes";
static ajustes_t s = { .brillo = 85, .volumen = 55, .tema = 0,
                       .scanlines = false, .rejilla = false, .efectos = true };

ajustes_t *ajustes(void) { return &s; }

void ajustes_cargar(void)
{
    nvs_handle_t h;
    if (nvs_open("hud", NVS_READONLY, &h) != ESP_OK) return;
    int32_t v;
    if (nvs_get_i32(h, "brillo", &v)  == ESP_OK) s.brillo = v;
    if (nvs_get_i32(h, "volumen", &v) == ESP_OK) s.volumen = v;
    if (nvs_get_i32(h, "tema", &v)    == ESP_OK) s.tema = v;
    if (nvs_get_i32(h, "scan", &v)    == ESP_OK) s.scanlines = v;
    if (nvs_get_i32(h, "grid", &v)    == ESP_OK) s.rejilla = v;
    if (nvs_get_i32(h, "efec", &v)    == ESP_OK) s.efectos = v;
    nvs_close(h);
    ESP_LOGI(TAG, "cargados: brillo %d volumen %d tema %d efectos %d",
             s.brillo, s.volumen, s.tema, s.efectos);
}

void ajustes_guardar(void)
{
    nvs_handle_t h;
    if (nvs_open("hud", NVS_READWRITE, &h) != ESP_OK) return;
    nvs_set_i32(h, "brillo", s.brillo);
    nvs_set_i32(h, "volumen", s.volumen);
    nvs_set_i32(h, "tema", s.tema);
    nvs_set_i32(h, "scan", s.scanlines);
    nvs_set_i32(h, "grid", s.rejilla);
    nvs_set_i32(h, "efec", s.efectos);
    nvs_commit(h);
    nvs_close(h);
}

void ajustes_aplicar(void)
{
    display_set_brightness(s.brillo);
    audio_set_volume(s.volumen);
}

unsigned short ajustes_acento(void)
{
    switch (s.tema) {
        case 1:  return C_MAGENTA;
        case 2:  return C_LIME;
        case 3:  return C_AMBER;
        case 4:  return C_ICE;
        case 5:  return C_BLOOD;
        case 6:  return C_GREY;
        case 7:  return C_WHITE;
        default: return C_CYAN;
    }
}

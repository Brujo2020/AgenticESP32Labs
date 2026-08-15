// ============================================================
//  Asistente de voz — Spotpear ESP32-S3-1.28-BOX
//  main solo orquesta: cada pieza vive en su componente.
//    board · display · touch · audio · net · hud
// ============================================================
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "display.h"
#include "touch.h"
#include "audio.h"
#include "net.h"
#include "hud.h"

static const char *TAG = "main";

// El clima se refresca en su propia tarea: la peticion HTTP bloquea
// varios segundos y no debe congelar la animacion.
static void tarea_clima(void *arg)
{
    while (1) {
        net_weather_update();
        vTaskDelay(pdMS_TO_TICKS(10 * 60 * 1000));   // cada 10 minutos
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "=== Asistente ESP32-S3 ===");

    ESP_ERROR_CHECK(display_init());
    display_clear(C_DARK);
    display_text_center(120, 110, "BOOT OK", C_GREEN, 2);
    display_flush();

    // Ni el tactil ni el audio abortan el arranque si fallan:
    // se reflejan como puntos rojos en el HUD.
    if (touch_init() != ESP_OK) ESP_LOGW(TAG, "tactil no disponible");
    if (audio_init() != ESP_OK) ESP_LOGW(TAG, "audio no disponible");
    if (audio_ready()) audio_beep(523, 90);

    display_clear(C_DARK);
    display_text_center(120, 110, "CONECTANDO", C_CYAN, 1);
    display_flush();

    if (net_init() == ESP_OK) {
        net_sync_time();
        xTaskCreate(tarea_clima, "clima", 4096, NULL, 4, NULL);
    }

    hud_init();
    hud_set_state(ST_IDLE);

    bool tocado = false;
    while (1) {
        // Cambio de pantalla al tocar (por flanco, no mientras se mantiene)
        int x, y;
        bool ahora = touch_get(&x, &y);
        if (ahora && !tocado) {
            hud_next_screen();
            if (audio_ready()) audio_beep(392, 45);
        }
        tocado = ahora;

        hud_render();
        vTaskDelay(pdMS_TO_TICKS(33));      // ~30 FPS
    }
}

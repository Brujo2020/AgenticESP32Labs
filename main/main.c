// ============================================================
//  Asistente de voz — Spotpear ESP32-S3-1.28-BOX
//  main solo orquesta: cada pieza vive en su componente.
//    board · display · touch · audio · net · hud
// ============================================================
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "display.h"
#include "touch.h"
#include "audio.h"
#include "net.h"
#include "hud.h"
#include "voice.h"

static const char *TAG = "main";

// Direccion del Mac donde corre websocket_bridge.py.
// Se ve en la pantalla SISTEMA del propio Mac, o con: ipconfig getifaddr en0
#define SERVIDOR_HOST  "192.168.1.100"
#define SERVIDOR_PORT  8765

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
    // beep de arranque retirado a peticion: molestaba

    display_clear(C_DARK);
    display_text_center(120, 110, "CONECTANDO", C_CYAN, 1);
    display_flush();

    if (net_init() == ESP_OK) {
        net_sync_time();
        xTaskCreate(tarea_clima, "clima", 4096, NULL, 4, NULL);
        voice_init(SERVIDOR_HOST, SERVIDOR_PORT);
    }

    hud_init();
    hud_set_state(ST_IDLE);

    // Toque corto: cambia de pantalla.
    // Toque mantenido (mas de 400 ms): pulsar para hablar.
    bool tocado = false;
    int64_t t_inicio = 0;
    bool hablando = false;

    while (1) {
        int x, y;
        bool ahora = touch_get(&x, &y);
        int64_t t = esp_timer_get_time() / 1000;

        if (ahora && !tocado) {
            t_inicio = t;
        } else if (ahora && !hablando && (t - t_inicio) > 400) {
            hablando = true;
            voice_talk_start();
        } else if (!ahora && tocado) {
            if (hablando) {
                voice_talk_stop();
                hablando = false;
            } else {
                hud_next_screen();      // fue un toque corto
            }
        }
        tocado = ahora;

        hud_render();
        vTaskDelay(pdMS_TO_TICKS(33));      // ~30 FPS
    }
}

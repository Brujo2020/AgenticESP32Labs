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
#include "ajustes.h"
#include "nvs_flash.h"

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

    // NVS antes que nada: de ahi salen las preferencias guardadas
    esp_err_t nv = nvs_flash_init();
    if (nv == ESP_ERR_NVS_NO_FREE_PAGES || nv == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    ajustes_cargar();

    ESP_ERROR_CHECK(display_init());

    // Ni el tactil ni el audio abortan el arranque si fallan:
    // se reflejan como puntos rojos en el HUD.
    if (touch_init() != ESP_OK) ESP_LOGW(TAG, "tactil no disponible");
    if (audio_init() != ESP_OK) ESP_LOGW(TAG, "audio no disponible");
    ajustes_aplicar();          // brillo y volumen guardados en NVS

    hud_boot_anim();            // secuencia de arranque

    display_clear(C_VOID);
    display_text_center(120, 110, "ESTABLECIENDO ENLACE", C_CYAN, 1);
    display_flush();

    if (net_init() == ESP_OK) {
        net_sync_time();
        xTaskCreate(tarea_clima, "clima", 4096, NULL, 4, NULL);
        // Primero se busca el servidor por mDNS; si no aparece,
        // se recurre a la direccion fija de arriba.
        char ip[16]; int puerto = SERVIDOR_PORT;
        if (net_descubre_servidor(ip, sizeof(ip), &puerto)) {
            voice_init(ip, puerto);
        } else {
            ESP_LOGW(TAG, "mDNS sin respuesta, uso %s", SERVIDOR_HOST);
            voice_init(SERVIDOR_HOST, SERVIDOR_PORT);
        }
    }

    hud_init();
    hud_set_state(ST_IDLE);

    // Toque corto: cambia de pantalla.
    // Toque mantenido (mas de 400 ms): pulsar para hablar.
    bool tocado = false;

    while (1) {
        int x = 0, y = 0;
        bool ahora = touch_get(&x, &y);

        // La logica de botones vive en el HUD; aqui solo se reenvian los eventos
        if (ahora && !tocado)      hud_touch_down(x, y);
        else if (ahora)            hud_touch_hold(x, y);
        else if (!ahora && tocado) hud_touch_up(x, y);
        tocado = ahora;

        hud_render();
        vTaskDelay(pdMS_TO_TICKS(33));      // ~30 FPS
    }
}

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
#include "sdkconfig.h"

static const char *TAG = "main";

// Direccion del equipo donde corre servidor/websocket_bridge.py.
// Solo se usa como fallback si mDNS no responde.
// Configurable en: idf.py menuconfig > AgenticESP32Labs > Servidor agentico
#define SERVIDOR_HOST  CONFIG_HUD_SERVER_HOST
#define SERVIDOR_PORT  CONFIG_HUD_SERVER_PORT

// Bucle de interfaz: lee el tactil, reenvia los eventos al HUD y dibuja.
// Toque corto cambia de pantalla; mantenido mas de 250 ms es pulsar-para-hablar.
static void tarea_hud(void *arg)
{
    bool tocado = false;
    // vTaskDelayUntil mantiene cadencia fija: con vTaskDelay el periodo se
    // desliza segun lo que tarde el fotograma y la animacion tiembla.
    TickType_t last = xTaskGetTickCount();
    while (1) {
        int x = 0, y = 0;
        bool ahora = touch_get(&x, &y);

        // La logica de botones vive en el HUD; aqui solo se reenvian eventos
        if (ahora && !tocado)      hud_touch_down(x, y);
        else if (ahora)            hud_touch_hold(x, y);
        else if (!ahora && tocado) hud_touch_up(x, y);
        tocado = ahora;

        hud_render();
        vTaskDelayUntil(&last, pdMS_TO_TICKS(33));      // ~30 FPS estables
    }
}

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

    // El render va en su propia tarea fijada al core 1. En ESP-IDF la pila
    // WiFi y lwIP viven en el core 0: dejarlos competir con el dibujado por
    // el mismo core produce tirones visibles cuando entra trafico.
    // 8192 y no 4096: hud_render encadena buffers de pila en varias pantallas
    // y la tarea main original ya usaba 8192. Quedarse corto aqui provoca
    // stack overflow y reinicio en bucle, que se ve como "no enciende".
    xTaskCreatePinnedToCore(tarea_hud, "hud", 8192, NULL, 5, NULL, 1);

    // El hilo principal se queda solo vigilando el enlace, sin quemar CPU
    while (1) vTaskDelay(pdMS_TO_TICKS(1000));
}

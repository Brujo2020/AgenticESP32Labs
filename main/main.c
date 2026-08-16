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

// Direccion del servidor donde corre websocket_bridge.py.
// Ahora vive en una VM de AWS Lightsail (IP publica y fija), no en el Mac:
// el ESP32 esta en la red de casa y el servidor en la nube, asi que mDNS
// (_hud._tcp) nunca los va a poner en contacto -- solo funciona dentro de
// la misma red local. Esta IP fija es el unico camino real.
#define SERVIDOR_HOST  "56.125.193.142"
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

// net_init()/mDNS/voice_init() bloquean varios segundos en total. Antes se
// pintaba "ESTABLECIENDO ENLACE" en un solo frame estatico y la pantalla se
// quedaba congelada todo ese rato (parecia trabado, no "cargando"). Ahora
// esa espera corre en su propia tarea y app_main() anima un spinner en el
// hilo principal mientras tanto.
static volatile bool s_red_lista = false;

static void tarea_red(void *arg)
{
    if (net_init() == ESP_OK) {
        net_sync_time();
        xTaskCreate(tarea_clima, "clima", 4096, NULL, 4, NULL);
        char ip[16]; int puerto = SERVIDOR_PORT;
        if (net_descubre_servidor(ip, sizeof(ip), &puerto)) {
            voice_init(ip, puerto);
        } else {
            ESP_LOGW(TAG, "mDNS sin respuesta, uso %s", SERVIDOR_HOST);
            voice_init(SERVIDOR_HOST, SERVIDOR_PORT);
        }
    }
    s_red_lista = true;
    vTaskDelete(NULL);
}

// Spinner de carga: arco que gira mas rapido que cualquier animacion del
// HUD normal, con texto grande, para que se vea claramente "cargando" y no
// "colgado". display_cos_q/sin_q en vez de cosf/sinf (Grupo 2: fuera del
// render las funciones trigonometricas float).
static void anima_enlace(int f)
{
    display_clear(C_VOID);
    int a0 = (f * 14) % 360;             // gira rapido: 14 grados por fotograma
    for (int k = 0; k < 3; k++) {
        int a = (a0 + k * 120) % 360;
        int32_t cs = display_cos_q(a), sn = display_sin_q(a);
        display_arc(120, 120, 90 - k * 10, 3, a, a + 70,
                    display_escala(C_CYAN, 255 - k * 60));
        (void)cs; (void)sn;
    }
    display_text_center(120, 108, "ESTABLECIENDO", C_CYAN, 2);
    display_text_center(120, 132, "ENLACE", C_CYAN, 2);
    // Puntos animados, tipo "..." progresivo, para reforzar que avanza
    char puntos[4] = {0};
    int n = (f / 6) % 4;
    for (int i = 0; i < n; i++) puntos[i] = '.';
    display_text_center(120, 156, puntos, display_escala(C_CYAN, 200), 2);
    display_flush();
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

    xTaskCreate(tarea_red, "red", 6144, NULL, 5, NULL);
    for (int f = 0; !s_red_lista; f++) {
        anima_enlace(f);
        vTaskDelay(pdMS_TO_TICKS(33));      // ~30 FPS, mismo ritmo que el HUD
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

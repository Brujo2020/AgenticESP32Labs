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
#include "bateria.h"
#include "nvs_flash.h"
#include "esp_heap_caps.h"

static const char *TAG = "main";

// Direccion del servidor donde corre websocket_bridge.py.
// Vive en una VM de AWS Lightsail, no en el Mac: el ESP32 esta en la red de
// casa y el servidor en la nube, asi que mDNS (_hud._tcp) nunca los va a
// poner en contacto -- solo funciona dentro de la misma red local.
//
// IP ESTATICA (StaticIp-1), adjunta el 23/08/2026. Ya NO cambia al reiniciar
// la instancia, que es lo que rompia la conexion cada dos por tres: el panel
// decia "sin dispositivo" y no habia ni voz ni ajustes, sin que nada estuviera
// roto en realidad (paso el 22/08: .142 -> .244).
//
// Region: sa-east-1 (Sao Paulo). El rango 15.229.x.x lo delata, y es el dato
// que decide que .pem usar para entrar por SSH.
//
// AVISO DE FACTURACION: una IP estatica de Lightsail es gratis SOLO mientras
// este adjunta a una instancia en marcha. Si algun dia se para la instancia y
// se deja la IP reservada, empieza a costar.
#define SERVIDOR_HOST  "15.229.88.144"
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

// Corre en su propia tarea para no bloquear net_sync_time() sobre la
// conexion de voz: antes se hacia en cadena (hasta 7.5s de NTP + 3s de mDNS
// ANTES de intentar hablar con el servidor) y esos 10s de nada parecian una
// placa colgada quien mira el HUD, cuando solo estaba esperando su turno.
static void tarea_hora(void *arg)
{
    net_sync_time();
    vTaskDelete(NULL);
}

static void tarea_red(void *arg)
{
    if (net_init() == ESP_OK) {
        xTaskCreate(tarea_hora, "hora", 4096, NULL, 3, NULL);
        xTaskCreate(tarea_clima, "clima", 4096, NULL, 4, NULL);

        // mDNS solo encuentra servidores en la MISMA red local (ver nota en
        // net_descubre_servidor): el servidor vive en Lightsail, en la nube,
        // asi que esa busqueda esta condenada a fallar siempre y solo suma
        // 3 segundos muertos antes de cada conexion. Se va directo a la IP
        // conocida; el dia que haya un servidor de verdad en la red local
        // esto se puede reactivar.
        ESP_LOGI(TAG, "conectando a %s:%d", SERVIDOR_HOST, SERVIDOR_PORT);
        voice_init(SERVIDOR_HOST, SERVIDOR_PORT);
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

// Informe de memoria. Existe porque "activar la PSRAM" no es una creencia:
// o se ve el reparto en el log o no se sabe si sirvio de algo.
//
// Que mirar:
//   PSRAM total 0        -> no arranco la PSRAM. Ver sdkconfig.defaults.
//   interna libre        -> lo que gano el reparto. Antes rondaba los 130 kB;
//                           con el framebuffer y los buffers fuera deberia
//                           subir bastante.
//   interna minima       -> el peor momento desde el arranque. ESTE es el
//                           numero que importa: si baja de ~40 kB, cualquier
//                           pico (reconexion TLS, una vista grande) puede
//                           quedarse sin memoria en caliente.
static void informe_memoria(const char *cuando)
{
    size_t i_libre = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    size_t i_min   = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
    size_t i_mayor = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL);
    size_t p_total = heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    size_t p_libre = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);

    ESP_LOGI(TAG, "[RAM %s] interna: %u libre / %u minima / %u bloque mayor",
             cuando, (unsigned)i_libre, (unsigned)i_min, (unsigned)i_mayor);
    if (p_total)
        ESP_LOGI(TAG, "[RAM %s] PSRAM:   %u libre de %u  (%u KB en uso)",
                 cuando, (unsigned)p_libre, (unsigned)p_total,
                 (unsigned)((p_total - p_libre) / 1024));
    else
        ESP_LOGW(TAG, "[RAM %s] PSRAM NO disponible: todo esta en RAM interna",
                 cuando);
}

void app_main(void)
{
    ESP_LOGI(TAG, "=== Asistente ESP32-S3 ===");
    informe_memoria("arranque");

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
    // Tampoco aborta: sin ADC el HUD simplemente no pinta el indicador.
    if (bateria_init() != ESP_OK) ESP_LOGW(TAG, "bateria no disponible");
    ajustes_aplicar();          // brillo y volumen guardados en NVS

    hud_boot_anim();            // secuencia de arranque

    xTaskCreate(tarea_red, "red", 6144, NULL, 5, NULL);
    for (int f = 0; !s_red_lista; f++) {
        anima_enlace(f);
        vTaskDelay(pdMS_TO_TICKS(33));      // ~30 FPS, mismo ritmo que el HUD
    }

    hud_init();
    hud_set_state(ST_IDLE);

    // Segundo informe: este es el que vale. En el de "arranque" todavia no se
    // habia reservado nada (ni framebuffer, ni WiFi, ni colchon de audio).
    // Comparar los dos dice exactamente cuanta RAM interna se ha liberado.
    informe_memoria("todo listo");

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

        // Barato: por dentro solo muestrea el ADC cada 2 s.
        bateria_actualiza();
        hud_render();
        vTaskDelay(pdMS_TO_TICKS(33));      // ~30 FPS
    }
}

#include "bateria.h"
#include "board_pins.h"
#include <stddef.h>
#include <stdint.h>
#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_timer.h"
#include "esp_log.h"

static const char *TAG = "bateria";

// GPIO1 en el ESP32-S3 es ADC1_CH0. Si algun dia cambia el pin en
// board_pins.h, este mapeo hay que revisarlo: no es automatico.
#define BAT_UNIT     ADC_UNIT_1
#define BAT_CHANNEL  ADC_CHANNEL_0
#define BAT_ATTEN    ADC_ATTEN_DB_12    // hasta ~3.1 V en el pin

// La placa mide la bateria por un divisor resistivo de 2:1, asi que una LiPo
// de 4.2 V llega al pin como ~2.1 V (dentro del rango del ADC).
#define DIVISOR      2

// Curva de una celda LiPo 1S. No es lineal: usar una recta entre 3.0 y 4.2
// hace que el indicador se quede clavado en "50%" media tarde y luego caiga
// en picado. Estos puntos aproximan la descarga real.
typedef struct { int mv; int pct; } punto_t;
static const punto_t CURVA[] = {
    {4200,100},{4100,92},{4000,84},{3900,74},{3800,62},
    {3750,54},{3700,44},{3650,32},{3600,22},{3500,12},
    {3400,6},{3300,2},{3000,0},
};

static adc_oneshot_unit_handle_t s_adc = NULL;
static adc_cali_handle_t s_cali = NULL;
static bool s_ok = false;
static bool s_cargando = false;
static int  s_mv = 0;            // media movil, en mV de bateria
static int64_t s_ultimo_us = 0;

#define PERIODO_US  (2 * 1000 * 1000)   // muestrea cada 2 s

esp_err_t bateria_init(void)
{
    adc_oneshot_unit_init_cfg_t uc = { .unit_id = BAT_UNIT };
    if (adc_oneshot_new_unit(&uc, &s_adc) != ESP_OK) {
        ESP_LOGW(TAG, "no se pudo abrir el ADC");
        return ESP_FAIL;
    }
    adc_oneshot_chan_cfg_t cc = { .atten = BAT_ATTEN, .bitwidth = ADC_BITWIDTH_DEFAULT };
    if (adc_oneshot_config_channel(s_adc, BAT_CHANNEL, &cc) != ESP_OK) {
        ESP_LOGW(TAG, "no se pudo configurar el canal");
        return ESP_FAIL;
    }

    // Calibracion de fabrica. Sin ella la lectura se desvia bastante entre
    // chips; si no esta disponible se sigue adelante con la conversion
    // aproximada -- mejor un porcentaje algo impreciso que ninguno.
    //
    // Que esquema existe depende del chip y de la version de IDF, asi que se
    // elige en tiempo de compilacion igual que en los ejemplos oficiales: el
    // ESP32-S3 usa curve fitting, otros usan line fitting. Sin los #if, este
    // fichero no compila en cualquier target aunque el codigo sea correcto.
#if defined(ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED) && ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    adc_cali_curve_fitting_config_t cal = {
        .unit_id = BAT_UNIT, .chan = BAT_CHANNEL,
        .atten = BAT_ATTEN, .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    if (adc_cali_create_scheme_curve_fitting(&cal, &s_cali) != ESP_OK) s_cali = NULL;
#elif defined(ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED) && ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
    adc_cali_line_fitting_config_t cal = {
        .unit_id = BAT_UNIT, .atten = BAT_ATTEN, .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    if (adc_cali_create_scheme_line_fitting(&cal, &s_cali) != ESP_OK) s_cali = NULL;
#else
    s_cali = NULL;
#endif
    if (!s_cali) ESP_LOGW(TAG, "sin calibracion de fabrica, lectura aproximada");

    // Deteccion de carga: el pin va a masa cuando el cargador esta activo.
    gpio_config_t g = {
        .pin_bit_mask = 1ULL << BOARD_CHARGE_DET,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&g);

    s_ok = true;
    s_ultimo_us = 0;
    bateria_actualiza();          // un valor valido desde el primer frame
    ESP_LOGI(TAG, "lista: %d mV (%d%%)", s_mv, bateria_pct());
    return ESP_OK;
}

bool bateria_disponible(void) { return s_ok; }
int  bateria_mv(void)         { return s_ok ? s_mv : 0; }
bool bateria_cargando(void)   { return s_ok && s_cargando; }

void bateria_actualiza(void)
{
    if (!s_ok) return;
    int64_t ahora = esp_timer_get_time();
    if (s_ultimo_us && (ahora - s_ultimo_us) < PERIODO_US) return;
    s_ultimo_us = ahora;

    // Varias lecturas y su media: una sola muestra del ADC salta decenas de mV.
    int suma = 0, validas = 0;
    for (int i = 0; i < 8; i++) {
        int crudo = 0;
        if (adc_oneshot_read(s_adc, BAT_CHANNEL, &crudo) != ESP_OK) continue;
        int mv = 0;
        if (s_cali) {
            if (adc_cali_raw_to_voltage(s_cali, crudo, &mv) != ESP_OK) continue;
        } else {
            mv = crudo * 3100 / 4095;      // aproximacion sin calibrar
        }
        suma += mv; validas++;
    }
    if (!validas) return;

    int medido = (suma / validas) * DIVISOR;
    // Media movil suave: el porcentaje no debe bailar entre fotogramas.
    s_mv = s_mv ? (s_mv * 3 + medido) / 4 : medido;
    s_cargando = (gpio_get_level(BOARD_CHARGE_DET) == 0);
}

int bateria_pct(void)
{
    if (!s_ok || s_mv <= 0) return -1;
    if (s_mv >= CURVA[0].mv) return 100;
    int n = sizeof(CURVA) / sizeof(CURVA[0]);
    if (s_mv <= CURVA[n - 1].mv) return 0;
    for (int i = 0; i < n - 1; i++) {
        if (s_mv <= CURVA[i].mv && s_mv > CURVA[i + 1].mv) {
            int dv = CURVA[i].mv - CURVA[i + 1].mv;
            int dp = CURVA[i].pct - CURVA[i + 1].pct;
            return CURVA[i + 1].pct + (s_mv - CURVA[i + 1].mv) * dp / (dv ? dv : 1);
        }
    }
    return -1;
}

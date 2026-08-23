// ============================================================
//  ES8311: codec de audio (microfono + parlante)
//
//  El componente oficial "espressif/es8311" NO sirve en IDF 6.0:
//  usa la API I2C legacy, que fue eliminada. Aqui se reimplementa
//  con i2c_master. La secuencia de registros esta derivada del
//  driver oficial (esp-bsp/components/es8311), fijada para
//  MCLK = 256 x fs. Los divisores salen de la fila {6144000, 24000}
//  de su tabla de coeficientes.
// ============================================================
#include "audio.h"
#include "board_pins.h"
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_check.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_timer.h"

static const char *TAG = "audio";
static i2c_master_bus_handle_t s_bus = NULL;
static i2c_master_dev_handle_t s_dev = NULL;
static i2s_chan_handle_t s_tx = NULL, s_rx = NULL;
static bool s_ready = false;
static int  s_level = 0;
static int  s_vol = 55;

// Definidas mas abajo, junto al resto de la logica del amplificador; se
// declaran aqui porque audio_init() lanza la tarea antes de esa seccion.
static void tarea_pa(void *arg);
void audio_pa(bool on);

static esp_err_t reg_w(uint8_t reg, uint8_t val)
{
    uint8_t b[2] = { reg, val };
    return i2c_master_transmit(s_dev, b, 2, 200);
}

esp_err_t audio_init(void)
{
    i2c_master_bus_config_t bc = {
        .i2c_port = BOARD_AU_I2C_PORT,
        .sda_io_num = BOARD_AU_SDA,
        .scl_io_num = BOARD_AU_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bc, &s_bus), TAG, "bus i2c");

    i2c_device_config_t dc = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = BOARD_ES8311_ADDR,
        .scl_speed_hz = 400000,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(s_bus, &dc, &s_dev), TAG, "add dev");
    ESP_RETURN_ON_ERROR(i2c_master_probe(s_bus, BOARD_ES8311_ADDR, 200), TAG, "sin respuesta");

    gpio_config_t pa = { .pin_bit_mask = 1ULL << BOARD_PA_EN, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&pa);
    // El amplificador arranca APAGADO. Antes se dejaba a 1 para siempre y el
    // clase D amplificaba su propio ruido de fondo las 24 horas: ese siseo
    // constante es lo que hace que un aparato suene "barato" aunque la voz
    // este bien. Lo enciende audio_pa() justo antes de sonar y lo apaga el
    // guardian tras PA_APAGADO_MS de silencio (mismo patron que el
    // audio_power_timer_ de Xiaozhi).
    gpio_set_level(BOARD_PA_EN, 0);

    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    cc.dma_desc_num = 6;
    cc.dma_frame_num = 240;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&cc, &s_tx, &s_rx), TAG, "i2s chan");

    i2s_std_config_t std = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(BOARD_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = BOARD_I2S_MCLK, .bclk = BOARD_I2S_BCLK, .ws = BOARD_I2S_WS,
            .dout = BOARD_I2S_DOUT, .din = BOARD_I2S_DIN,
        },
    };
    std.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256;
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_tx, &std), TAG, "i2s tx");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_rx, &std), TAG, "i2s rx");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_tx), TAG, "tx on");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_rx), TAG, "rx on");

    // ---- arranque del codec ----
    reg_w(0x00, 0x1F); vTaskDelay(pdMS_TO_TICKS(20));
    reg_w(0x00, 0x00);
    reg_w(0x00, 0x80);        // power-on, modo esclavo
    reg_w(0x01, 0x3F);        // MCLK desde el pin MCLK, relojes activos
    reg_w(0x02, 0x00);        // pre_div=1, pre_multi=1
    reg_w(0x03, 0x10);        // fs_mode=0, adc_osr
    reg_w(0x04, 0x10);        // dac_osr
    reg_w(0x05, 0x00);        // adc_div=1, dac_div=1
    reg_w(0x06, 0x03);        // bclk_div=4
    reg_w(0x07, 0x00);
    reg_w(0x08, 0xFF);
    reg_w(0x09, 0x0C);        // I2S 16 bits entrada
    reg_w(0x0A, 0x0C);        // I2S 16 bits salida
    reg_w(0x0D, 0x01);        // analogico arriba
    reg_w(0x0E, 0x02);        // PGA + modulador ADC
    reg_w(0x12, 0x00);        // DAC arriba
    reg_w(0x13, 0x10);        // salida al driver del parlante
    reg_w(0x1C, 0x6A);        // ADC: bypass EQ, cancela offset DC
    reg_w(0x37, 0x08);        // DAC: bypass EQ
    reg_w(0x17, 0xC8);        // ganancia ADC
    reg_w(0x14, 0x1A);        // microfono analogico, PGA alta
    reg_w(0x32, 0x8C);        // volumen ~55%

    s_ready = true;
    xTaskCreate(tarea_pa, "pa_off", 2048, NULL, 2, NULL);
    ESP_LOGI(TAG, "ES8311 listo a %d Hz", BOARD_SAMPLE_RATE);
    return ESP_OK;
}

bool audio_ready(void) { return s_ready; }

// ============================================================
//  Amplificador bajo demanda
// ============================================================
// El PA se enciende al primer byte de audio y se apaga solo cuando lleva
// PA_APAGADO_MS sin nada que sonar. Dos motivos, y el segundo importa mas:
//
//  1) El siseo del clase D desaparece cuando no hay nada que decir.
//  2) El "pop" del encendido queda ANTES del audio, no encima: hay que dar
//     unos ms al amplificador para estabilizarse, y por eso audio_pa(true)
//     espera. Encender y escribir en la misma instruccion mete el chasquido
//     justo sobre la primera silaba.
#define PA_APAGADO_MS   3000

static volatile int64_t s_pa_ultimo_us = 0;
static volatile bool    s_pa_on = false;

void audio_pa(bool on)
{
    if (!s_ready) return;
    s_pa_ultimo_us = esp_timer_get_time();
    if (on == s_pa_on) return;
    gpio_set_level(BOARD_PA_EN, on ? 1 : 0);
    s_pa_on = on;
    if (on) vTaskDelay(pdMS_TO_TICKS(8));   // que se estabilice antes de sonar
}

bool audio_pa_encendido(void) { return s_pa_on; }

// Corre en su propia tarea: apagar el PA desde la tarea de audio obligaria a
// que esta se despertara sola para comprobarlo, y esta bloqueada esperando
// datos de red la mayor parte del tiempo.
static void tarea_pa(void *arg)
{
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(500));
        if (s_pa_on &&
            (esp_timer_get_time() - s_pa_ultimo_us) > (int64_t)PA_APAGADO_MS * 1000)
            audio_pa(false);
    }
}

void audio_beep(int freq_hz, int ms)
{
    if (!s_ready) return;
    audio_pa(true);
    int16_t buf[256];
    size_t w;
    int total = BOARD_SAMPLE_RATE * ms / 1000;
    int fade  = BOARD_SAMPLE_RATE / 200;      // 5 ms de subida/bajada
    for (int i = 0; i < total; i += 256) {
        for (int j = 0; j < 256; j++) {
            int n = i + j;
            float env = 1.0f;
            if (n < fade)              env = (float)n / fade;
            else if (n > total - fade) env = (float)(total - n) / fade;
            if (env < 0) env = 0;
            buf[j] = (int16_t)(2600 * env * sinf(2.0f * (float)M_PI * freq_hz * n / BOARD_SAMPLE_RATE));
        }
        i2s_channel_write(s_tx, buf, sizeof(buf), &w, pdMS_TO_TICKS(300));
    }
    // Sin esto el DMA se queda repitiendo el tono en bucle (ver audio_silencio).
    audio_silencio();
}

size_t audio_mic_read(int16_t *dst, size_t bytes, int timeout_ms)
{
    if (!s_ready) return 0;
    size_t got = 0;
    if (i2s_channel_read(s_rx, dst, bytes, &got, pdMS_TO_TICKS(timeout_ms)) != ESP_OK) return 0;
    // El nivel se calcula aqui, sobre las muestras que YA se han leido. Ver
    // audio_mic_level() para por que esto importa tanto.
    if (got) audio_mide_nivel(dst, got);
    return got;
}

// Actualiza el medidor a partir de muestras ya leidas por otro.
void audio_mide_nivel(const int16_t *muestras, size_t bytes)
{
    int32_t peak = 0;
    int n = (int)(bytes / sizeof(int16_t));
    for (int i = 0; i < n; i++) {
        int32_t v = muestras[i] < 0 ? -muestras[i] : muestras[i];
        if (v > peak) peak = v;
    }
    int lvl = (int)((peak * 100) / 32768);
    s_level = (s_level * 6 + lvl * 4) / 10;   // suavizado
}

// SOLO consulta: no toca el I2S.
//
// Antes esta funcion leia del canal RX por su cuenta, y ahi estaba el
// problema gordo del reconocimiento de voz: el HUD la llama hasta tres veces
// por fotograma a 30 fps (barra VU, anillo de escucha y deteccion de
// silencio), o sea ~90 lecturas por segundo. Cada una se llevaba muestras del
// MISMO canal del que tarea_mic saca el audio que se manda al servidor.
//
// Resultado: mientras hablabas, buena parte de tus muestras se iban al
// medidor y nunca salian por el websocket. Whisper recibia la frase
// agujereada y transcribia cualquier cosa -- y el agente contestaba, muy
// correctamente, a algo que tu no habias dicho. Parecia que "el modelo decia
// sandeces" cuando en realidad no estaba oyendo la pregunta entera.
//
// Ahora hay un unico lector del microfono (tarea_mic) y el nivel se calcula
// de paso, sobre esas mismas muestras.
int audio_mic_level(void)
{
    return s_ready ? s_level : 0;
}

void audio_play_pcm(const void *pcm, size_t bytes)
{
    if (!s_ready || !pcm || !bytes) return;
    audio_pa(true);
    size_t w;
    i2s_channel_write(s_tx, pcm, bytes, &w, pdMS_TO_TICKS(2000));
}

// Deja los buffers DMA en silencio.
//
// Por que hace falta: el canal se creo sin 'auto_clear', asi que cuando el TX
// se queda sin datos nuevos el DMA NO calla -- sigue emitiendo en bucle lo
// ultimo que quedara en sus descriptores. Despues del beep de 1200 Hz eso
// suena como un "ti-ti-ti-ti" rapidisimo: son dma_desc_num(6) x
// dma_frame_num(240) = 1440 muestras = 60 ms de bucle, unas 16 repeticiones
// por segundo, y no para hasta que llega audio nuevo.
//
// Se arregla escribiendo silencio en vez de activar auto_clear en la config
// del canal porque el nombre de ese campo ha cambiado entre versiones de
// ESP-IDF (auto_clear -> auto_clear_after_cb); esto funciona en todas.
void audio_silencio(void)
{
    if (!s_ready) return;
    // 2048 muestras (~85 ms) cubren de sobra los 1440 frames del anillo DMA.
    static const int16_t ceros[512] = {0};
    size_t w;
    for (int i = 0; i < 4; i++)
        i2s_channel_write(s_tx, ceros, sizeof(ceros), &w, pdMS_TO_TICKS(300));
}

void audio_set_volume(int pct)
{
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    s_vol = pct;
    if (!s_ready) return;
    // El registro 0x32 va de 0 a 255 de forma lineal
    reg_w(0x32, (uint8_t)(pct ? (pct * 256 / 100) - 1 : 0));
}

int audio_volume(void) { return s_vol; }

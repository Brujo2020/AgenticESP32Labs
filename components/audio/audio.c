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

static const char *TAG = "audio";
static i2c_master_bus_handle_t s_bus = NULL;
static i2c_master_dev_handle_t s_dev = NULL;
static i2s_chan_handle_t s_tx = NULL, s_rx = NULL;
static bool s_ready = false;
static int  s_level = 0;

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
    gpio_set_level(BOARD_PA_EN, 1);          // habilita el amplificador

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
    ESP_LOGI(TAG, "ES8311 listo");
    return ESP_OK;
}

bool audio_ready(void) { return s_ready; }

void audio_beep(int freq_hz, int ms)
{
    if (!s_ready) return;
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
}

size_t audio_mic_read(int16_t *dst, size_t bytes, int timeout_ms)
{
    if (!s_ready) return 0;
    size_t got = 0;
    if (i2s_channel_read(s_rx, dst, bytes, &got, pdMS_TO_TICKS(timeout_ms)) != ESP_OK) return 0;
    return got;
}

int audio_mic_level(void)
{
    if (!s_ready) return 0;
    int16_t buf[256];
    size_t got = audio_mic_read(buf, sizeof(buf), 0);
    if (got == 0) return s_level;
    int32_t peak = 0;
    for (int i = 0; i < (int)(got / sizeof(int16_t)); i++) {
        int32_t v = buf[i] < 0 ? -buf[i] : buf[i];
        if (v > peak) peak = v;
    }
    int lvl = (int)((peak * 100) / 32768);
    s_level = (s_level * 6 + lvl * 4) / 10;   // suavizado
    return s_level;
}

void audio_play_pcm(const void *pcm, size_t bytes)
{
    if (!s_ready || !pcm || !bytes) return;
    size_t w;
    i2s_channel_write(s_tx, pcm, bytes, &w, pdMS_TO_TICKS(2000));
}

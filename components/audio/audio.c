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
#include "esp_heap_caps.h"

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

    // CAUSA RAIZ REAL encontrada (23 ago 2026), comparando linea a linea
    // contra el firmware de referencia xiaozhi-esp32
    // (main/audio/codecs/es8311_audio_codec.cc, CreateDuplexChannels()):
    // ellos configuran el I2S en STEREO con slot_mask=BOTH, no en MONO. El
    // ES8311 en este hardware transmite/recibe en AMBOS slots I2S (L y R)
    // aunque el audio real sea mono -- pedir I2S_SLOT_MODE_MONO hacia que
    // el driver del ESP32 leyera solo uno de los dos slots, perdiendo la
    // mitad de los datos reales y quedandose con un patron intercalado que
    // sonaba grave/suavizado/con tono espurio. Ese era el bug de verdad, NO
    // el reloj: el intento anterior de subir todo a 32kHz "por si era el
    // MCLK" no cambio nada porque el problema nunca fue la velocidad del
    // reloj, era que faltaba la mitad del audio.
    //
    // Vuelve a I2S_STD_CLK_DEFAULT_CONFIG(BOARD_SAMPLE_RATE) (16000 Hz
    // directo, sin capa de conversion 32k<->16k en software: ya no hace
    // falta) y a slot STEREO/BOTH, igual que Xiaozhi. audio_mic_read()
    // vuelve a leer directo sin downsample -- ver mas abajo.
    i2s_std_config_t std = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(BOARD_SAMPLE_RATE),
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_16BIT,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
            .slot_mode = I2S_SLOT_MODE_STEREO,
            .slot_mask = I2S_STD_SLOT_BOTH,
            .ws_width = I2S_DATA_BIT_WIDTH_16BIT,
            .ws_pol = false,
            .bit_shift = true,
        },
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
    //
    // HISTORIA DEL BUG (23 ago 2026) -- causa raiz real encontrada al
    // final, comparando linea a linea contra xiaozhi-esp32:
    //
    // A 16000 Hz declarados, grabaciones reales mostraban audio grave y
    // distorsionado. Se sospecho primero del reloj (MCLK no exacto) y se
    // probo subir todo a 32000 Hz con conversion de tasa en software -- NO
    // cambio nada, el problema persistio identico. Eso descarto el reloj.
    //
    // La causa real: el I2S estaba en I2S_SLOT_MODE_MONO, pero el ES8311 en
    // este hardware transmite/recibe en AMBOS slots I2S (L y R) aunque el
    // audio sea mono -- exactamente como lo configura xiaozhi-esp32
    // (main/audio/codecs/es8311_audio_codec.cc, CreateDuplexChannels():
    // slot_mode=STEREO, slot_mask=BOTH). Pedir MONO hacia que el driver
    // leyera/escribiera solo uno de los dos slots, perdiendo la mitad de
    // los datos reales -- de ahi el patron de audio "suavizado" con un tono
    // espurio dominante y transcripciones sin ninguna relacion con lo dicho.
    //
    // Fix real: I2S en STEREO/BOTH (ver std.slot_cfg arriba), 16000 Hz
    // directo (sin capa de conversion de tasa, nunca hizo falta). Cada
    // muestra logica se duplica en L/R al escribir (audio_play_pcm,
    // audio_beep) y se extrae de un canal al leer (audio_mic_read).
    //
    // Los registros de abajo son la fila oficial de Espressif
    // (esp-bsp/components/es8311/es8311.c) para MCLK=4096000 Hz (=256 x
    // 16000) con fs=16000: pre_div=1, pre_multi=0, adc_div=1, dac_div=1,
    // bclk_div=4.
    reg_w(0x00, 0x1F); vTaskDelay(pdMS_TO_TICKS(20));
    reg_w(0x00, 0x00);
    reg_w(0x00, 0x80);        // power-on, modo esclavo
    reg_w(0x01, 0x3F);        // MCLK desde el pin MCLK, relojes activos
    reg_w(0x02, 0x00);        // pre_div=1, pre_multi=0 (fila oficial 4096000/16000)
    reg_w(0x03, 0x10);        // fs_mode=0, adc_osr=0x10
    reg_w(0x04, 0x10);        // dac_osr
    reg_w(0x05, 0x00);        // adc_div=1, dac_div=1 (igual en ambas filas)
    reg_w(0x06, 0x03);        // bclk_div=4 (igual en ambas filas)
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
    // Ganancia del microfono: estaba en el maximo posible (PGA 0xA = ~33dB,
    // mas 0xC8 de ganancia digital ADC encima). Resultado medido en el
    // servidor: audioop.max() del PCM daba 32768 -- fondo de escala TOTAL,
    // es decir clipping, no voz limpia. Con la onda cortada en el propio
    // ESP32 (antes de llegar a la red), el STT recibia una forma de onda
    // rota y alucinaba frases sueltas ("gracias por ver el video", tipico
    // de Whisper con entrada saturada/sin habla reconocible) -- se veia
    // como "el reconocimiento de voz es malo" cuando el problema era este
    // registro, puesto antes de tener manera de medir el nivel real.
    //
    // 0x00 = 0dB de ganancia digital ADC (neutro): la normalizacion de
    // volumen ya la hace el servidor (normaliza() en websocket_bridge.py),
    // que SI sabe subir el nivel si hace falta -- pero no puede arreglar
    // clipping, solo amplificar lo que ya esta limpio.
    // OJO -- dos intentos anteriores fallaron por tocar el registro
    // equivocado. 0x14 NO es la ganancia del microfono: es el registro que
    // HABILITA el microfono analogico (el propio driver oficial de
    // Espressif, esp-bsp/components/es8311/es8311.c, lo deja fijo en 0x1A
    // con el comentario "enable analog MIC and max PGA gain" -- ese "max
    // PGA gain" del comentario es enganoso, 0x1A es simplemente EL VALOR DE
    // FABRICA para habilitar la entrada, no un control ajustable). Tocar
    // ese nibble bajo (probado con 0x04 y con 0x15) no bajo la ganancia:
    // rompio el bit de habilitacion y silencio el mic entero (confirmado
    // con el medidor de nivel en vivo del propio ESP32: 0% sostenido).
    //
    // La ganancia real del microfono vive en OTRO registro: 0x16
    // (ES8311_ADC_REG16, "ADC gain scale up" segun
    // es8311_microphone_gain_set() en el driver oficial). Ese registro no
    // se tocaba en absoluto -- se quedaba en su valor de reset del chip,
    // que resulto demasiado alto para este hardware (pico=32768, clipping
    // total, confirmado por el log del servidor).
    //
    // Fix correcto: 0x14 vuelve a 0x1A (el valor de fabrica que SI habilita
    // el mic, sin tocarlo mas) y 0x17 vuelve a como estaba (ganancia
    // digital ADC de fabrica). El ajuste de nivel se hace en 0x16.
    // es8311_mic_gain_t va de 0 (0dB) a 10 (+42dB) en el driver oficial, en
    // pasos de a 3dB aprox. Se prueba con 0x03 (~9dB, bien conservador) para
    // salir de la saturacion total sin pasarse al otro extremo. Verificar
    // con "nivel de audio del turno: pico=" en el log del servidor: si
    // sigue saturado (pico pegado a 32767) subir el offset de gama a 0x02,
    // 0x01...; si queda flojo (pico < 3000) probar 0x04, 0x05...
    reg_w(0x17, 0xC8);        // ganancia digital ADC: valor de fabrica (no es el problema)
    reg_w(0x14, 0x1A);        // habilita microfono analogico: valor de fabrica, NO TOCAR
    reg_w(0x16, 0x03);        // ganancia PGA real del mic (registro correcto): ~9dB
    reg_w(0x32, 0x8C);        // volumen ~55%

    s_ready = true;
    xTaskCreate(tarea_pa, "pa_off", 2048, NULL, 2, NULL);
    ESP_LOGI(TAG, "ES8311 listo a %d Hz (I2S stereo/BOTH, mono real duplicado en L/R)",
             BOARD_SAMPLE_RATE);
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
    // FIX chasquido de cierre (24/ago/2026): apagar el GPIO del PA en seco
    // corta la salida analogica mientras el capacitor de acople / la etapa
    // de salida del ES8311 todavia tiene carga -- eso es el "pop" agudo al
    // final. audio_silencio() ya se llama antes desde voice.c cuando
    // termina la racha, pero por si el PA se apagara desde otro camino
    // (guardian tarea_pa, beeps, etc.) se refuerza aqui: silencio real en
    // el DMA justo antes de cortar el GPIO, nunca sobre la ultima muestra
    // de voz a mitad de onda.
    if (!on) audio_silencio();
    gpio_set_level(BOARD_PA_EN, on ? 1 : 0);
    s_pa_on = on;
    if (on) vTaskDelay(pdMS_TO_TICKS(8));   // que se estabilice antes de sonar
    else    vTaskDelay(pdMS_TO_TICKS(15));  // deja que el silencio real llegue al altavoz antes de cortar la alimentacion
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
    // Stereo: cada muestra logica sale duplicada en L y R (buf[2*j]=L,
    // buf[2*j+1]=R). El slot_mode ahora es STEREO/BOTH (ver audio_init(),
    // mismo patron que xiaozhi-esp32) -- escribir solo un canal dejaria el
    // otro con lo que hubiera antes en el buffer DMA.
    int16_t buf[512];   // 256 muestras logicas x 2 canales
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
            int16_t s = (int16_t)(2600 * env * sinf(2.0f * (float)M_PI * freq_hz * n / BOARD_SAMPLE_RATE));
            buf[2 * j] = s; buf[2 * j + 1] = s;
        }
        i2s_channel_write(s_tx, buf, sizeof(buf), &w, pdMS_TO_TICKS(300));
    }
    // Sin esto el DMA se queda repitiendo el tono en bucle (ver audio_silencio).
    audio_silencio();
}

size_t audio_mic_read(int16_t *dst, size_t bytes, int timeout_ms)
{
    if (!s_ready) return 0;
    // El I2S ahora esta en STEREO/BOTH (ver audio_init()): el codec entrega
    // pares intercalados L,R,L,R,... por cada muestra logica, aunque el
    // audio real sea mono. Leer en MONO (como se hacia antes) descartaba de
    // hecho la mitad de los datos reales, no "la otra mitad silenciosa" --
    // de ahi la distorsion. Aqui se lee el doble de bytes (stereo) y se
    // extrae un solo canal (L) para quedarse con las muestras mono reales,
    // a BOARD_SAMPLE_RATE directo, sin ninguna conversion de tasa.
    size_t muestras_pedidas = bytes / sizeof(int16_t);
    size_t bytes_hw = muestras_pedidas * 2 * sizeof(int16_t);   // x2 por ser stereo
    int16_t crudo[1024];   // 2048 bytes: cubre hasta 512 muestras logicas pedidas
    if (bytes_hw > sizeof(crudo)) bytes_hw = sizeof(crudo);

    size_t got_hw = 0;
    if (i2s_channel_read(s_rx, crudo, bytes_hw, &got_hw, pdMS_TO_TICKS(timeout_ms)) != ESP_OK)
        return 0;
    if (!got_hw) return 0;

    size_t n_hw = got_hw / sizeof(int16_t);   // total de valores L+R intercalados
    size_t n_pares = n_hw / 2;                // pares completos L,R disponibles
    size_t n_pedidas = muestras_pedidas < n_pares ? muestras_pedidas : n_pares;

    // Extrae el canal L (indice par: 0, 2, 4, ...): el ES8311 en este
    // hardware es mono real, asi que L y R deberian traer el mismo dato o
    // uno de los dos ser el valido -- se confirma de oido con la primera
    // prueba tras este cambio. Si R resultara ser el canal correcto en vez
    // de L, es un cambio de un indice (crudo[2*i+1] en vez de crudo[2*i]).
    for (size_t i = 0; i < n_pedidas; i++)
        dst[i] = crudo[2 * i];
    size_t got = n_pedidas * sizeof(int16_t);

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

    // El I2S ahora esta en STEREO/BOTH (ver audio_init(), mismo patron que
    // xiaozhi-esp32): cada muestra logica hay que escribirla duplicada en
    // L y R, si no, uno de los dos canales del codec se queda con lo que
    // hubiera antes en el buffer DMA -- ya no hace falta convertir tasa
    // (16000 logico == 16000 real), solo duplicar a stereo.
    //
    // heap_caps_malloc en vez de la pila: estos bloques vienen de
    // tarea_audio en voice.c con trozos de frase completos, pueden ser
    // varios KB -- una tarea FreeRTOS no tiene margen de stack para eso.
    size_t n_in = bytes / sizeof(int16_t);
    if (n_in < 1) return;
    size_t n_out = n_in * 2;   // x2 por duplicar a stereo, no por cambiar tasa
    int16_t *stereo = heap_caps_malloc(n_out * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    if (!stereo) stereo = heap_caps_malloc(n_out * sizeof(int16_t), MALLOC_CAP_DEFAULT);
    if (!stereo) {
        // Sin memoria para duplicar a stereo: no deberia pasar nunca en la
        // practica (PSRAM de sobra). Mejor no sonar que arriesgar un canal
        // con basura -- se aborta en vez de escribir 'pcm' tal cual (eso
        // dejaria solo medio buffer, mitad L real y mitad lo que hubiera).
        ESP_LOGE(TAG, "sin memoria para duplicar audio a stereo, se descarta");
        return;
    }
    const int16_t *in = (const int16_t *)pcm;
    for (size_t i = 0; i < n_in; i++) {
        stereo[2 * i] = in[i];
        stereo[2 * i + 1] = in[i];
    }
    i2s_channel_write(s_tx, stereo, n_out * sizeof(int16_t), &w, pdMS_TO_TICKS(2000));
    heap_caps_free(stereo);
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

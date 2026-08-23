// ============================================================
//  Spotpear ESP32-S3-1.28-BOX — pinout oficial de la placa
//  Fuente: 78/xiaozhi-esp32, sp-esp32-s3-1.28-box/config.h
//  UNICA fuente de verdad: ningun otro archivo define pines.
// ============================================================
#pragma once

// ---- Pantalla GC9A01A (SPI) ----
#define BOARD_LCD_SCLK      4
#define BOARD_LCD_MOSI      2
#define BOARD_LCD_CS        5
#define BOARD_LCD_DC        47
#define BOARD_LCD_RST       38
#define BOARD_LCD_BL        42      // OJO: logica invertida, 0 = encendido
#define BOARD_LCD_H_RES     240
#define BOARD_LCD_V_RES     240
#define BOARD_LCD_SPI_HZ    (40 * 1000 * 1000)

// ---- Tactil CST816 (I2C bus 0) ----
#define BOARD_TP_SDA        11
#define BOARD_TP_SCL        7
#define BOARD_TP_RST        6
#define BOARD_TP_INT        12
#define BOARD_TP_I2C_PORT   I2C_NUM_0

// ---- Audio ES8311 (I2C bus 1 + I2S) ----
#define BOARD_AU_SDA        15
#define BOARD_AU_SCL        14
#define BOARD_AU_I2C_PORT   I2C_NUM_1
#define BOARD_ES8311_ADDR   0x18
#define BOARD_I2S_MCLK      16
#define BOARD_I2S_BCLK      9
#define BOARD_I2S_WS        45
#define BOARD_I2S_DIN       10      // microfono -> ESP32
#define BOARD_I2S_DOUT      8       // ESP32 -> parlante
#define BOARD_PA_EN         46      // habilita el amplificador
// 16 kHz de punta a punta. NO es "menos calidad": es no resamplear NUNCA.
//
// Antes esto era 24000 y ahi estaba el timbre metalico de la voz. Polly con
// OutputFormat=pcm SOLO entrega 8000 o 16000, asi que el servidor subia de
// 16k a 24k con audioop.ratecv -- un interpolador lineal SIN filtro
// anti-imagen. Al subir 16->24 aparecen replicas espectrales alrededor de
// 8 kHz dentro de la banda audible: eso es el aspero que se oia, y no venia
// de Polly (Lucia neuronal suena bien).
//
// A 16000 la cadena entera es nativa y no hay ninguna conversion:
//   Polly pcm      -> 16000  nativo
//   Transcribe STT -> 16000  es su tasa de trabajo
//   Piper x_low    -> 16000  nativo
// La banda util del habla muere en ~8 kHz, asi que 16 kHz la cubre entera:
// subir a 24 kHz no anadia informacion, solo artefactos.
//
// De regalo: 33% menos bytes por WiFi -> menos jitter y menos cortes.
//
// Los registros del ES8311 (0x02-0x06) NO hay que tocarlos: estan fijados
// para la relacion MCLK = 256 x fs, no para 24000 en absoluto. A 16 kHz el
// I2S genera MCLK = 4.096 MHz, sigue siendo 256x, y los mismos divisores
// (pre_div=1, pre_multi=1, adc_div=1, dac_div=1, bclk_div=4) valen igual.
#define BOARD_SAMPLE_RATE   16000

// ---- Otros ----
#define BOARD_LED           48
#define BOARD_BTN_BOOT      0
#define BOARD_BAT_ADC       1
#define BOARD_CHARGE_DET    41

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
// 80 MHz: el GC9A01A lo aguanta y duplica el ancho de banda de volcado.
// El framebuffer completo son 115 KB; a 40 MHz eso son ~23 ms solo de SPI,
// que a 30 fps (33 ms de presupuesto) se comia el fotograma entero.
// Si aparecieran artefactos en el panel, bajar a 40 MHz.
#define BOARD_LCD_SPI_HZ    (40 * 1000 * 1000)   // 80 MHz sin verificar: revertido

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
#define BOARD_SAMPLE_RATE   24000

// ---- Otros ----
#define BOARD_LED           48
#define BOARD_BTN_BOOT      0
#define BOARD_BAT_ADC       1
#define BOARD_CHARGE_DET    41

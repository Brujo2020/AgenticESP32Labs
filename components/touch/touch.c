#include "touch.h"
#include "board_pins.h"
#include "esp_log.h"
#include "esp_check.h"
#include "driver/i2c_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_touch_cst816s.h"

static const char *TAG = "touch";
static i2c_master_bus_handle_t s_bus = NULL;
static esp_lcd_touch_handle_t  s_tp  = NULL;

esp_err_t touch_init(void)
{
    // IDF 6.0: solo existe la API i2c_master, el driver legacy fue eliminado.
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = BOARD_TP_I2C_PORT,
        .sda_io_num = BOARD_TP_SDA,
        .scl_io_num = BOARD_TP_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_cfg, &s_bus), TAG, "bus i2c");

    esp_lcd_panel_io_handle_t io = NULL;
    esp_lcd_panel_io_i2c_config_t io_cfg = ESP_LCD_TOUCH_IO_I2C_CST816S_CONFIG();
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_i2c(s_bus, &io_cfg, &io), TAG, "panel io");

    esp_lcd_touch_config_t cfg = {
        .x_max = BOARD_LCD_H_RES,
        .y_max = BOARD_LCD_V_RES,
        .rst_gpio_num = BOARD_TP_RST,
        .int_gpio_num = BOARD_TP_INT,
        .flags = { .swap_xy = 0, .mirror_x = 1, .mirror_y = 0 },  // igual que el panel
    };
    ESP_RETURN_ON_ERROR(esp_lcd_touch_new_i2c_cst816s(io, &cfg, &s_tp), TAG, "cst816s");
    ESP_LOGI(TAG, "CST816 listo");
    return ESP_OK;
}

bool touch_ready(void) { return s_tp != NULL; }

bool touch_get(int *x, int *y)
{
    if (!s_tp) return false;
    uint16_t px[1], py[1];
    uint8_t n = 0;
    esp_lcd_touch_read_data(s_tp);
    if (!esp_lcd_touch_get_coordinates(s_tp, px, py, NULL, &n, 1) || n == 0) return false;
    if (x) *x = px[0];
    if (y) *y = py[0];
    return true;
}

idf_component_register(
    SRCS "touch.c"
    INCLUDE_DIRS "include"
    REQUIRES board esp_lcd esp_driver_i2c espressif__esp_lcd_touch_cst816s
)

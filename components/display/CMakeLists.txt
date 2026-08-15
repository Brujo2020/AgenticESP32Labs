idf_component_register(
    SRCS "display.c" "font5x7.c"
    INCLUDE_DIRS "include"
    REQUIRES board esp_lcd esp_driver_spi esp_driver_gpio esp_driver_ledc espressif__esp_lcd_gc9a01
)

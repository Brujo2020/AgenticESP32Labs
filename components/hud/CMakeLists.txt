idf_component_register(
    SRCS "hud.c" "ui.c"
    INCLUDE_DIRS "include"
    REQUIRES display audio touch net voice ajustes esp_timer
)

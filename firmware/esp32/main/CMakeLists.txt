idf_component_register(SRCS "main.cpp"
  INCLUDE_DIRS "." "../../common/include"
  REQUIRES esp_driver_i2s esp_wifi esp_event esp_netif esp_timer esp_http_client nvs_flash json lwip)
target_compile_features(${COMPONENT_LIB} PUBLIC cxx_std_17)

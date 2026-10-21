
/* fw_logic.h - pure logic, host-testable (no ESP-IDF headers) */
#ifndef FW_LOGIC_H
#define FW_LOGIC_H
#include <stdint.h>
#include <stddef.h>

typedef enum { FW_BOOT_START, FW_BOOT_LDO_EN, FW_BOOT_PIN_INIT,
               FW_BOOT_CONSOLE, FW_BOOT_SELFTEST, FW_BOOT_RUN } fw_boot_t;

fw_boot_t fw_boot_next(fw_boot_t s);
int fw_debounce_stable(const uint8_t *samples, size_t n);
uint8_t fw_spi_loopback(const uint8_t *tx, uint8_t *rx, size_t n);
uint32_t fw_led_blink_ms(uint32_t period_ms, uint8_t duty_pct);
#endif
